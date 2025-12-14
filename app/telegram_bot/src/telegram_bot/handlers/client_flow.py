from datetime import datetime, timezone

import grpc
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telegram_bot.keyboards import (
    booking_confirm_keyboard,
    booking_details_keyboard,
    booking_result_keyboard,
    cancel_result_keyboard,
    main_menu_keyboard,
    my_bookings_keyboard,
    provider_keyboard,
    service_search_keyboard,
    slots_keyboard,
)
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.errors import user_friendly_error
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.states import ClientStates
from telegram_bot.utils.corr import new_corr_id


def format_bookings(bookings):
    if not bookings:
        return "Активных записей нет."
    lines = []
    for b in bookings:
        dt = b.created_at.strftime("%d.%m %H:%M") if b.created_at else ""
        lines.append(f"• {b.service_name or b.service_id} у {b.provider_name or b.provider_id} ({dt}) статус: {b.status}")
    return "\n".join(lines)

router = Router()


# Главное меню (reply кнопки)
@router.message(F.text == "Поиск услуг")
async def on_search_services(message: Message, state: FSMContext):
    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        services = await cal_svc.list_services(
            clients.calendar_stub(),
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
    await state.update_data(selected_service_id=None, selected_provider_id=None, selected_slot_id=None)
    await message.answer("Выберите услугу:", reply_markup=service_search_keyboard(services))


@router.message(F.text == "Мои записи")
async def on_my_bookings(message: Message, state: FSMContext):
    data = await state.get_data()
    client_id = data.get("client_id")
    if not client_id:
        await message.answer("Нет client_id, повторите /start")
        return

    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        bookings = await cal_svc.list_bookings(
            clients.calendar_stub(),
            client_id=client_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await message.answer(user_friendly_error(exc))
        return

    await state.set_state(ClientStates.my_bookings)
    await message.answer(
        format_bookings(bookings),
        reply_markup=my_bookings_keyboard(bookings),
    )


@router.message(F.text.in_({"Профиль", "Помощь"}))
async def on_profile_help(message: Message, state: FSMContext):
    await state.set_state(ClientStates.profile_help)
    await message.answer(
        "Профиль/помощь (заглушка). Имя, роль, контакты, команды.",
        reply_markup=main_menu_keyboard(),
    )


# Поиск услуги → выбор провайдера
@router.callback_query(ClientStates.service_search, F.data.startswith("service:choose:"))
async def on_service_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, service_id = callback.data.split(":")
    await state.update_data(selected_service_id=service_id, selected_provider_id=None, selected_slot_id=None)
    await state.set_state(ClientStates.service_search)
    await callback.message.edit_text(
        f"Услуга выбрана: {service_id}. Выберите представителя:",
        reply_markup=provider_keyboard(service_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("provider:choose:"))
async def on_provider_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, service_id, provider_id = callback.data.split(":")
    data = await state.get_data()
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
            reply_markup=provider_keyboard(service_id),
        )
        await callback.answer()
        return

    await state.set_state(ClientStates.slots_view)
    await callback.message.edit_text(
        f"Провайдер выбран: {provider_id}. Доступные слоты:",
        reply_markup=slots_keyboard(service_id, provider_id, slots),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("provider:back:"))
async def on_provider_back(callback: CallbackQuery, state: FSMContext):
    _, _, service_id = callback.data.split(":")
    await state.set_state(ClientStates.service_search)
    await callback.message.edit_text(
        f"Вернулись к выбору провайдера для услуги {service_id}.",
        reply_markup=provider_keyboard(service_id),
    )
    await callback.answer()


# Слоты → подтверждение
@router.callback_query(ClientStates.slots_view, F.data.startswith("slot:choose:"))
async def on_slot_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, service_id, provider_id, slot_id = callback.data.split(":")
    await state.update_data(selected_slot_id=slot_id)
    await state.set_state(ClientStates.booking_confirm)
    await callback.message.edit_text(
        f"Слот выбран: {slot_id}. Подтвердить?",
        reply_markup=booking_confirm_keyboard(service_id, provider_id, slot_id),
    )
    await callback.answer()


@router.callback_query(ClientStates.booking_confirm, F.data.startswith("booking:cancel:"))
async def on_booking_cancel(callback: CallbackQuery, state: FSMContext):
    _, _, service_id, provider_id = callback.data.split(":")
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

    await state.set_state(ClientStates.slots_view)
    await callback.message.edit_text(
        "Выберите другой слот:", reply_markup=slots_keyboard(service_id, provider_id, slots)
    )
    await callback.answer()


@router.callback_query(ClientStates.booking_confirm, F.data.startswith("booking:confirm:"))
async def on_booking_confirm(callback: CallbackQuery, state: FSMContext):
    _, _, service_id, provider_id, slot_id = callback.data.split(":")
    data = await state.get_data()
    client_id = data.get("client_id")
    if not client_id:
        await callback.answer("Нет client_id, повторите /start", show_alert=True)
        return

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
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.set_state(ClientStates.booking_result)
    await callback.message.edit_text(
        f"Бронь создана: {booking.service_name} у {booking.provider_name} на {booking.id}",
        reply_markup=booking_result_keyboard(success=True),
    )
    await callback.answer("Успешно")


# Бронирование результат → переходы
@router.callback_query(ClientStates.booking_result, F.data == "bookings:mine")
async def on_booking_result_to_my(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    client_id = data.get("client_id")
    if not client_id:
        await callback.answer("Нет client_id, повторите /start", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        bookings = await cal_svc.list_bookings(
            clients.calendar_stub(),
            client_id=client_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.set_state(ClientStates.my_bookings)
    await callback.message.edit_text(
        format_bookings(bookings),
        reply_markup=my_bookings_keyboard(bookings),
    )
    await callback.answer()


# Мои записи
@router.callback_query(F.data == "bookings:mine")
async def on_bookings_inline(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    client_id = data.get("client_id")
    if not client_id:
        await callback.answer("Нет client_id, повторите /start", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        bookings = await cal_svc.list_bookings(
            clients.calendar_stub(),
            client_id=client_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.set_state(ClientStates.my_bookings)
    await callback.message.edit_text(
        format_bookings(bookings),
        reply_markup=my_bookings_keyboard(bookings),
    )
    await callback.answer()


@router.callback_query(ClientStates.my_bookings, F.data.startswith("booking:detail:"))
async def on_booking_detail(callback: CallbackQuery, state: FSMContext):
    _, _, booking_id = callback.data.split(":")
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
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.set_state(ClientStates.booking_details)
    await callback.message.edit_text(
        f"{booking.service_name} у {booking.provider_name}\nСтатус: {booking.status}",
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
    await state.set_state(ClientStates.main_menu)
    await callback.message.answer("Главное меню:", reply_markup=main_menu_keyboard())
    await callback.answer()


# Профиль/помощь через inline не дублируем, т.к. покрыто текстом меню


@router.callback_query(F.data == "menu:main")
async def on_menu_any(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ClientStates.main_menu)
    await callback.message.answer("Главное меню:", reply_markup=main_menu_keyboard())
    await callback.answer()
