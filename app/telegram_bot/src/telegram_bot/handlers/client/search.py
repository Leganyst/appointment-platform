from datetime import datetime, timezone
import logging

import grpc
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telegram_bot.keyboards import (
    main_menu_inline_keyboard,
    main_menu_only_inline_keyboard,
    main_menu_keyboard,
    provider_keyboard,
    service_search_keyboard,
    services_for_provider_keyboard,
    slots_keyboard,
)
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.identity import find_provider_by_phone
from telegram_bot.services.errors import user_friendly_error
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.states import ClientStates
from telegram_bot.utils.corr import new_corr_id
from .utils import cache_slot_context, filter_available_slots, safe_edit, slot_is_bookable, slot_is_future, title_with_id, truncate

SERVICE_PAGE_SIZE = 10
PROVIDER_PAGE_SIZE = 10

router = Router()
logger = logging.getLogger(__name__)


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


@router.message(F.text == "Найти провайдера по телефону")
async def on_find_provider_phone(message: Message, state: FSMContext):
    await state.set_state(ClientStates.provider_phone_search)
    await message.answer("Введите телефон или @username провайдера (как на визитке/в профиле):", reply_markup=main_menu_keyboard())


@router.message(ClientStates.provider_phone_search)
async def handle_provider_phone(message: Message, state: FSMContext):
    from telegram_bot.utils.contacts import parse_contact

    raw = (message.text or "").strip()
    phone, username, err = parse_contact(raw)
    if err:
        await message.answer(err, reply_markup=main_menu_keyboard())
        return
    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        provider_user = await find_provider_by_phone(
            clients.identity_stub(),
            phone=phone or ("@" + username if username else raw),
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
        f"Провайдер найден: {title_with_id(provider.display_name, provider.id)}. Выберите услугу:",
        reply_markup=services_for_provider_keyboard(provider.id, services),
    )


@router.callback_query(ClientStates.service_search, F.data.startswith("service:choose:"))
async def on_service_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, service_id = callback.data.split(":")
    await state.update_data(selected_service_id=service_id, selected_provider_id=None, selected_slot_id=None)
    data = await state.get_data()
    service_cache = data.get("service_cache") or {}
    service = service_cache.get(service_id)
    service_title = service.name if service else service_id
    service_desc = truncate(service.description) if service and service.description else ""
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
            reply_markup=main_menu_only_inline_keyboard(),
        )
    else:
        has_next = total > PROVIDER_PAGE_SIZE
        await state.update_data(provider_page=1, provider_cache={p.id: p for p in providers})
        await callback.message.edit_text(
            (
                f"Услуга: {service_title}\n"
                f"{service_desc}\n\n"
                "Выберите представителя (имя — описание):\n"
                + "\n".join(
                    [
                        f"• {title_with_id(p.display_name, p.id)} — {truncate(p.description) or 'нет описания'}"
                        for p in providers
                    ]
                )
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
        await callback.message.edit_text("Услуги не найдены", reply_markup=main_menu_inline_keyboard())
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
    if not providers:
        await callback.message.edit_text(
            f"Услуга: {service_title}\nПровайдеры по услуге не найдены.",
            reply_markup=main_menu_only_inline_keyboard(),
        )
    else:
        await callback.message.edit_text(
            (
                f"Услуга: {service_title}\n"
                f"Страница {page}. Выберите представителя (имя — описание):\n"
                + "\n".join(
                    [
                        f"• {title_with_id(p.display_name, p.id)} — {truncate(p.description) or 'нет описания'}"
                        for p in providers
                    ]
                )
            ),
            reply_markup=provider_keyboard(providers, page, has_prev, has_next),
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
    provider_desc = truncate(provider.description) if provider and provider.description else ""
    service_title = service.name if service else service_id

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        now = datetime.now(timezone.utc)
        slots = await cal_svc.find_free_slots(
            clients.calendar_stub(),
            provider_id=provider_id,
            service_id=service_id,
            from_dt=now,
            days=cal_svc.DEFAULT_SLOTS_WINDOW_DAYS,
            limit=10,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        logger.info(
            "client.search: slots fetched service=%s provider=%s count=%s sample=%s",
            service_id,
            provider_id,
            len(slots),
            [
                {
                    "id": s.id[:8],
                    "status": s.status,
                    "start": (s.starts_at.isoformat() if s.starts_at else None),
                }
                for s in slots[:5]
            ],
        )
        before = len(slots)
        slots = [s for s in slots if slot_is_bookable(s)]
        slots = await filter_available_slots(callback.message.bot, clients, settings, provider_id, slots)
        if before != len(slots):
            logger.info(
                "client.search: filtered past slots service=%s provider=%s removed=%s left=%s corr=%s",
                service_id,
                provider_id,
                before - len(slots),
                len(slots),
                corr_id,
            )
        cache_slot_context(callback.message.bot, slots, provider_id, service_id)
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
        provider_lines = "\n".join(
            [f"• {title_with_id(p.display_name, p.id)} — {truncate(p.description) or 'нет описания'}" for p in providers]
        )
        has_next = total > PROVIDER_PAGE_SIZE
        try:
            await callback.message.edit_text(
                (
                    "Свободных слотов нет, попробуйте позже.\n"
                    "Выберите другого представителя:\n" + provider_lines
                ),
                reply_markup=provider_keyboard(providers, 1, False, has_next) if providers else main_menu_inline_keyboard(),
            )
        except Exception:
            pass  # Сообщение уже имеет такой же контент
        await callback.answer()
        return

    await state.set_state(ClientStates.slots_view)
    await state.update_data(slot_times={s.id: s.starts_at.isoformat() for s in slots})
    await safe_edit(
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
        now = datetime.now(timezone.utc)
        slots = await cal_svc.find_free_slots(
            clients.calendar_stub(),
            provider_id=provider_id,
            service_id=service_id,
            from_dt=now,
            days=cal_svc.DEFAULT_SLOTS_WINDOW_DAYS,
            limit=10,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        logger.info(
            "client.search(phone): slots fetched service=%s provider=%s count=%s sample=%s",
            service_id,
            provider_id,
            len(slots),
            [
                {
                    "id": s.id[:8],
                    "status": s.status,
                    "start": (s.starts_at.isoformat() if s.starts_at else None),
                }
                for s in slots[:5]
            ],
        )
        before = len(slots)
        slots = [s for s in slots if slot_is_bookable(s)]
        slots = await filter_available_slots(callback.message.bot, clients, settings, provider_id, slots)
        if before != len(slots):
            logger.info(
                "client.search: filtered past slots by phone service=%s provider=%s removed=%s left=%s corr=%s",
                service_id,
                provider_id,
                before - len(slots),
                len(slots),
                corr_id,
            )
        cache_slot_context(callback.message.bot, slots, provider_id, service_id)
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    if not slots:
        await callback.message.edit_text(
            "Свободных слотов нет, попробуйте позже.",
            reply_markup=main_menu_only_inline_keyboard(),
        )
        logger.info("client.search: no slots service=%s provider=%s corr=%s", service_id, provider_id, corr_id)
        await callback.answer()
        return

    await state.set_state(ClientStates.slots_view)
    await state.update_data(slot_times={s.id: s.starts_at.isoformat() for s in slots})
    await safe_edit(
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
            f"Страница {page}. Выберите представителя (имя — описание):\n"
            + "\n".join(
                [
                    f"• {title_with_id(p.display_name, p.id)} — {truncate(p.description) or 'нет описания'}"
                    for p in providers
                ]
            )
        ),
        reply_markup=provider_keyboard(providers, page, has_prev, has_next) if providers else main_menu_only_inline_keyboard(),
    )
    await callback.answer()
