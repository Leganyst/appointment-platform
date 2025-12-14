from datetime import datetime, timedelta, timezone

import grpc
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telegram_bot.keyboards import (
    provider_add_slot_confirm,
    provider_main_menu_keyboard,
    provider_schedule_keyboard,
    provider_slots_actions,
)
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.errors import user_friendly_error
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.states import ProviderStates
from telegram_bot.utils.corr import new_corr_id

router = Router()


def _fmt_slots(slots):
    if not slots:
        return "Слотов нет."
    lines = []
    for ps in slots:
        s = ps.slot
        dt = s.starts_at.strftime("%d.%m %H:%M") if s.starts_at else ""
        booking_info = ""
        if ps.booking:
            booking_info = f" | бронь: {ps.booking.status}"
        lines.append(f"• {dt} ({s.id}) статус: {s.status}{booking_info}")
    return "\n".join(lines)


def _fmt_bookings(bookings):
    if not bookings:
        return "Записей нет."
    lines = []
    for b in bookings:
        dt = b.created_at.strftime("%d.%m %H:%M") if b.created_at else ""
        lines.append(f"• {b.service_name or b.service_id} | {dt} | статус: {b.status} | id: {b.id}")
    return "\n".join(lines)


async def _show_schedule(message: Message, state: FSMContext):
    data = await state.get_data()
    provider_id = data.get("provider_id")
    if not provider_id:
        await message.answer("Нет provider_id, выберите роль представителя /start")
        return

    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    try:
        now = datetime.now(timezone.utc)
        slots, _ = await cal_svc.list_provider_slots(
            stub,
            provider_id=provider_id,
            from_dt=now,
            to_dt=now + timedelta(days=cal_svc.DEFAULT_SLOTS_WINDOW_DAYS),
            include_bookings=True,
            page=1,
            page_size=20,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await message.answer(user_friendly_error(exc))
        return

    await state.set_state(ProviderStates.schedule_dashboard)
    await message.answer(
        _fmt_slots(slots),
        reply_markup=provider_schedule_keyboard(_fmt_slots(slots)),
    )
    if slots:
        for slot in slots[:5]:
            await message.answer(
                f"Слот {slot.id}: {slot.starts_at.strftime('%d.%m %H:%M') if slot.starts_at else ''}",
                reply_markup=provider_slots_actions(slot.id),
            )


@router.message(F.text == "Управление расписанием")
async def schedule_dashboard(message: Message, state: FSMContext):
    await _show_schedule(message, state)


@router.callback_query(F.data.in_({"provider:slot:refresh", "provider:slot:list"}))
async def refresh_schedule(callback: CallbackQuery, state: FSMContext):
    await _show_schedule(callback.message, state)
    await callback.answer()


@router.message(F.text == "Мои записи (провайдер)")
async def provider_bookings(message: Message, state: FSMContext):
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
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await message.answer(user_friendly_error(exc))
        return

    await state.set_state(ProviderStates.booking_list)
    await message.answer(
        _fmt_bookings(bookings),
        reply_markup=provider_main_menu_keyboard(),
    )


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

    await callback.message.edit_text(
        f"Бронь подтверждена. Статус: {booking.status}",
        reply_markup=provider_main_menu_keyboard(),
    )
    await callback.answer()


@router.message(F.text == "Профиль (провайдер)")
async def provider_profile(message: Message, state: FSMContext):
    await state.set_state(ProviderStates.profile_help)
    data = await state.get_data()
    provider_id = data.get("provider_id") or "—"
    role = data.get("role") or "provider"
    contact = data.get("contact_phone") or "—"
    text = (
        "Профиль представителя\n"
        f"Имя в TG: {message.from_user.full_name}\n"
        f"Роль: {role}\n"
        f"Provider ID: {provider_id}\n"
        f"Контакты: {contact}\n"
        "Разделы: управление расписанием, мои записи, помощь.\n"
        "Для смены роли используйте /start → Выбор роли."
    )
    await message.answer(text, reply_markup=provider_main_menu_keyboard())


@router.callback_query(F.data.startswith("provider:slot:add"))
async def start_add_slot(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProviderStates.slot_create)
    await callback.message.edit_text(
        "Введите слот в формате: YYYY-MM-DD HH:MM +duration_min (например '2025-12-20 10:00 60')"
    )
    await callback.answer()


@router.message(ProviderStates.slot_create)
async def handle_add_slot(message: Message, state: FSMContext):
    parts = message.text.strip().split()
    if len(parts) != 3:
        await message.answer("Неверный формат. Пример: 2025-12-20 10:00 60")
        return
    date_part, time_part, dur_part = parts
    try:
        start_dt = datetime.fromisoformat(f"{date_part} {time_part}")
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        duration = int(dur_part)
    except Exception:
        await message.answer("Не удалось распарсить дату/длительность")
        return

    data = await state.get_data()
    provider_id = data.get("provider_id")
    if not provider_id:
        await message.answer("Нет provider_id, выберите роль представителя /start")
        return

    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    # сохраняем черновик в состоянии и просим подтвердить
    await state.update_data(
        pending_slot={
            "provider_id": provider_id,
            "start_iso": start_dt.isoformat(),
            "duration": duration,
            "service_id": "",
        }
    )
    pretty_dt = start_dt.strftime("%d.%m %H:%M")
    await message.answer(
        f"Создать слот {pretty_dt} на {duration} мин?",
        reply_markup=provider_add_slot_confirm(f"{pretty_dt} ({duration} мин)"),
    )


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
    try:
        await cal_svc.delete_slot(
            stub,
            slot_id=slot_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await callback.message.edit_text("Слот удалён", reply_markup=provider_main_menu_keyboard())
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
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.update_data(pending_slot=None)
    await state.set_state(ProviderStates.schedule_dashboard)
    await callback.message.edit_text(
        f"Слот создан: {slot.starts_at.strftime('%d.%m %H:%M') if slot.starts_at else ''}",
        reply_markup=provider_main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "provider:slot:create:cancel")
async def cancel_slot_create(callback: CallbackQuery, state: FSMContext):
    await state.update_data(pending_slot=None)
    await state.set_state(ProviderStates.schedule_dashboard)
    await callback.message.edit_text("Создание слота отменено", reply_markup=provider_main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("provider:slot:edit:"))
async def edit_slot_blocked(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Изменение слота не реализовано в текущей версии.", reply_markup=provider_main_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "provider:menu")
async def provider_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProviderStates.main_menu)
    await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
    await callback.answer()
