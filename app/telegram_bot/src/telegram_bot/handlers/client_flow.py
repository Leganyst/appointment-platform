from datetime import datetime, timedelta, timezone
import logging

import grpc
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from telegram_bot.keyboards import (
    booking_confirm_keyboard,
    booking_details_keyboard,
    booking_result_keyboard,
    cancel_result_keyboard,
    main_menu_keyboard,
    my_bookings_keyboard,
    provider_keyboard,
    provider_main_menu_keyboard,
    service_search_keyboard,
    services_for_provider_keyboard,
    slots_keyboard,
)
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.identity import find_provider_by_phone, get_profile
from telegram_bot.services.errors import user_friendly_error
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.dto import SlotDTO
from telegram_bot.states import ClientStates, ProviderStates
from telegram_bot.utils.corr import new_corr_id

SERVICE_PAGE_SIZE = 10
PROVIDER_PAGE_SIZE = 10

logger = logging.getLogger(__name__)


def _title_with_id(name: str | None, entity_id: str) -> str:
    short = entity_id[:8]
    return name or f"ID {short}"


def _is_active_booking(status: str) -> bool:
    status_upper = (status or "").upper()
    return status_upper not in {"CANCELLED", "BOOKING_STATUS_CANCELLED"}


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%d.%m %H:%M") if dt else "—"


def format_bookings_split(bookings, slot_map: dict[str, SlotDTO]):
    if not bookings:
        return "Записей нет."
    active = [b for b in bookings if _is_active_booking(b.status)]
    past = [b for b in bookings if not _is_active_booking(b.status)]

    def _status_text(status: str) -> str:
        return {
            "BOOKING_STATUS_PENDING": "Ожидает подтверждения",
            "BOOKING_STATUS_CONFIRMED": "Подтверждена",
            "BOOKING_STATUS_CANCELLED": "Отменена",
        }.get(status, status)

    def _line(b):
        created = _fmt_dt(b.created_at)
        slot = slot_map.get(b.slot_id)
        slot_dt = _fmt_dt(slot.starts_at) if slot else "—"
        return "\n".join(
            [
                f"• {slot_dt} — {_status_text(b.status)}",
                f"  Услуга: {b.service_name or b.service_id}",
                f"  Провайдер: {b.provider_name or b.provider_id}",
                f"  Создано: {created}",
            ]
        )

    parts = []
    if active:
        parts.append("Активные:")
        parts.extend([_line(b) for b in active])
    if past:
        parts.append("Прошедшие/отменённые:")
        parts.extend([_line(b) for b in past])
    return "\n\n".join(parts)


def _truncate(text: str, limit: int = 120) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


async def _safe_edit(message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return False
        raise


async def _ensure_client_context(state: FSMContext, bot, telegram_id: int):
    data = await state.get_data()
    if data.get("client_id"):
        return data
    settings = bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        user = await get_profile(
            clients.identity_stub(),
            telegram_id=telegram_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError:
        return data

    merged = {**data}
    merged.update(
        client_id=user.client_id,
        provider_id=user.provider_id,
        role=user.role_code,
        contact_phone=user.contact_phone,
        display_name=user.display_name,
        username=user.username,
    )
    await state.update_data(**merged)
    return merged


async def _build_slot_map_for_bookings(clients: GrpcClients, settings, bookings) -> dict[str, SlotDTO]:
    if not bookings:
        return {}
    slot_map: dict[str, SlotDTO] = {}
    per_provider = {}
    for b in bookings:
        if b.slot_id in slot_map:
            continue
        per_provider.setdefault(b.provider_id, set()).add(b.slot_id)

    from_dt = datetime.now(timezone.utc) - timedelta(days=180)
    to_dt = datetime.now(timezone.utc) + timedelta(days=365)
    page_size = 500
    for provider_id, slot_ids in per_provider.items():
        page = 1
        remaining = set(slot_ids)
        while remaining:
            slots_page, total = await cal_svc.list_provider_slots(
                clients.calendar_stub(),
                provider_id=provider_id,
                from_dt=from_dt,
                to_dt=to_dt,
                include_bookings=True,
                page=page,
                page_size=page_size,
                metadata=build_metadata(new_corr_id()),
                timeout=settings.grpc_deadline_sec,
            )
            for ps in slots_page:
                slot_map[ps.slot.id] = ps.slot
                remaining.discard(ps.slot.id)
            if len(slots_page) < page_size or len(slot_map) >= len(slot_ids):
                break
            page += 1
    return slot_map

router = Router()


# Главное меню (reply кнопки)
@router.message(F.text == "Поиск услуг")
async def on_search_services(message: Message, state: FSMContext):
    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        services, total = await cal_svc.list_services(
            clients.calendar_stub(),
            page=1,
            page_size=SERVICE_PAGE_SIZE,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await message.answer(user_friendly_error(exc))
        return

    if not services:
        await message.answer("Услуги не найдены", reply_markup=main_menu_keyboard())
        return

    await state.set_state(ClientStates.service_search)
    await state.update_data(
        selected_service_id=None,
        selected_provider_id=None,
        selected_slot_id=None,
        service_page=1,
        service_cache={s.id: s for s in services},
    )
    has_next = total > SERVICE_PAGE_SIZE
    await message.answer("Выберите услугу:", reply_markup=service_search_keyboard(services, 1, False, has_next))


@router.message(F.text == "Мои записи")
async def on_my_bookings(message: Message, state: FSMContext):
    data = await _ensure_client_context(state, message.bot, message.from_user.id)
    client_id = data.get("client_id")
    if not client_id:
        await message.answer("Не нашёл ваш профиль, повторите /start")
        return

    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    corr_id = new_corr_id()
    try:
        logger.info("client_flow.my_bookings: tg=%s client_id=%s corr=%s", message.from_user.id, client_id, corr_id)
        bookings = await cal_svc.list_bookings(
            clients.calendar_stub(),
            client_id=client_id,
            from_dt=datetime.now(timezone.utc) - timedelta(days=30),
            to_dt=datetime.now(timezone.utc) + timedelta(days=60),
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        slot_cache = await _build_slot_map_for_bookings(clients, settings, bookings)
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "client_flow.my_bookings failed: tg=%s client_id=%s corr=%s code=%s details=%s",
            message.from_user.id,
            client_id,
            corr_id,
            exc.code(),
            exc.details(),
        )
        await message.answer(f"Не удалось загрузить записи. Повторите /start или позже. (corr={corr_id})")
        return

    await state.set_state(ClientStates.my_bookings)
    await state.update_data(slot_cache=slot_cache)
    cancellable_ids = {b.id for b in bookings if _is_active_booking(b.status)}
    await message.answer(
        format_bookings_split(bookings, slot_cache),
        reply_markup=my_bookings_keyboard(bookings, cancellable_ids),
    )


def _profile_text(message: Message, data: dict) -> str:
    display_name = data.get("display_name") or message.from_user.full_name
    username = data.get("username") or (message.from_user.username and f"@{message.from_user.username}") or "—"
    role = data.get("role") or "—"
    contact = data.get("contact_phone") or username or "—"
    return (
        f"Профиль\n"
        f"Имя: {display_name}\n"
        f"Username: {username}\n"
        f"Роль: {role}\n"
        f"Контакт: {contact}\n\n"
        "Основные разделы:\n"
        "• Поиск услуг — выбор услуги, провайдера и слота.\n"
        "• Мои записи — активные и прошедшие бронирования.\n"
        "• Профиль — контакт и роль.\n"
        "• Помощь — краткая инструкция."
    )


@router.message(F.text == "Профиль")
async def on_profile(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(ClientStates.profile_help)
    await message.answer(
        _profile_text(message, data),
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "Помощь")
async def on_help(message: Message, state: FSMContext):
    await state.set_state(ClientStates.profile_help)
    await message.answer(
        (
            "Помощь\n"
            "• Поиск услуг — выберите услугу, затем представителя и слот.\n"
            "• Мои записи — смотрите активные и прошедшие бронирования, отменяйте активные.\n"
            "• Профиль — роль и контакт для связи.\n"
            "• Главное меню — вернуться из любого экрана.\n\n"
            "При ошибках бронирования бот покажет причину (конфликт, слот занят)."
        ),
        reply_markup=main_menu_keyboard(),
    )


# Поиск провайдера по телефону
@router.message(F.text == "Найти провайдера по телефону")
async def on_find_provider_phone(message: Message, state: FSMContext):
    await state.set_state(ClientStates.provider_phone_search)
    await message.answer("Введите номер телефона провайдера (как на визитке):", reply_markup=main_menu_keyboard())


@router.message(ClientStates.provider_phone_search)
async def handle_provider_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        provider_user = await find_provider_by_phone(
            clients.identity_stub(),
            phone=phone,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await message.answer(user_friendly_error(exc), reply_markup=main_menu_keyboard())
        return

    if not provider_user or not provider_user.provider_id:
        await message.answer("Провайдер по этому телефону не найден.", reply_markup=main_menu_keyboard())
        return

    await state.update_data(selected_provider_id=provider_user.provider_id)
    corr_id = new_corr_id()
    try:
        provider, services = await cal_svc.list_provider_services(
            clients.calendar_stub(),
            provider_id=provider_user.provider_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await message.answer(user_friendly_error(exc), reply_markup=main_menu_keyboard())
        return

    if not services:
        await message.answer("У провайдера нет доступных услуг.", reply_markup=main_menu_keyboard())
        return

    await state.set_state(ClientStates.service_search)
    await message.answer(
        f"Провайдер найден: {_title_with_id(provider.display_name, provider.id)}. Выберите услугу:",
        reply_markup=services_for_provider_keyboard(provider.id, services),
    )


# Поиск услуги → выбор провайдера
@router.callback_query(ClientStates.service_search, F.data.startswith("service:choose:"))
async def on_service_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, service_id = callback.data.split(":")
    await state.update_data(selected_service_id=service_id, selected_provider_id=None, selected_slot_id=None)
    data = await state.get_data()
    service_cache = data.get("service_cache") or {}
    service = service_cache.get(service_id)
    service_title = service.name if service else service_id
    service_desc = _truncate(service.description) if service and service.description else ""
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        providers, total = await cal_svc.list_providers(
            clients.calendar_stub(),
            service_id=service_id,
            page=1,
            page_size=PROVIDER_PAGE_SIZE,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.set_state(ClientStates.service_search)
    if not providers:
        await callback.message.edit_text(
            "Провайдеры по услуге не найдены.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        has_next = total > PROVIDER_PAGE_SIZE
        await state.update_data(provider_page=1, provider_cache={p.id: p for p in providers})
        await callback.message.edit_text(
            (
                f"Услуга: {service_title}\n"
                f"{service_desc}\n\n"
                "Выберите представителя (имя — описание):\n" +
                "\n".join([f"• {_title_with_id(p.display_name, p.id)} — {_truncate(p.description) or 'нет описания'}" for p in providers])
            ),
            reply_markup=provider_keyboard(providers, 1, False, has_next),
        )
    await callback.answer()


@router.callback_query(ClientStates.service_search, F.data.startswith("service:page:"))
async def on_service_page(callback: CallbackQuery, state: FSMContext):
    _, _, page_str = callback.data.split(":")
    page = max(1, int(page_str))
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        services, total = await cal_svc.list_services(
            clients.calendar_stub(),
            page=page,
            page_size=SERVICE_PAGE_SIZE,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    if not services:
        await callback.message.edit_text("Услуги не найдены", reply_markup=main_menu_keyboard())
        await callback.answer()
        return

    await state.update_data(service_page=page, service_cache={s.id: s for s in services})
    has_prev = page > 1
    has_next = total > page * SERVICE_PAGE_SIZE
    await callback.message.edit_text(
        "Выберите услугу:", reply_markup=service_search_keyboard(services, page, has_prev, has_next)
    )
    await callback.answer()


@router.callback_query(ClientStates.service_search, F.data.startswith("provider:page:"))
async def on_provider_page(callback: CallbackQuery, state: FSMContext):
    try:
        _, _, page_str = callback.data.split(":")
        page = max(1, int(page_str))
    except ValueError:
        await callback.answer("Неверный формат страницы")
        return

    data = await state.get_data()
    service_id = data.get("selected_service_id")
    if not service_id:
        await callback.answer("Услуга не выбрана, начните сначала /start", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        providers, total = await cal_svc.list_providers(
            clients.calendar_stub(),
            service_id=service_id,
            page=page,
            page_size=PROVIDER_PAGE_SIZE,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.update_data(provider_page=page, selected_service_id=service_id, provider_cache={p.id: p for p in providers})
    has_prev = page > 1
    has_next = total > page * PROVIDER_PAGE_SIZE
    data = await state.get_data()
    service_cache = data.get("service_cache") or {}
    service = service_cache.get(service_id)
    service_title = service.name if service else service_id
    await callback.message.edit_text(
        (
            f"Услуга: {service_title}\n"
            f"Страница {page}. Выберите представителя (имя — описание):\n" +
            "\n".join([f"• {_title_with_id(p.display_name, p.id)} — {_truncate(p.description) or 'нет описания'}" for p in providers])
        ),
        reply_markup=provider_keyboard(providers, page, has_prev, has_next) if providers else main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("provider:choose:"))
async def on_provider_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, provider_id = callback.data.split(":")
    data = await state.get_data()
    service_id = data.get("selected_service_id")
    if not service_id:
        await callback.answer("Услуга не выбрана, начните сначала /start", show_alert=True)
        return
    await state.update_data(selected_service_id=service_id, selected_provider_id=provider_id)
    provider_cache = data.get("provider_cache") or {}
    service_cache = data.get("service_cache") or {}
    provider = provider_cache.get(provider_id)
    service = service_cache.get(service_id)
    provider_title = provider.display_name if provider else provider_id
    provider_desc = _truncate(provider.description) if provider and provider.description else ""
    service_title = service.name if service else service_id

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        slots = await cal_svc.find_free_slots(
            clients.calendar_stub(),
            provider_id=provider_id,
            service_id=service_id,
            from_dt=datetime.now(timezone.utc),
            days=cal_svc.DEFAULT_SLOTS_WINDOW_DAYS,
            limit=10,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    if not slots:
        try:
            providers, total = await cal_svc.list_providers(
                clients.calendar_stub(),
                service_id=service_id,
                page=1,
                page_size=PROVIDER_PAGE_SIZE,
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
        except grpc.aio.AioRpcError as exc:
            await callback.message.edit_text(user_friendly_error(exc))
            await callback.answer()
            return

        await state.update_data(provider_cache={p.id: p for p in providers})
        provider_lines = "\n".join([f"• {_title_with_id(p.display_name, p.id)} — {_truncate(p.description) or 'нет описания'}" for p in providers])
        has_next = total > PROVIDER_PAGE_SIZE
        await callback.message.edit_text(
            (
                "Свободных слотов нет, попробуйте позже.\n"
                "Выберите другого представителя:\n" + provider_lines
            ),
            reply_markup=provider_keyboard(providers, 1, False, has_next) if providers else main_menu_keyboard(),
        )
        await callback.answer()
        return

    await state.set_state(ClientStates.slots_view)
    await state.update_data(slot_times={s.id: s.starts_at.isoformat() for s in slots})
    await _safe_edit(
        callback.message,
        (
            f"Услуга: {service_title}\n"
            f"Провайдер: {provider_title}\n"
            f"{provider_desc}\n\n"
            "Доступные слоты:"
        ),
        reply_markup=slots_keyboard(service_id, provider_id, slots),
    )
    await callback.answer()


@router.callback_query(ClientStates.service_search, F.data.startswith("provider_service:choose:"))
async def on_provider_service_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, service_id = callback.data.split(":")
    data = await state.get_data()
    provider_id = data.get("selected_provider_id")
    if not provider_id:
        await callback.answer("Провайдер не выбран, начните сначала /start", show_alert=True)
        return

    await state.update_data(selected_service_id=service_id, selected_provider_id=provider_id)

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        slots = await cal_svc.find_free_slots(
            clients.calendar_stub(),
            provider_id=provider_id,
            service_id=service_id,
            from_dt=datetime.now(timezone.utc),
            days=cal_svc.DEFAULT_SLOTS_WINDOW_DAYS,
            limit=10,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    if not slots:
        await callback.message.edit_text(
            "Свободных слотов нет, попробуйте позже.",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return

    await state.set_state(ClientStates.slots_view)
    await state.update_data(slot_times={s.id: s.starts_at.isoformat() for s in slots})
    await _safe_edit(
        callback.message,
        f"Провайдер выбран: {provider_id}. Доступные слоты:",
        reply_markup=slots_keyboard(service_id, provider_id, slots),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("provider:back:"))
async def on_provider_back(callback: CallbackQuery, state: FSMContext):
    _, _, service_id = callback.data.split(":")
    await state.set_state(ClientStates.service_search)
    data = await state.get_data()
    page = data.get("provider_page", 1)
    service_cache = data.get("service_cache") or {}
    service = service_cache.get(service_id)
    service_title = service.name if service else service_id
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        providers, total = await cal_svc.list_providers(
            clients.calendar_stub(),
            service_id=service_id,
            page=page,
            page_size=PROVIDER_PAGE_SIZE,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.update_data(provider_cache={p.id: p for p in providers})
    has_prev = page > 1
    has_next = total > page * PROVIDER_PAGE_SIZE
    await callback.message.edit_text(
        (
            f"Услуга: {service_title}\n"
            f"Страница {page}. Выберите представителя (имя — описание):\n" +
            "\n".join([f"• {_title_with_id(p.display_name, p.id)} — {_truncate(p.description) or 'нет описания'}" for p in providers])
        ),
        reply_markup=provider_keyboard(providers, page, has_prev, has_next) if providers else main_menu_keyboard(),
    )
    await callback.answer()


# Слоты → подтверждение
@router.callback_query(ClientStates.slots_view, F.data.startswith("slot:choose:"))
async def on_slot_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, slot_id = callback.data.split(":")
    data = await state.get_data()
    service_id = data.get("selected_service_id")
    provider_id = data.get("selected_provider_id")
    slot_times = data.get("slot_times") or {}
    slot_iso = slot_times.get(slot_id)
    slot_dt = datetime.fromisoformat(slot_iso) if slot_iso else None
    if not service_id or not provider_id:
        await callback.answer("Контекст потерян, начните заново /start", show_alert=True)
        return

    service_cache = data.get("service_cache") or {}
    provider_cache = data.get("provider_cache") or {}
    service = service_cache.get(service_id)
    provider = provider_cache.get(provider_id)
    service_title = service.name if service else service_id
    provider_title = provider.display_name if provider else provider_id
    slot_text = _fmt_dt(slot_dt)

    await state.update_data(selected_slot_id=slot_id)
    await state.set_state(ClientStates.booking_confirm)
    await callback.message.edit_text(
        (
            f"Запись: {service_title} у {provider_title}\n"
            f"Время: {slot_text}\n"
            "Подтвердить?"
        ),
        reply_markup=booking_confirm_keyboard(slot_id),
    )
    await callback.answer()


@router.callback_query(ClientStates.booking_confirm, F.data.startswith("booking:cancel:"))
async def on_booking_cancel(callback: CallbackQuery, state: FSMContext):
    _, _, slot_id = callback.data.split(":")
    data = await state.get_data()
    service_id = data.get("selected_service_id")
    provider_id = data.get("selected_provider_id")
    if not service_id or not provider_id:
        await callback.answer("Контекст потерян, начните заново /start", show_alert=True)
        return
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        slots = await cal_svc.find_free_slots(
            clients.calendar_stub(),
            provider_id=provider_id,
            service_id=service_id,
            from_dt=datetime.now(timezone.utc),
            days=cal_svc.DEFAULT_SLOTS_WINDOW_DAYS,
            limit=10,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.update_data(slot_times={s.id: s.starts_at.isoformat() for s in slots})
    await state.set_state(ClientStates.slots_view)
    await callback.message.edit_text(
        "Выберите другой слот:", reply_markup=slots_keyboard(service_id, provider_id, slots)
    )
    await callback.answer()


@router.callback_query(ClientStates.booking_confirm, F.data.startswith("booking:confirm:"))
async def on_booking_confirm(callback: CallbackQuery, state: FSMContext):
    _, _, slot_id = callback.data.split(":")
    data = await state.get_data()
    service_id = data.get("selected_service_id")
    provider_id = data.get("selected_provider_id")
    client_id = data.get("client_id")
    slot_times = data.get("slot_times") or {}
    slot_iso = slot_times.get(slot_id)
    slot_dt = datetime.fromisoformat(slot_iso) if slot_iso else None
    if not client_id or not service_id or not provider_id:
        await callback.answer("Контекст потерян, начните заново /start", show_alert=True)
        return

    service_cache = data.get("service_cache") or {}
    provider_cache = data.get("provider_cache") or {}
    service = service_cache.get(service_id)
    provider = provider_cache.get(provider_id)
    service_title = (service.name if service else None) or service_id
    provider_title = (provider.display_name if provider else None) or provider_id
    slot_text = _fmt_dt(slot_dt)

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    try:
        available, reason = await cal_svc.check_availability(
            stub,
            client_id=client_id,
            slot_id=slot_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        if not available:
            await callback.message.edit_text(
                f"Слот недоступен: {reason}",
                reply_markup=slots_keyboard(service_id, provider_id, []),
            )
            await callback.answer()
            return

        booking = await cal_svc.create_booking(
            stub,
            client_id=client_id,
            slot_id=slot_id,
            comment=None,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        service_title = booking.service_name or service_title
        provider_title = booking.provider_name or provider_title
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.set_state(ClientStates.booking_result)
    await callback.message.edit_text(
        (
            "Запись создана!\n"
            f"Услуга: {service_title}\n"
            f"Провайдер: {provider_title}\n"
            f"Время: {slot_text}\n"
            f"Статус: {booking.status}"
        ),
        reply_markup=booking_result_keyboard(success=True),
    )
    await callback.answer("Успешно")


# Бронирование результат → переходы
@router.callback_query(ClientStates.booking_result, F.data == "bookings:mine")
async def on_booking_result_to_my(callback: CallbackQuery, state: FSMContext):
    data = await _ensure_client_context(state, callback.message.bot, callback.from_user.id)
    client_id = data.get("client_id")
    if not client_id:
        await callback.answer("Не нашёл ваш профиль, повторите /start", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        logger.info("client_flow.bookings_inline(from_result): tg=%s client_id=%s corr=%s", callback.from_user.id, client_id, corr_id)
        bookings = await cal_svc.list_bookings(
            clients.calendar_stub(),
            client_id=client_id,
            from_dt=datetime.now(timezone.utc) - timedelta(days=30),
            to_dt=datetime.now(timezone.utc) + timedelta(days=60),
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        slot_cache = await _build_slot_map_for_bookings(clients, settings, bookings)
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "client_flow.bookings_inline(from_result) failed: tg=%s client_id=%s corr=%s code=%s details=%s",
            callback.from_user.id,
            client_id,
            corr_id,
            exc.code(),
            exc.details(),
        )
        await callback.message.edit_text(f"Не удалось загрузить записи. Повторите /start или позже. (corr={corr_id})")
        await callback.answer()
        return

    await state.set_state(ClientStates.my_bookings)
    await state.update_data(slot_cache=slot_cache)
    cancellable_ids = {b.id for b in bookings if _is_active_booking(b.status)}
    await callback.message.edit_text(
        format_bookings_split(bookings, slot_cache),
        reply_markup=my_bookings_keyboard(bookings, cancellable_ids),
    )
    await callback.answer()


# Мои записи
@router.callback_query(F.data == "bookings:mine")
async def on_bookings_inline(callback: CallbackQuery, state: FSMContext):
    data = await _ensure_client_context(state, callback.message.bot, callback.from_user.id)
    client_id = data.get("client_id")
    if not client_id:
        await callback.answer("Не нашёл ваш профиль, повторите /start", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        logger.info("client_flow.bookings_inline: tg=%s client_id=%s corr=%s", callback.from_user.id, client_id, corr_id)
        bookings = await cal_svc.list_bookings(
            clients.calendar_stub(),
            client_id=client_id,
            from_dt=datetime.now(timezone.utc) - timedelta(days=30),
            to_dt=datetime.now(timezone.utc) + timedelta(days=60),
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        slot_cache = await _build_slot_map_for_bookings(clients, settings, bookings)
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "client_flow.bookings_inline failed: tg=%s client_id=%s corr=%s code=%s details=%s",
            callback.from_user.id,
            client_id,
            corr_id,
            exc.code(),
            exc.details(),
        )
        await callback.message.edit_text(f"Не удалось загрузить записи. Повторите /start или позже. (corr={corr_id})")
        await callback.answer()
        return

    await state.set_state(ClientStates.my_bookings)
    await state.update_data(slot_cache=slot_cache)
    cancellable_ids = {b.id for b in bookings if _is_active_booking(b.status)}
    await callback.message.edit_text(
        format_bookings_split(bookings, slot_cache),
        reply_markup=my_bookings_keyboard(bookings, cancellable_ids),
    )
    await callback.answer()


@router.callback_query(ClientStates.my_bookings, F.data.startswith("booking:detail:"))
async def on_booking_detail(callback: CallbackQuery, state: FSMContext):
    _, _, booking_id = callback.data.split(":")
    data = await state.get_data()
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        booking = await cal_svc.get_booking(
            clients.calendar_stub(),
            booking_id=booking_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        slot_cache = data.get("slot_cache") or {}
        if booking.slot_id not in slot_cache:
            slot_cache = await _build_slot_map_for_bookings(clients, settings, [booking])
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.set_state(ClientStates.booking_details)
    await callback.message.edit_text(
        (
            f"Услуга: {booking.service_name or booking.service_id}\n"
            f"Провайдер: {booking.provider_name or booking.provider_id}\n"
            f"Статус: {booking.status}\n"
            f"Приём: {_fmt_dt((slot_cache.get(booking.slot_id) or {}).starts_at if slot_cache.get(booking.slot_id) else None)}\n"
            f"Создано: {_fmt_dt(booking.created_at)}\n"
            f"Отменено: {_fmt_dt(booking.cancelled_at)}\n"
            f"Комментарий: {booking.comment or '—'}"
        ),
        reply_markup=booking_details_keyboard(booking.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("booking:cancel_active:"))
async def on_booking_cancel_active(callback: CallbackQuery, state: FSMContext):
    _, _, booking_id = callback.data.split(":")
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        booking = await cal_svc.cancel_booking(
            clients.calendar_stub(),
            booking_id=booking_id,
            reason="client_request",
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.set_state(ClientStates.cancel_result)
    await callback.message.edit_text(
        f"Бронирование отменено. Статус: {booking.status}",
        reply_markup=cancel_result_keyboard(),
    )
    await callback.answer()


@router.callback_query(ClientStates.cancel_result, F.data == "menu:main")
async def on_cancel_to_menu(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    role = data.get("role")
    if role == "provider":
        await state.set_state(ProviderStates.main_menu)
        reply_markup = provider_main_menu_keyboard()
    else:
        await state.set_state(ClientStates.main_menu)
        reply_markup = main_menu_keyboard()

    await callback.message.answer("Главное меню:", reply_markup=reply_markup)
    await callback.answer()


# Профиль/помощь через inline не дублируем, т.к. покрыто текстом меню


@router.callback_query(F.data == "menu:main")
async def on_menu_any(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    role = data.get("role")
    if role == "provider":
        await state.set_state(ProviderStates.main_menu)
        reply_markup = provider_main_menu_keyboard()
    else:
        await state.set_state(ClientStates.main_menu)
        reply_markup = main_menu_keyboard()

    await callback.message.answer("Главное меню:", reply_markup=reply_markup)
    await callback.answer()
