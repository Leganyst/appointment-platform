from datetime import datetime, timedelta, timezone
import logging

import grpc
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telegram_bot.keyboards import booking_details_keyboard, cancel_result_keyboard, main_menu_keyboard, my_bookings_keyboard, provider_main_menu_keyboard
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.errors import user_friendly_error
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.states import ClientStates, ProviderStates
from telegram_bot.utils.corr import new_corr_id
from .utils import (
    build_slot_map_for_bookings,
    ensure_client_context,
    fmt_dt,
    format_bookings_split,
    get_provider_chat,
    is_active_booking,
    slot_is_future,
)

router = Router()
logger = logging.getLogger(__name__)


def _filter_future_bookings(bookings, slot_cache):
    filtered = [b for b in bookings if (slot_cache.get(b.slot_id) and slot_is_future(slot_cache[b.slot_id].starts_at))]
    if len(filtered) != len(bookings):
        logger.info(
            "client.bookings: filtered past bookings removed=%s left=%s",
            len(bookings) - len(filtered),
            len(filtered),
        )
    return filtered


@router.message(F.text == "Мои записи")
async def on_my_bookings(message: Message, state: FSMContext):
    data = await ensure_client_context(state, message.bot, message.from_user.id)
    client_id = data.get("client_id")
    if not client_id:
        await message.answer("Не нашёл ваш профиль, повторите /start")
        return

    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        logger.info("client.bookings: tg=%s client_id=%s corr=%s", message.from_user.id, client_id, corr_id)
        bookings = await cal_svc.list_bookings(
            clients.calendar_stub(),
            client_id=client_id,
            from_dt=datetime.now(timezone.utc) - timedelta(days=30),
            to_dt=datetime.now(timezone.utc) + timedelta(days=60),
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        slot_cache = await build_slot_map_for_bookings(clients, settings, bookings)
        bookings = _filter_future_bookings(bookings, slot_cache)
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "client.bookings failed: tg=%s client_id=%s corr=%s code=%s details=%s",
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
    cancellable_ids = {b.id for b in bookings if is_active_booking(b.status)}
    await message.answer(
        format_bookings_split(bookings, slot_cache),
        reply_markup=my_bookings_keyboard(bookings, cancellable_ids),
    )


@router.callback_query(F.data == "bookings:mine")
async def on_bookings_inline(callback: CallbackQuery, state: FSMContext):
    data = await ensure_client_context(state, callback.message.bot, callback.from_user.id)
    client_id = data.get("client_id")
    if not client_id:
        await callback.answer("Не нашёл ваш профиль, повторите /start", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        logger.info("client.bookings_inline: tg=%s client_id=%s corr=%s", callback.from_user.id, client_id, corr_id)
        bookings = await cal_svc.list_bookings(
            clients.calendar_stub(),
            client_id=client_id,
            from_dt=datetime.now(timezone.utc) - timedelta(days=30),
            to_dt=datetime.now(timezone.utc) + timedelta(days=60),
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        slot_cache = await build_slot_map_for_bookings(clients, settings, bookings)
        bookings = _filter_future_bookings(bookings, slot_cache)
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "client.bookings_inline failed: tg=%s client_id=%s corr=%s code=%s details=%s",
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
    cancellable_ids = {b.id for b in bookings if is_active_booking(b.status)}
    await callback.message.edit_text(
        format_bookings_split(bookings, slot_cache),
        reply_markup=my_bookings_keyboard(bookings, cancellable_ids),
    )
    await callback.answer()


@router.callback_query(ClientStates.booking_result, F.data == "bookings:mine")
async def on_booking_result_to_my(callback: CallbackQuery, state: FSMContext):
    data = await ensure_client_context(state, callback.message.bot, callback.from_user.id)
    client_id = data.get("client_id")
    if not client_id:
        await callback.answer("Не нашёл ваш профиль, повторите /start", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        logger.info("client.bookings_inline(from_result): tg=%s client_id=%s corr=%s", callback.from_user.id, client_id, corr_id)
        bookings = await cal_svc.list_bookings(
            clients.calendar_stub(),
            client_id=client_id,
            from_dt=datetime.now(timezone.utc) - timedelta(days=30),
            to_dt=datetime.now(timezone.utc) + timedelta(days=60),
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        slot_cache = await build_slot_map_for_bookings(clients, settings, bookings)
        bookings = _filter_future_bookings(bookings, slot_cache)
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "client.bookings_inline(from_result) failed: tg=%s client_id=%s corr=%s code=%s details=%s",
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
    cancellable_ids = {b.id for b in bookings if is_active_booking(b.status)}
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
            slot_cache = await build_slot_map_for_bookings(clients, settings, [booking])
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
            f"Приём: {fmt_dt((slot_cache.get(booking.slot_id) or {}).starts_at if slot_cache.get(booking.slot_id) else None)}\n"
            f"Создано: {fmt_dt(booking.created_at)}\n"
            f"Отменено: {fmt_dt(booking.cancelled_at)}\n"
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
        provider_chat = get_provider_chat(callback.message.bot, booking.provider_id)
        slot_cache = (await state.get_data()).get("slot_cache") or {}
        slot_dt = fmt_dt((slot_cache.get(booking.slot_id) or {}).starts_at if slot_cache.get(booking.slot_id) else None)
        if provider_chat:
            try:
                await callback.message.bot.send_message(
                    chat_id=provider_chat,
                    text=(
                        "Клиент отменил запись\n"
                        f"Услуга: {booking.service_name or booking.service_id}\n"
                        f"Время: {slot_dt}\n"
                        f"Booking: {booking.id[:8]}"
                    ),
                )
                logger.info(
                    "client.bookings: notified provider about cancel tg=%s provider_id=%s booking=%s",
                    provider_chat,
                    booking.provider_id,
                    booking.id,
                )
            except Exception:
                logger.exception(
                    "client.bookings: failed notify provider cancel tg=%s provider_id=%s booking=%s",
                    provider_chat,
                    booking.provider_id,
                    booking.id,
                )
        else:
            logger.warning(
                "client.bookings: provider chat not found for cancel provider_id=%s booking=%s",
                booking.provider_id,
                booking.id,
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
