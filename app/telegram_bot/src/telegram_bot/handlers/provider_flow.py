from datetime import datetime, timedelta, timezone
import logging
import re

import grpc
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telegram_bot.keyboards import (
    provider_add_slot_confirm,
    provider_bookings_keyboard,
    provider_main_menu_keyboard,
    provider_schedule_keyboard,
    provider_service_select_keyboard,
    provider_slots_actions,
)
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.errors import user_friendly_error
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.states import ProviderStates
from telegram_bot.utils.corr import new_corr_id

router = Router()
logger = logging.getLogger(__name__)

TZ_ALIASES = {
    "msk": 180,
    "мск": 180,
    "moscow": 180,
}

BOOKING_STATUS_MAP = {
    "BOOKING_STATUS_PENDING": "Ожидает подтверждения",
    "BOOKING_STATUS_CONFIRMED": "Подтверждена",
    "BOOKING_STATUS_CANCELLED": "Отменена",
}


def _is_active_booking(status: str) -> bool:
    status_upper = (status or "").upper()
    return status_upper not in {"CANCELLED", "BOOKING_STATUS_CANCELLED"}


def _fmt_offset(offset_min: int) -> str:
    sign = "+" if offset_min >= 0 else "-"
    minutes = abs(offset_min)
    hours = minutes // 60
    mins = minutes % 60
    return f"UTC{sign}{hours:02d}:{mins:02d}"


def _parse_date_input(text: str):
    """Parse date from several common formats. If year is missing, assume current year and roll to next year if past."""
    candidates = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m", "%d/%m", "%d-%m"]
    clean = text.strip()
    today = datetime.utcnow().date()
    for fmt in candidates:
        try:
            dt = datetime.strptime(clean, fmt).date()
            if "%Y" not in fmt:
                dt = dt.replace(year=today.year)
                if dt < today:
                    dt = dt.replace(year=today.year + 1)
            return dt
        except ValueError:
            continue
    return None


def _parse_time_input(text: str):
    """Parse time from flexible inputs like 10, 10:00, 10.00, 10-30, 1030."""
    clean = text.strip().replace(".", ":").replace("-", ":").replace(" ", ":")
    # If only digits like 930 or 0930 -> insert colon
    if re.fullmatch(r"\d{3,4}", clean):
        clean = f"{clean[:-2]}:{clean[-2:]}"
    # If only hour
    if re.fullmatch(r"\d{1,2}", clean):
        clean = f"{clean}:00"
    try:
        return datetime.strptime(clean, "%H:%M").time()
    except ValueError:
        return None


def _parse_tz_offset(text: str):
    if not text:
        return None
    val = text.strip().lower()
    if val in TZ_ALIASES:
        return TZ_ALIASES[val]
    m = re.fullmatch(r"([+-]?)(\d{1,2})(?::?(\d{2}))?", val)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    hours = int(m.group(2))
    mins = int(m.group(3) or 0)
    return sign * (hours * 60 + mins)


def _parse_time_with_offset(text: str, default_offset_min: int = 0):
    """Parse time and optional offset. Supports '10:00 +3', '10:00+03:00', '1030-5', '10 msk'."""
    raw = (text or "").strip().lower()
    time_part = raw
    offset_part = None

    # Check split by space
    tokens = raw.split()
    if len(tokens) >= 2:
        time_part = " ".join(tokens[:-1])
        offset_part = tokens[-1]

    # Inline offset like 10:00+3 or 10:00-0530
    if offset_part is None:
        m = re.match(r"(.+?)([+-]\d{1,2}(?::?\d{2})?)$", raw)
        if m:
            time_part = m.group(1).strip()
            offset_part = m.group(2)

    offset_min = _parse_tz_offset(offset_part) if offset_part else None
    time_obj = _parse_time_input(time_part)

    if time_obj and offset_min is None:
        offset_min = default_offset_min

    return time_obj, offset_min


def _fmt_slots(slots, tz_offset_min: int = 180):
    if not slots:
        return "Слотов нет."
    tzinfo_local = timezone(timedelta(minutes=tz_offset_min))
    status_map = {
        "SLOT_STATUS_FREE": "свободно",
        "SLOT_STATUS_BOOKED": "занято",
        "SLOT_STATUS_CANCELED": "отменено",
    }
    booking_map = {
        "BOOKING_STATUS_CONFIRMED": "подтверждена",
        "BOOKING_STATUS_PENDING": "ожидает",
        "BOOKING_STATUS_CANCELED": "отменена",
    }
    lines = []
    for ps in slots:
        s = ps.slot
        start_dt = s.starts_at
        if start_dt and start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        dt_local = start_dt.astimezone(tzinfo_local) if start_dt else None
        dt_label = dt_local.strftime("%d.%m %H:%M") if dt_local else ""
        slot_status = status_map.get(s.status, "")
        booking_note = ""
        if ps.booking:
            booking_note = booking_map.get(ps.booking.status, "забронировано")
        line = f"• {dt_label} — {booking_note or slot_status or 'свободно'}"
        lines.append(line)
    return "\n".join(lines)


async def _clear_prev_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    prev_id = data.get("last_prompt_message_id")
    if prev_id and prev_id != message.message_id:
        try:
            await message.bot.delete_message(message.chat.id, prev_id)
        except Exception:
            pass


async def _remember_prompt(message: Message, state: FSMContext):
    await state.update_data(last_prompt_message_id=message.message_id)


def _fmt_bookings(bookings, slot_map: dict | None = None):
    if not bookings:
        return "Записей нет."
    parts = []
    for b in bookings:
        slot = (slot_map or {}).get(b.slot_id)
        when = slot.starts_at.strftime("%d.%m %H:%M") if slot else "—"
        created = b.created_at.strftime("%d.%m %H:%M") if b.created_at else "—"
        status_text = BOOKING_STATUS_MAP.get(b.status, b.status)
        parts.append(
            "\n".join(
                [
                    f"• {when} — {status_text}",
                    f"  Услуга: {b.service_name or b.service_id}",
                    f"  Создано: {created}",
                    f"  Бронь: {b.id[:8]}",
                ]
            )
        )
    return "\n\n".join(parts)


async def _show_schedule(message: Message, state: FSMContext):
    data = await state.get_data()
    provider_id = data.get("provider_id")
    tz_offset_min = data.get("tz_offset_min", 180)
    if not provider_id:
        await message.answer("Нет provider_id, выберите роль представителя /start")
        return
    logger.info("provider_flow.show_schedule: user=%s provider_id=%s", message.from_user.id, provider_id)

    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    try:
        now = datetime.now(timezone.utc)
        logger.info(
            "provider:list_provider_slots tg=%s provider_id=%s from=%s to=%s corr=%s",
            message.from_user.id,
            provider_id,
            now.isoformat(),
            (now + timedelta(days=30)).isoformat(),
            corr_id,
        )
        slots, _ = await cal_svc.list_provider_slots(
            stub,
            provider_id=provider_id,
            from_dt=now,
            to_dt=now + timedelta(days=30),
            include_bookings=True,
            page=1,
            page_size=50,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await message.answer(user_friendly_error(exc))
        return

    await state.set_state(ProviderStates.schedule_dashboard)
    await message.answer(
        _fmt_slots(slots, tz_offset_min),
        reply_markup=provider_schedule_keyboard(page=1, has_prev=False, has_next=False),
    )
    if slots:
        for ps in slots[:5]:
            slot = ps.slot
            start_dt = slot.starts_at
            if start_dt and start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            tzinfo_local = timezone(timedelta(minutes=tz_offset_min))
            dt_local = start_dt.astimezone(tzinfo_local) if start_dt else None
            pretty = dt_local.strftime('%d.%m %H:%M') if dt_local else ''
            await message.answer(
                f"Слот: {pretty}",
                reply_markup=provider_slots_actions(slot.id),
            )


@router.message(F.text == "Управление расписанием")
async def schedule_dashboard(message: Message, state: FSMContext):
    await _show_schedule(message, state)


@router.callback_query(F.data.in_({"provider:slot:refresh", "provider:slot:list"}))
async def refresh_schedule(callback: CallbackQuery, state: FSMContext):
    await _show_schedule(callback.message, state)
    await callback.answer()


@router.callback_query(ProviderStates.booking_list, F.data.startswith("provider:booking:cancel:"))
async def provider_cancel_booking(callback: CallbackQuery, state: FSMContext):
    _, _, _, booking_id = callback.data.split(":")
    data = await state.get_data()
    provider_id = data.get("provider_id")
    if not provider_id:
        await callback.answer("Нет provider_id, выберите роль представителя /start", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        await cal_svc.cancel_booking(
            clients.calendar_stub(),
            booking_id=booking_id,
            reason="provider_cancel",
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "provider:cancel_booking failed tg=%s provider_id=%s booking_id=%s corr=%s code=%s details=%s",
            callback.from_user.id,
            provider_id,
            booking_id,
            corr_id,
            exc.code(),
            exc.details(),
        )
        await callback.answer("Не удалось отменить запись", show_alert=True)
        return

    await _show_provider_bookings(callback.message, state, as_edit=True)
    await callback.answer("Отменено")


@router.message(F.text == "Мои записи (провайдер)")
async def provider_bookings(message: Message, state: FSMContext):
    await _show_provider_bookings(message, state, as_edit=False)


async def _fetch_slot_map_for_provider(clients: GrpcClients, settings, provider_id: str, slot_ids: set[str]):
    if not slot_ids:
        return {}
    slot_map = {}
    page = 1
    page_size = 200
    from_dt = datetime.now(timezone.utc) - timedelta(days=180)
    to_dt = datetime.now(timezone.utc) + timedelta(days=365)
    while True:
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
        if len(slots_page) < page_size or len(slot_map) >= len(slot_ids):
            break
        page += 1
    return slot_map


async def _show_provider_bookings(message: Message, state: FSMContext, as_edit: bool):
    data = await state.get_data()
    provider_id = data.get("provider_id")
    if not provider_id:
        await message.answer("Нет provider_id, выберите роль представителя /start")
        return

    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        bookings = await cal_svc.list_provider_bookings(
            clients.calendar_stub(),
            provider_id=provider_id,
            from_dt=datetime.now(timezone.utc) - timedelta(days=30),
            to_dt=datetime.now(timezone.utc) + timedelta(days=60),
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "provider:list_provider_bookings failed tg=%s provider_id=%s corr=%s code=%s details=%s",
            message.from_user.id,
            provider_id,
            corr_id,
            exc.code(),
            exc.details(),
        )
        await message.answer(
            "Не удалось показать записи. Обновите роль через /start или повторите позже."
        )
        return

    slot_ids = {b.slot_id for b in bookings}
    slot_map = await _fetch_slot_map_for_provider(clients, settings, provider_id, slot_ids)
    cancellable_ids = {b.id for b in bookings if _is_active_booking(b.status)}

    text = _fmt_bookings(bookings, slot_map)
    markup = provider_bookings_keyboard(bookings, cancellable_ids)
    await state.set_state(ProviderStates.booking_list)
    if as_edit:
        try:
            await message.edit_text(text, reply_markup=markup)
        except Exception:
            await message.answer(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("provider:booking:confirm:"))
async def provider_confirm_booking(callback: CallbackQuery, state: FSMContext):
    _, _, _, booking_id = callback.data.split(":")
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        booking = await cal_svc.confirm_booking(
            clients.calendar_stub(),
            booking_id=booking_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await callback.message.edit_text(f"Бронь подтверждена. Статус: {booking.status}")
    await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
    await callback.answer()


@router.message(F.text == "Профиль (провайдер)")
async def provider_profile(message: Message, state: FSMContext):
    await state.set_state(ProviderStates.profile_help)
    data = await state.get_data()
    provider_id = data.get("provider_id") or "—"
    role = data.get("role") or "provider"
    contact = data.get("contact_phone") or "—"
    text = (
        "=== Профиль представителя ===\n"
        f"Имя в TG: {message.from_user.full_name}\n"
        f"Роль: {role}\n"
        f"Provider ID: {provider_id}\n"
        f"Контакт: {contact}\n"
        "\n"
        "Доступные разделы:\n"
        "- Управление расписанием\n"
        "- Мои записи\n"
        "- Помощь\n"
        "\n"
        "Сменить роль: /start → Выбор роли."
    )
    await message.answer(text, reply_markup=provider_main_menu_keyboard())


@router.callback_query(F.data.startswith("provider:slot:add"))
async def start_add_slot(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    provider_id = data.get("provider_id")
    if not provider_id:
        await callback.message.edit_text("Нет provider_id, выберите роль представителя /start")
        await callback.answer()
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    logger.info(
        "provider_flow.start_add_slot: user=%s corr_id=%s", callback.from_user.id, corr_id
    )
    try:
        _, services = await cal_svc.list_provider_services(
            clients.calendar_stub(),
            provider_id=provider_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        logger.info(
            "provider:list_provider_services tg=%s provider_id=%s count=%s corr=%s",
            callback.from_user.id,
            provider_id,
            len(services) if services else 0,
            corr_id,
        )
    except grpc.aio.AioRpcError as exc:
        logger.exception("provider_flow.start_add_slot: list_services failed corr_id=%s", corr_id)
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    if not services:
        await callback.message.edit_text(
            "Для вашего профиля пока нет доступных услуг. Добавьте услугу в каталоге и повторите."
        )
        await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
        await callback.answer()
        return

    await state.update_data(
        pending_slot=None,
        slot_services=[{"id": s.id, "name": s.name, "duration": s.default_duration_min} for s in services],
    )
    await state.set_state(ProviderStates.slot_create_service)
    await _clear_prev_prompt(callback.message, state)
    prompt = await callback.message.answer(
        "Выберите услугу для слота:",
        reply_markup=provider_service_select_keyboard(services),
    )
    await _remember_prompt(prompt, state)
    await callback.answer()


@router.callback_query(ProviderStates.slot_create_service, F.data.startswith("provider:slot:service:"))
async def on_slot_service_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, _, service_id = callback.data.split(":")
    logger.info(
        "provider_flow.slot_service_chosen: user=%s service_id=%s", callback.from_user.id, service_id
    )
    data = await state.get_data()
    services_cache = data.get("slot_services") or []
    service_name = next((s.get("name") for s in services_cache if s.get("id") == service_id), "")
    default_duration = next((s.get("duration") for s in services_cache if s.get("id") == service_id), 60)
    await state.update_data(
        pending_slot={"service_id": service_id, "service_name": service_name, "default_duration": default_duration}
    )
    await state.set_state(ProviderStates.slot_create_date)
    await _clear_prev_prompt(callback.message, state)
    prompt = await callback.message.answer(
        "Шаг 1 из 3. Дата.\n"
        "Просто напишите дату: 2025-12-20, 20.12.2025, 20/12/2025 или коротко 20.12.\n",
    )
    await _remember_prompt(prompt, state)
    await callback.answer()


@router.message(ProviderStates.slot_create_date)
async def handle_slot_date(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    date_obj = _parse_date_input(text)
    if not date_obj:
        await message.answer(
            "Не понял дату. Примеры: 2025-12-20 или 20.12"
        )
        return

    data = await state.get_data()
    pending = data.get("pending_slot") or {}
    pending.update({"date": text})
    await state.update_data(pending_slot=pending)
    await state.set_state(ProviderStates.slot_create_time)
    await _clear_prev_prompt(message, state)
    prompt = await message.answer(
        "Шаг 2 из 3. Время.\n"
        "Просто напишите время: 10:00, 10-30, 1030 или 10. Можно добавить пояс: 10:00 мск или 10:00+3.\n"
        "Если ничего не добавлять, считаем время по Москве.\n"
        f"Дата: {date_obj.strftime('%d.%m.%Y')}",
    )
    await _remember_prompt(prompt, state)


@router.message(ProviderStates.slot_create_time)
async def handle_slot_time(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    data = await state.get_data()
    default_offset = data.get("tz_offset_min", 180)
    time_obj, offset_min = _parse_time_with_offset(text, default_offset_min=default_offset)
    if not time_obj:
        await message.answer("Не понял время. Примеры: 10:00, 10-30, 1030, 10, 10:00 мск")
        return

    pending = data.get("pending_slot") or {}
    if not pending.get("date"):
        await message.answer("Сначала укажите дату (шаг 1). Начните заново через меню.")
        await state.set_state(ProviderStates.schedule_dashboard)
        return

    pending.update({"time": text, "tz_offset_min": offset_min})
    await state.update_data(pending_slot=pending, tz_offset_min=offset_min)
    await state.set_state(ProviderStates.slot_create_duration)
    suggested = pending.get("default_duration") or 60
    await _clear_prev_prompt(message, state)
    prompt = await message.answer(
        "Шаг 3 из 3. Сколько минут длится приём?\n"
        f"Пример: {suggested}. Можно от 10 до 480.\n"
        "После ввода покажу итог и попрошу подтвердить.",
    )
    await _remember_prompt(prompt, state)


@router.message(ProviderStates.slot_create_duration)
async def handle_add_slot(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    try:
        duration = int(text)
    except ValueError:
        await message.answer("Длительность должна быть целым числом, пример: 60")
        return
    if duration < 10 or duration > 480:
        await message.answer("Укажите длительность от 10 до 480 минут")
        return

    data = await state.get_data()
    provider_id = data.get("provider_id")
    pending = data.get("pending_slot") or {}
    if not provider_id:
        await message.answer("Нет provider_id, выберите роль представителя /start")
        return
    service_id = pending.get("service_id") or ""
    service_name = pending.get("service_name") or "выбранная услуга"
    date_part = pending.get("date")
    time_part = pending.get("time")
    if not date_part or not time_part:
        await message.answer("Не хватает даты или времени. Начните создание слота заново из меню.")
        await state.set_state(ProviderStates.schedule_dashboard)
        return

    tz_offset = pending.get("tz_offset_min", 180)
    tzinfo_local = timezone(timedelta(minutes=tz_offset))
    # Parse date/time again to ensure consistency
    date_obj = _parse_date_input(date_part)
    time_obj = _parse_time_input(time_part)
    if not date_obj or not time_obj:
        await message.answer("Не удалось разобрать дату и время, укажите заново через меню.")
        await state.set_state(ProviderStates.schedule_dashboard)
        return

    start_local = datetime.combine(date_obj, time_obj, tzinfo=tzinfo_local)
    start_dt = start_local.astimezone(timezone.utc)

    end_local = start_local + timedelta(minutes=duration)
    pretty_start_local = start_local.strftime("%d.%m %H:%M")
    pretty_end_local = end_local.strftime("%H:%M")
    tz_label = _fmt_offset(tz_offset)

    # сохраняем черновик в состоянии и просим подтвердить
    await state.update_data(
        pending_slot={
            "provider_id": provider_id,
            "start_iso": start_dt.isoformat(),
            "duration": duration,
            "service_id": service_id,
            "service_name": service_name,
            "tz_offset_min": tz_offset,
        }
    )
    logger.info(
        "provider_flow.handle_add_slot: user=%s provider_id=%s service_id=%s start=%s duration=%s",
        message.from_user.id,
        provider_id,
        service_id,
        start_dt.isoformat(),
        duration,
    )
    await _clear_prev_prompt(message, state)
    prompt = await message.answer(
        "Проверьте и подтвердите:\n"
        f"• Услуга: {service_name}\n"
        f"• Время: {pretty_start_local} — {pretty_end_local} ({tz_label})\n"
        f"• Длительность: {duration} мин\n\n"
        "Клиенты увидят просто время слота. Пример строки:\n"
        f"• {pretty_start_local} — {pretty_end_local} — свободно",
        reply_markup=provider_add_slot_confirm(f"{pretty_start_local} ({duration} мин)"),
    )
    await _remember_prompt(prompt, state)
    await state.set_state(ProviderStates.slot_create)


@router.callback_query(F.data.startswith("provider:slot:delete:"))
async def delete_slot(callback: CallbackQuery, state: FSMContext):
    _, _, _, slot_id = callback.data.split(":")
    data = await state.get_data()
    provider_id = data.get("provider_id")
    if not provider_id:
        await callback.answer("Нет provider_id", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    logger.info(
        "provider_flow.delete_slot: user=%s provider_id=%s slot_id=%s corr_id=%s",
        callback.from_user.id,
        provider_id,
        slot_id,
        corr_id,
    )
    try:
        await cal_svc.delete_slot(
            stub,
            slot_id=slot_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "provider_flow.delete_slot failed: user=%s provider_id=%s slot_id=%s corr_id=%s",
            callback.from_user.id,
            provider_id,
            slot_id,
            corr_id,
        )
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await callback.message.edit_text("Слот удалён")
    await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "provider:slot:create:confirm")
async def confirm_slot_create(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pending = data.get("pending_slot") or {}
    provider_id = pending.get("provider_id")
    start_iso = pending.get("start_iso")
    duration = pending.get("duration")
    service_id = pending.get("service_id", "")
    if not provider_id or not start_iso or not duration:
        await callback.answer("Нет данных слота", show_alert=True)
        return
    if not service_id:
        await callback.answer("Нет выбранной услуги", show_alert=True)
        return

    try:
        start_dt = datetime.fromisoformat(start_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
    except Exception:
        await callback.answer("Не удалось прочитать время", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    logger.info(
        "provider_flow.confirm_slot_create: user=%s provider_id=%s service_id=%s start=%s duration=%s corr_id=%s",
        callback.from_user.id,
        provider_id,
        service_id,
        start_dt.isoformat(),
        duration,
        corr_id,
    )
    try:
        slot = await cal_svc.create_slot(
            stub,
            provider_id=provider_id,
            service_id=service_id,
            start=start_dt,
            duration_min=int(duration),
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "provider_flow.confirm_slot_create failed: user=%s provider_id=%s service_id=%s corr_id=%s",
            callback.from_user.id,
            provider_id,
            service_id,
            corr_id,
        )
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.update_data(pending_slot=None)
    await state.set_state(ProviderStates.schedule_dashboard)
    tz_offset_min = data.get("tz_offset_min", 180)
    tzinfo_local = timezone(timedelta(minutes=tz_offset_min))
    start_dt = slot.starts_at
    if start_dt and start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    dt_local = start_dt.astimezone(tzinfo_local) if start_dt else None
    pretty = dt_local.strftime('%d.%m %H:%M') if dt_local else ''
    await _clear_prev_prompt(callback.message, state)
    await callback.message.edit_text(f"Слот создан: {pretty}")
    await _show_schedule(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "provider:slot:create:cancel")
async def cancel_slot_create(callback: CallbackQuery, state: FSMContext):
    await state.update_data(pending_slot=None)
    await state.set_state(ProviderStates.schedule_dashboard)
    await _clear_prev_prompt(callback.message, state)
    await callback.message.edit_text("Создание слота отменено")
    await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("provider:slot:edit:"))
async def edit_slot_blocked(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Изменение слота не реализовано в текущей версии.")
    await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "provider:menu")
async def provider_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProviderStates.main_menu)
    await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
    await callback.answer()
