from datetime import datetime, timedelta, timezone
import logging

import grpc
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from telegram_bot.keyboards import booking_confirm_keyboard, booking_result_keyboard, slots_keyboard
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.errors import user_friendly_error
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.states import ClientStates
from telegram_bot.utils.corr import new_corr_id
from .utils import cache_slot_context, fmt_dt, get_slot_context, ensure_client_context, get_provider_chat, slot_is_future, slot_is_bookable, filter_available_slots, blacklist_slot

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(ClientStates.slots_view, F.data.startswith("slot:choose:"))
async def on_slot_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, slot_id = callback.data.split(":")
    data = await state.get_data()
    service_id = data.get("selected_service_id")
    provider_id = data.get("selected_provider_id")
    slot_times = data.get("slot_times") or {}
    slot_iso = slot_times.get(slot_id)
    slot_dt = datetime.fromisoformat(slot_iso) if slot_iso else None
    if not service_id or not provider_id or not slot_iso or not slot_is_future(slot_dt):
        # stale slot, refresh list
        settings = callback.message.bot.dispatcher.workflow_data.get("settings")
        clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
        corr_id = new_corr_id()
        try:
            now = datetime.now(timezone.utc)
            slots = await cal_svc.find_free_slots(
                clients.calendar_stub(),
                provider_id=provider_id or "",
                service_id=service_id or "",
                from_dt=now,
                days=cal_svc.DEFAULT_SLOTS_WINDOW_DAYS,
                limit=10,
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
            before = len(slots)
            slots = [s for s in slots if slot_is_bookable(s)]
            slots = await filter_available_slots(callback.message.bot, clients, settings, provider_id or "", slots)
            logger.info(
                "client.booking: refreshed slots after stale selection service=%s provider=%s count=%s filtered=%s sample=%s",
                service_id,
                provider_id,
                len(slots),
                before - len(slots),
                [
                    {
                        "id": s.id[:8],
                        "status": s.status,
                        "start": (s.starts_at.isoformat() if s.starts_at else None),
                    }
                    for s in slots[:5]
                ],
            )
            logger.info(
                "client.booking: refreshed slots on stale selection tg=%s removed=%s left=%s corr=%s",
                callback.from_user.id,
                before - len(slots),
                len(slots),
                corr_id,
            )
            await state.update_data(slot_times={s.id: s.starts_at.isoformat() for s in slots})
            await state.set_state(ClientStates.slots_view)
            await callback.message.edit_text("Слот недоступен, выберите другой:", reply_markup=slots_keyboard(service_id or "", provider_id or "", slots))
            await callback.answer()
            return
        except grpc.aio.AioRpcError as exc:
            await callback.answer(user_friendly_error(exc), show_alert=True)
            return
        cached_ctx = get_slot_context(callback.message.bot, slot_id)
        logger.warning(
            "client.booking: lost context on slot choose tg=%s slot=%s have_service=%s have_provider=%s have_time=%s cached=%s",
            callback.from_user.id,
            slot_id,
            bool(service_id),
            bool(provider_id),
            bool(slot_iso),
            bool(cached_ctx),
        )
        if cached_ctx:
            service_id = service_id or cached_ctx.get("service_id")
            provider_id = provider_id or cached_ctx.get("provider_id")
            slot_iso = slot_iso or cached_ctx.get("starts_at")
            slot_dt = datetime.fromisoformat(slot_iso) if slot_iso else slot_dt
            if slot_iso:
                slot_times = {**slot_times, slot_id: slot_iso}
            await state.update_data(
                selected_service_id=service_id,
                selected_provider_id=provider_id,
                slot_times=slot_times,
            )
    if not service_id or not provider_id:
        await callback.answer("Контекст потерян, начните заново /start", show_alert=True)
        return

    service_cache = data.get("service_cache") or {}
    provider_cache = data.get("provider_cache") or {}
    service = service_cache.get(service_id)
    provider = provider_cache.get(provider_id)
    service_title = service.name if service else service_id
    provider_title = provider.display_name if provider else provider_id
    slot_text = fmt_dt(slot_dt)

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
        cached_ctx = get_slot_context(callback.message.bot, slot_id)
        logger.warning(
            "client.booking: lost context on cancel tg=%s slot=%s have_service=%s have_provider=%s cached=%s",
            callback.from_user.id,
            slot_id,
            bool(service_id),
            bool(provider_id),
            bool(cached_ctx),
        )
        if cached_ctx:
            service_id = service_id or cached_ctx.get("service_id")
            provider_id = provider_id or cached_ctx.get("provider_id")
            await state.update_data(
                selected_service_id=service_id,
                selected_provider_id=provider_id,
            )
    if not service_id or not provider_id:
        await callback.answer("Контекст потерян, начните заново /start", show_alert=True)
        return
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
        before = len(slots)
        slots = [s for s in slots if slot_is_bookable(s)]
        slots = await filter_available_slots(callback.message.bot, clients, settings, provider_id, slots)
        logger.info(
            "client.booking: slots after cancel refresh provider=%s service=%s count=%s filtered=%s sample=%s",
            provider_id,
            service_id,
            len(slots),
            before - len(slots),
            [
                {
                    "id": s.id[:8],
                    "status": s.status,
                    "start": (s.starts_at.isoformat() if s.starts_at else None),
                }
                for s in slots[:5]
            ],
        )
        if before != len(slots):
            logger.info(
                "client.booking: filtered past slots after cancel tg=%s removed=%s left=%s corr=%s",
                callback.from_user.id,
                before - len(slots),
                len(slots),
                corr_id,
            )
        cache_slot_context(callback.message.bot, slots, provider_id, service_id)
    except grpc.aio.AioRpcError as exc:
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.update_data(slot_times={s.id: s.starts_at.isoformat() for s in slots})
    await state.set_state(ClientStates.slots_view)
    await callback.message.edit_text("Выберите другой слот:", reply_markup=slots_keyboard(service_id, provider_id, slots))
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
    if not client_id or not service_id or not provider_id or not slot_iso:
        cached_ctx = get_slot_context(callback.message.bot, slot_id)
        logger.warning(
            "client.booking: lost context on confirm tg=%s slot=%s have_client=%s have_service=%s have_provider=%s have_time=%s cached=%s",
            callback.from_user.id,
            slot_id,
            bool(client_id),
            bool(service_id),
            bool(provider_id),
            bool(slot_iso),
            bool(cached_ctx),
        )
        if cached_ctx:
            service_id = service_id or cached_ctx.get("service_id")
            provider_id = provider_id or cached_ctx.get("provider_id")
            slot_iso = slot_iso or cached_ctx.get("starts_at")
            slot_dt = datetime.fromisoformat(slot_iso) if slot_iso else slot_dt
            if slot_iso:
                slot_times = {**slot_times, slot_id: slot_iso}
            await state.update_data(
                selected_service_id=service_id,
                selected_provider_id=provider_id,
                slot_times=slot_times,
            )
        if not client_id:
            restored = await ensure_client_context(state, callback.message.bot, callback.from_user.id)
            client_id = restored.get("client_id")
            if client_id:
                logger.info(
                    "client.booking: restored client_id via ensure_client_context tg=%s client_id=%s",
                    callback.from_user.id,
                    client_id,
                )
    if not client_id or not service_id or not provider_id:
        await callback.answer("Контекст потерян, начните заново /start", show_alert=True)
        return

    service_cache = data.get("service_cache") or {}
    provider_cache = data.get("provider_cache") or {}
    service = service_cache.get(service_id)
    provider = provider_cache.get(provider_id)
    service_title = (service.name if service else None) or service_id
    provider_title = (provider.display_name if provider else None) or provider_id
    slot_text = fmt_dt(slot_dt)

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    try:
        # Дополнительно сверяем бронирование слота через list_provider_slots
        try:
            slot_window_from = slot_dt - timedelta(days=1) if slot_dt else datetime.now(timezone.utc) - timedelta(days=1)
            slot_window_to = slot_dt + timedelta(days=1) if slot_dt else datetime.now(timezone.utc) + timedelta(days=365)
            slot_page, _ = await cal_svc.list_provider_slots(
                stub,
                provider_id=provider_id,
                from_dt=slot_window_from,
                to_dt=slot_window_to,
                include_bookings=True,
                page=1,
                page_size=50,
                metadata=build_metadata(new_corr_id()),
                timeout=settings.grpc_deadline_sec,
            )
            for ps in slot_page:
                if ps.slot.id != slot_id:
                    continue
                booking_status = getattr(ps.booking, "status", None) if ps.booking else None
                logger.info(
                    "client.booking: precheck slot=%s status=%s booking_status=%s",
                    ps.slot.id,
                    ps.slot.status,
                    booking_status,
                )
                if ps.booking:
                    blacklist_slot(callback.message.bot, slot_id)
                    await callback.message.edit_text(
                        "Слот уже занят. Выберите другой:",
                        reply_markup=slots_keyboard(service_id, provider_id, []),
                    )
                    await callback.answer()
                    return
                if ps.slot.status != "SLOT_STATUS_FREE":
                    blacklist_slot(callback.message.bot, slot_id)
                    await callback.message.edit_text(
                        "Слот недоступен. Выберите другой:",
                        reply_markup=slots_keyboard(service_id, provider_id, []),
                    )
                    await callback.answer()
                    return
        except Exception as precheck_exc:
            logger.exception("client.booking: precheck slot failed tg=%s slot=%s err=%s", callback.from_user.id, slot_id, precheck_exc)

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
        provider_chat = get_provider_chat(callback.message.bot, provider_id)
        if provider_chat:
            try:
                await callback.message.bot.send_message(
                    chat_id=provider_chat,
                    text=(
                        "Новая запись от клиента\n"
                        f"Услуга: {service_title}\n"
                        f"Время: {slot_text}\n"
                        f"Booking: {booking.id[:8]}"
                    ),
                )
                logger.info(
                    "client.booking: notified provider tg=%s provider_id=%s booking=%s",
                    provider_chat,
                    provider_id,
                    booking.id,
                )
            except Exception:
                logger.exception(
                    "client.booking: failed to notify provider tg=%s provider_id=%s booking=%s",
                    provider_chat,
                    provider_id,
                    booking.id,
                )
        else:
            logger.warning(
                "client.booking: provider chat not found, skip notify provider_id=%s booking=%s",
                provider_id,
                booking.id,
            )
    except grpc.aio.AioRpcError as exc:
        dup_key = 'duplicate key value violates unique constraint "idx_bookings_slot_id"' in str(exc)
        if exc.code() == grpc.StatusCode.ALREADY_EXISTS or dup_key:
            # Слот уже занят — обновим список
            logger.warning(
                "client.booking: slot already booked tg=%s slot=%s corr=%s dup=%s",
                callback.from_user.id,
                slot_id,
                corr_id,
                dup_key,
            )
            try:
                now = datetime.now(timezone.utc)
                fresh_slots = await cal_svc.find_free_slots(
                    stub,
                    provider_id=provider_id,
                    service_id=service_id,
                    from_dt=now,
                    days=cal_svc.DEFAULT_SLOTS_WINDOW_DAYS,
                    limit=10,
                    metadata=build_metadata(new_corr_id()),
                    timeout=settings.grpc_deadline_sec,
                )
                before = len(fresh_slots)
                fresh_slots = [s for s in fresh_slots if slot_is_bookable(s)]
                fresh_slots = await filter_available_slots(callback.message.bot, clients, settings, provider_id, fresh_slots)
                logger.info(
                    "client.booking: dup slot refresh provider=%s service=%s count=%s filtered=%s sample=%s",
                    provider_id,
                    service_id,
                    len(fresh_slots),
                    before - len(fresh_slots),
                    [
                        {
                            "id": s.id[:8],
                            "status": s.status,
                            "start": (s.starts_at.isoformat() if s.starts_at else None),
                        }
                        for s in fresh_slots[:5]
                    ],
                )
                await state.update_data(slot_times={s.id: s.starts_at.isoformat() for s in fresh_slots})
                await state.set_state(ClientStates.slots_view)
                await callback.message.edit_text(
                    "Слот уже занят. Выберите другой:",
                    reply_markup=slots_keyboard(service_id, provider_id, fresh_slots),
                )
                await callback.answer()
                return
            except Exception as refresh_exc:
                logger.exception("client.booking: failed to refresh slots after dup tg=%s err=%s", callback.from_user.id, refresh_exc)
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
