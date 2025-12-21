from datetime import datetime, timedelta, timezone
import logging

import grpc
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telegram_bot.keyboards import provider_bookings_keyboard, provider_main_menu_keyboard
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.errors import user_friendly_error
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.states import ProviderStates
from telegram_bot.utils.corr import new_corr_id
from telegram_bot.utils.roles import format_contact, role_label
from .provider.utils import fmt_bookings, is_active_booking
from .client.utils import fmt_dt, get_client_chat, remember_provider_chat, slot_is_future

router = Router()
logger = logging.getLogger(__name__)


async def _fetch_slot_map_for_provider(
    clients: GrpcClients, settings, provider_id: str, slot_ids: set[str]
) -> dict:
    if not slot_ids:
        return {}
    slot_map = {}
    page = 1
    page_size = 200
    now_utc = datetime.now(timezone.utc)
    from_dt = now_utc - timedelta(days=180)
    to_dt = now_utc + timedelta(days=365)
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
        await message.answer("Не удалось показать записи. Обновите роль через /start или повторите позже.")
        return

    slot_ids = {b.slot_id for b in bookings}
    slot_map = await _fetch_slot_map_for_provider(clients, settings, provider_id, slot_ids)
    try:
        await state.update_data(
            provider_slot_cache={
                sid: (slot_map[sid].starts_at.isoformat() if slot_map.get(sid) else None) for sid in slot_ids
            }
        )
    except Exception:
        logger.exception("provider.bookings: failed to cache slot times provider_id=%s", provider_id)
    now = datetime.now(timezone.utc)
    before = len(bookings)
    bookings = [b for b in bookings if (slot_map.get(b.slot_id) and slot_is_future(slot_map[b.slot_id].starts_at))]
    if before != len(bookings):
        logger.info(
            "provider.bookings: filtered past bookings tg=%s provider_id=%s removed=%s left=%s",
            message.from_user.id,
            provider_id,
            before - len(bookings),
            len(bookings),
        )
    cancellable_ids = {b.id for b in bookings if is_active_booking(b.status)}

    text = fmt_bookings(bookings, slot_map)
    markup = provider_bookings_keyboard(bookings, cancellable_ids)
    await state.set_state(ProviderStates.booking_list)
    if as_edit:
        try:
            await message.edit_text(text, reply_markup=markup)
        except Exception:
            await message.answer(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(ProviderStates.booking_list, F.data.startswith("provider:booking:cancel:"))
async def provider_cancel_booking(callback: CallbackQuery, state: FSMContext):
    _, _, _, booking_id = callback.data.split(":")
    data = await state.get_data()
    provider_id = data.get("provider_id")
    if not provider_id:
        await callback.answer("Нет provider_id, выберите роль представителя /start", show_alert=True)
        return
    try:
        remember_provider_chat(callback.message.bot, provider_id, callback.from_user.id)
    except Exception:
        logger.exception("provider.cancel: failed to remember provider chat tg=%s", callback.from_user.id)

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
        booking = await cal_svc.get_booking(
            clients.calendar_stub(),
            booking_id=booking_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        slot_cache = (await state.get_data()).get("provider_slot_cache") or {}
        slot_iso = slot_cache.get(booking.slot_id)
        slot_dt = datetime.fromisoformat(slot_iso) if slot_iso else None
        slot_text = fmt_dt(slot_dt)
        client_chat = get_client_chat(callback.message.bot, booking.client_id)
        if client_chat:
            try:
                await callback.message.bot.send_message(
                    chat_id=client_chat,
                    text=(
                        "Ваша запись отменена представителем\n"
                        f"Услуга: {booking.service_name or booking.service_id}\n"
                        f"Время: {slot_text}\n"
                        f"Booking: {booking.id[:8]}"
                    ),
                )
                logger.info(
                    "provider.cancel: notified client tg=%s client_id=%s booking=%s",
                    client_chat,
                    booking.client_id,
                    booking.id,
                )
            except Exception:
                logger.exception(
                    "provider.cancel: failed to notify client tg=%s client_id=%s booking=%s",
                    client_chat,
                    booking.client_id,
                    booking.id,
                )
        else:
            logger.warning(
                "provider.cancel: client chat not found client_id=%s booking=%s",
                booking.client_id,
                booking.id,
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


@router.message(F.text == "Мои записи (провайдер)")
async def provider_bookings(message: Message, state: FSMContext):
    await _show_provider_bookings(message, state, as_edit=False)


@router.message(F.text == "Профиль (провайдер)")
async def provider_profile(message: Message, state: FSMContext):
    await state.set_state(ProviderStates.profile_help)
    data = await state.get_data()
    provider_id = data.get("provider_id") or "—"
    role = role_label(data.get("role") or "provider")
    contact = format_contact(data.get("contact_phone"), data.get("username") or message.from_user.username)
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


@router.callback_query(F.data == "provider:menu")
async def provider_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProviderStates.main_menu)
    await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
    await callback.answer()
