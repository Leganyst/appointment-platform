from datetime import datetime, timedelta, timezone
import logging

import grpc
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telegram_bot.keyboards import (
    provider_add_slot_confirm,
    provider_main_menu_keyboard,
    provider_schedule_keyboard,
    provider_service_select_keyboard,
    provider_slots_actions,
    provider_slots_list_keyboard,
    provider_week_confirm_keyboard,
    provider_week_days_keyboard,
)
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.errors import user_friendly_error
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.states import ProviderStates
from telegram_bot.utils.corr import new_corr_id
from .utils import (
    is_active_booking,
    clear_prev_prompt,
    fmt_offset,
    fmt_slots,
    fmt_times_list,
    fmt_weekday_set,
    parse_date_input,
    parse_time_input,
    parse_time_list,
    parse_time_with_offset,
    remember_prompt,
)

router = Router()
logger = logging.getLogger(__name__)


async def _get_provider_id(state: FSMContext, telegram_id: int, bot) -> str | None:
    """Получить provider_id из state или загрузить с бэкенда"""
    data = await state.get_data()
    provider_id = data.get("provider_id")
    
    if provider_id:
        from telegram_bot.handlers.client.utils import remember_provider_chat
        remember_provider_chat(bot, provider_id, telegram_id)
        return provider_id
    
    # Загружаем с бэкенда
    settings = bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    try:
        from telegram_bot.services.identity import get_profile
        user = await get_profile(
            clients.identity_stub(),
            telegram_id=telegram_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        provider_id = user.provider_id
        if provider_id:
            await state.update_data(
                provider_id=provider_id,
                client_id=user.client_id,
                role=user.role_code,
            )
            logger.info("provider.schedule: restored provider_id from backend tg=%s provider_id=%s", telegram_id, provider_id)
            from telegram_bot.handlers.client.utils import remember_provider_chat
            remember_provider_chat(bot, provider_id, telegram_id)
        return provider_id
    except Exception:
        logger.exception("provider.schedule: failed to load provider_id tg=%s", telegram_id)
        return None


async def _show_schedule(message: Message, state: FSMContext, page: int = 1, user_id: int | None = None):
    data = await state.get_data()
    tz_offset_min = data.get("tz_offset_min", 180)
    
    # Определяем telegram_id пользователя (для callback может быть None в message.from_user)
    tg_id = user_id or (message.from_user.id if message.from_user else None)
    
    if not tg_id:
        await message.bot.send_message(chat_id=message.chat.id, text="Ошибка: не удалось определить пользователя")
        return
    
    # Получаем provider_id (с автоподгрузкой с бэкенда при необходимости)
    provider_id = await _get_provider_id(state, tg_id, message.bot)
    
    if not provider_id:
        await message.bot.send_message(chat_id=message.chat.id, text="Нет provider_id, выберите роль представителя /start")
        return

    # Обновляем кэш чатов для уведомлений (особенно после /start или рестарта бота)
    try:
        from telegram_bot.handlers.client.utils import remember_provider_chat
        remember_provider_chat(message.bot, provider_id, message.chat.id)
    except Exception:
        logger.exception("provider.schedule: failed to remember provider chat tg=%s", tg_id)
    
    logger.info("provider.schedule: show_schedule user=%s provider_id=%s page=%s", tg_id, provider_id, page)

    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    page_size = 5  # Уменьшено для тестирования пагинации
    
    # Получаем текущие услуги провайдера для фильтрации слотов
    try:
        _, provider_services = await cal_svc.list_provider_services(
            stub,
            provider_id=provider_id,
            metadata=build_metadata(new_corr_id()),
            timeout=settings.grpc_deadline_sec,
        )
        current_service_ids = {s.id for s in provider_services}
    except grpc.aio.AioRpcError:
        current_service_ids = set()
    
    try:
        now = datetime.now(timezone.utc)
        logger.info(
            "provider:list_provider_slots tg=%s provider_id=%s from=%s to=%s page=%s corr=%s",
            tg_id,
            provider_id,
            now.isoformat(),
            (now + timedelta(days=365)).isoformat(),
            page,
            corr_id,
        )
        all_slots, _ = await cal_svc.list_provider_slots(
            stub,
            provider_id=provider_id,
            from_dt=now,
            to_dt=now + timedelta(days=365),
            include_bookings=True,
            page=1,
            page_size=1000,  # Получаем все для фильтрации
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await message.bot.send_message(chat_id=message.chat.id, text=user_friendly_error(exc))
        return

    # Фильтруем слоты по текущим услугам провайдера
    if current_service_ids:
        filtered_slots = [ps for ps in all_slots if ps.slot.service_id in current_service_ids]
        orphaned_count = len(all_slots) - len(filtered_slots)
    else:
        # Если услуг нет вообще - показываем все слоты (legacy)
        filtered_slots = all_slots
        orphaned_count = 0
    
    total_count = len(filtered_slots)
    
    # Пагинация на отфильтрованных данных
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    slots = filtered_slots[start_idx:end_idx]

    await state.set_state(ProviderStates.schedule_dashboard)
    await state.update_data(schedule_page=page)
    
    # Выводим список слотов компактно
    slots_text = fmt_slots(slots, tz_offset_min)
    if slots:
        slots_text += f"\n\n📋 Показано слотов: {len(slots)} из {total_count}"
    
    # Предупреждение об осиротевших слотах
    if orphaned_count > 0:
        slots_text += f"\n\n⚠️ Скрыто {orphaned_count} слотов от старых услуг"
    
    has_prev = page > 1
    has_next = end_idx < total_count
    
    # Сохраняем слоты в state для управления
    await state.update_data(
        cached_slots=[{"id": ps.slot.id, "starts_at": ps.slot.starts_at.isoformat() if ps.slot.starts_at else None, "status": ps.slot.status, "has_booking": ps.booking is not None} for ps in slots],
        total_slots=total_count,
        current_service_ids=list(current_service_ids),
    )
    
    # Используем chat.id для отправки, чтобы работало после удаления сообщения
    await message.bot.send_message(
        chat_id=message.chat.id,
        text=slots_text,
        reply_markup=provider_schedule_keyboard(page=page, has_prev=has_prev, has_next=has_next, slots_count=len(slots)),
    )


@router.message(F.text == "Управление расписанием")
async def schedule_dashboard(message: Message, state: FSMContext):
    await _show_schedule(message, state)


@router.callback_query(F.data.in_({"provider:slot:refresh", "provider:slot:list"}))
async def refresh_schedule(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = data.get("schedule_page", 1)
    user_id = callback.from_user.id
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _show_schedule(callback.message, state, page=page, user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("provider:slot:page:"))
async def change_schedule_page(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    logger.info("provider.schedule: change_schedule_page TRIGGERED user=%s data=%s state=%s", callback.from_user.id, callback.data, current_state)
    try:
        page = int(callback.data.split(":")[-1])
    except (ValueError, IndexError):
        logger.error("provider.schedule: change_schedule_page PARSE ERROR data=%s", callback.data)
        await callback.answer("Неверный номер страницы", show_alert=True)
        return
    
    user_id = callback.from_user.id
    logger.info("provider.schedule: change_schedule_page parsed page=%s user=%s", page, user_id)
    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning("provider.schedule: change_schedule_page delete failed: %s", e)
    await _show_schedule(callback.message, state, page=page, user_id=user_id)
    await callback.answer()


# ----- Управление слотами -----


@router.callback_query(F.data == "provider:slots:manage")
async def show_slots_management(callback: CallbackQuery, state: FSMContext):
    """Показать список слотов для управления"""
    data = await state.get_data()
    tz_offset_min = data.get("tz_offset_min", 180)
    user_id = callback.from_user.id
    
    provider_id = await _get_provider_id(state, user_id, callback.message.bot)
    if not provider_id:
        await callback.answer("Нет provider_id", show_alert=True)
        return
    
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    stub = clients.calendar_stub()
    corr_id = new_corr_id()
    page_size = 10
    
    # Получаем текущие услуги провайдера для фильтрации
    current_service_ids = data.get("current_service_ids")
    if not current_service_ids:
        try:
            _, provider_services = await cal_svc.list_provider_services(
                stub,
                provider_id=provider_id,
                metadata=build_metadata(new_corr_id()),
                timeout=settings.grpc_deadline_sec,
            )
            current_service_ids = [s.id for s in provider_services]
            await state.update_data(current_service_ids=current_service_ids)
        except grpc.aio.AioRpcError:
            current_service_ids = []
    
    current_service_set = set(current_service_ids) if current_service_ids else set()
    
    try:
        now = datetime.now(timezone.utc)
        all_slots, _ = await cal_svc.list_provider_slots(
            stub,
            provider_id=provider_id,
            from_dt=now,
            to_dt=now + timedelta(days=365),
            include_bookings=True,
            page=1,
            page_size=1000,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        logger.info(
            "provider.manage_slots: list_provider_slots provider=%s count=%s sample=%s",
            provider_id,
            len(all_slots),
            [
                {
                    "id": ps.slot.id[:8],
                    "slot_status": ps.slot.status,
                    "booking_status": getattr(ps.booking, "status", None) if ps.booking else None,
                }
                for ps in all_slots[:5]
            ],
        )
    except grpc.aio.AioRpcError as exc:
        await callback.answer(user_friendly_error(exc), show_alert=True)
        return
    
    # Фильтруем слоты по текущим услугам
    if current_service_set:
        filtered_slots = [ps for ps in all_slots if ps.slot.service_id in current_service_set]
    else:
        filtered_slots = all_slots
    
    if not filtered_slots:
        await callback.answer("Нет слотов для управления", show_alert=True)
        return
    
    total_count = len(filtered_slots)
    slots = filtered_slots[:page_size]
    has_next = total_count > page_size
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    await callback.message.bot.send_message(
        chat_id=callback.message.chat.id,
        text="🗂 Выберите слот для управления:\n\n🟢 — свободно\n🔴 — забронировано",
        reply_markup=provider_slots_list_keyboard(slots, tz_offset_min, page=1, has_prev=False, has_next=has_next),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("provider:slots:manage:page:"))
async def slots_management_page(callback: CallbackQuery, state: FSMContext):
    """Пагинация в режиме управления слотами"""
    try:
        page = int(callback.data.split(":")[-1])
    except (ValueError, IndexError):
        await callback.answer("Неверный номер страницы", show_alert=True)
        return
    
    data = await state.get_data()
    tz_offset_min = data.get("tz_offset_min", 180)
    user_id = callback.from_user.id
    
    provider_id = await _get_provider_id(state, user_id, callback.message.bot)
    if not provider_id:
        await callback.answer("Нет provider_id", show_alert=True)
        return
    
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    stub = clients.calendar_stub()
    corr_id = new_corr_id()
    page_size = 10
    
    # Получаем текущие услуги провайдера для фильтрации
    current_service_ids = data.get("current_service_ids", [])
    current_service_set = set(current_service_ids) if current_service_ids else set()
    
    try:
        now = datetime.now(timezone.utc)
        all_slots, _ = await cal_svc.list_provider_slots(
            stub,
            provider_id=provider_id,
            from_dt=now,
            to_dt=now + timedelta(days=365),
            include_bookings=True,
            page=1,
            page_size=1000,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        await callback.answer(user_friendly_error(exc), show_alert=True)
        return
    
    # Фильтруем слоты по текущим услугам
    if current_service_set:
        filtered_slots = [ps for ps in all_slots if ps.slot.service_id in current_service_set]
    else:
        filtered_slots = all_slots
    
    total_count = len(filtered_slots)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    slots = filtered_slots[start_idx:end_idx]
    
    has_prev = page > 1
    has_next = end_idx < total_count
    
    try:
        await callback.message.edit_text(
            "🗂 Выберите слот для управления:\n\n🟢 — свободно\n🔴 — забронировано",
            reply_markup=provider_slots_list_keyboard(slots, tz_offset_min, page=page, has_prev=has_prev, has_next=has_next),
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("provider:slot:select:"))
async def select_slot_for_action(callback: CallbackQuery, state: FSMContext):
    """Выбор конкретного слота — показываем меню действий"""
    slot_id_prefix = callback.data.split(":")[-1]
    
    data = await state.get_data()
    tz_offset_min = data.get("tz_offset_min", 180)
    user_id = callback.from_user.id
    
    provider_id = await _get_provider_id(state, user_id, callback.message.bot)
    if not provider_id:
        await callback.answer("Нет provider_id", show_alert=True)
        return
    
    # Ищем полный ID слота
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    
    try:
        now = datetime.now(timezone.utc)
        slots, _ = await cal_svc.list_provider_slots(
            clients.calendar_stub(),
            provider_id=provider_id,
            from_dt=now,
            to_dt=now + timedelta(days=365),
            include_bookings=True,
            page=1,
            page_size=50,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        logger.info(
            "provider.slot_select: slots page provider=%s count=%s sample=%s",
            provider_id,
            len(slots),
            [
                {
                    "id": ps.slot.id[:8],
                    "slot_status": ps.slot.status,
                    "booking_status": getattr(ps.booking, "status", None) if ps.booking else None,
                }
                for ps in slots[:5]
            ],
        )
    except grpc.aio.AioRpcError as exc:
        await callback.answer(user_friendly_error(exc), show_alert=True)
        return
    
    # Находим слот по префиксу ID
    selected_slot = None
    for ps in slots:
        if ps.slot.id.startswith(slot_id_prefix):
            selected_slot = ps
            break
    
    if not selected_slot:
        await callback.answer("Слот не найден", show_alert=True)
        return
    
    slot = selected_slot.slot
    booking_status = getattr(selected_slot.booking, "status", None) if selected_slot.booking else None
    start_dt = slot.starts_at
    if start_dt and start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    tzinfo_local = timezone(timedelta(minutes=tz_offset_min))
    dt_local = start_dt.astimezone(tzinfo_local) if start_dt else None
    dt_label = dt_local.strftime("%d.%m.%Y %H:%M") if dt_local else "?"
    
    has_active_booking = selected_slot.booking and is_active_booking(booking_status)
    has_any_booking = selected_slot.booking is not None
    status_text = "🔴 Забронирован" if has_any_booking or slot.status == "SLOT_STATUS_BOOKED" else "🟢 Свободен" if slot.status == "SLOT_STATUS_FREE" else "⚪ Другой статус"
    logger.info(
        "provider.slot_select: chosen slot=%s status=%s booking_status=%s active=%s",
        slot.id,
        slot.status,
        booking_status,
        has_active_booking,
    )
    
    slot_info = (
        f"📅 Слот: {dt_label}\n"
        f"Статус: {status_text}\n"
    )
    
    if selected_slot.booking:
        if has_active_booking:
            slot_info += f"⚠️ На этот слот есть бронирование! ({booking_status})\n"
        else:
            slot_info += f"ℹ️ Есть бронь со статусом: {booking_status}\n"
    
    try:
        await callback.message.edit_text(
            slot_info,
            reply_markup=provider_slots_actions(slot.id),
        )
    except Exception:
        pass
    await callback.answer()


# ----- Single-slot creation -----


@router.callback_query(F.data == "provider:slot:add")
async def start_add_slot(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    provider_id = await _get_provider_id(state, user_id, callback.message.bot)
    if not provider_id:
        await callback.message.edit_text("Нет provider_id, выберите роль представителя /start")
        await callback.answer()
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    logger.info("provider.schedule: start_add_slot user=%s corr_id=%s", callback.from_user.id, corr_id)
    try:
        _, services = await cal_svc.list_provider_services(
            clients.calendar_stub(),
            provider_id=provider_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        logger.exception("provider.schedule: list_services failed corr_id=%s", corr_id)
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    if not services:
        await callback.message.edit_text(
            "Для вашего профиля пока нет доступных услуг. Добавьте услугу и повторите."
        )
        await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
        await callback.answer()
        return

    await state.update_data(
        pending_slot=None,
        slot_services=[{"id": s.id, "name": s.name, "duration": s.default_duration_min} for s in services],
    )
    await state.set_state(ProviderStates.slot_create_service)
    await clear_prev_prompt(callback.message, state)
    try:
        await callback.message.delete()
    except Exception:
        pass
    prompt = await callback.message.answer(
        "🗓 Создание слота\n\nШаг 1 из 4: Выберите услугу",
        reply_markup=provider_service_select_keyboard(services),
    )
    await remember_prompt(prompt, state)
    await callback.answer()


@router.callback_query(ProviderStates.slot_create_service, F.data.startswith("provider:slot:service:"))
async def on_slot_service_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, _, service_id = callback.data.split(":")
    logger.info("provider.schedule: slot_service_chosen user=%s service_id=%s", callback.from_user.id, service_id)
    data = await state.get_data()
    services_cache = data.get("slot_services") or []
    service_name = next((s.get("name") for s in services_cache if s.get("id") == service_id), "")
    default_duration = next((s.get("duration") for s in services_cache if s.get("id") == service_id), 60)
    await state.update_data(
        pending_slot={"service_id": service_id, "service_name": service_name, "default_duration": default_duration}
    )
    await state.set_state(ProviderStates.slot_create_date)
    await clear_prev_prompt(callback.message, state)
    try:
        await callback.message.delete()
    except Exception:
        pass
    prompt = await callback.message.answer(
        f"🗓 Создание слота: {service_name}\n\nШаг 2 из 4: Укажите дату\n"
        "Например: 2025-12-20, 20.12.2025 или 20.12",
    )
    await remember_prompt(prompt, state)
    await callback.answer()


@router.message(ProviderStates.slot_create_date)
async def handle_slot_date(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    date_obj = parse_date_input(text)
    if not date_obj:
        await message.answer("Не понял дату. Примеры: 2025-12-20 или 20.12")
        return

    data = await state.get_data()
    pending = data.get("pending_slot") or {}
    date_str = date_obj.strftime('%d.%m.%Y')
    pending.update({"date": date_str})
    await state.update_data(pending_slot=pending)
    await state.set_state(ProviderStates.slot_create_time)
    await clear_prev_prompt(message, state)
    try:
        await message.delete()
    except Exception:
        pass
    prompt = await message.answer(
        f"🗓 Создание слота: {pending.get('service_name', '')}\n📅 Дата: {date_str}\n\nШаг 3 из 4: Укажите время\n"
        "Например: 10:00, 14:00 или 9:30\n"
        "Можете указать часовой пояс: 10:00+3",
    )
    await remember_prompt(prompt, state)


@router.message(ProviderStates.slot_create_time)
async def handle_slot_time(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    data = await state.get_data()
    default_offset = data.get("tz_offset_min", 180)
    time_obj, offset_min = parse_time_with_offset(text, default_offset_min=default_offset)
    if not time_obj:
        await message.answer("Не понял время. Примеры: 10:00, 10-30, 1030, 10, 10:00 мск")
        return

    pending = data.get("pending_slot") or {}
    if not pending.get("date"):
        await message.answer("Сначала укажите дату (шаг 1). Начните заново через меню.")
        await state.set_state(ProviderStates.schedule_dashboard)
        return

    time_str = time_obj.strftime("%H:%M")
    pending.update({"time": time_str, "tz_offset_min": offset_min})
    await state.update_data(pending_slot=pending, tz_offset_min=offset_min)
    await state.set_state(ProviderStates.slot_create_duration)
    suggested = pending.get("default_duration") or 60
    await clear_prev_prompt(message, state)
    try:
        await message.delete()
    except Exception:
        pass
    prompt = await message.answer(
        f"🗓 Создание слота: {pending.get('service_name', '')}\n"
        f"📅 {pending.get('date', '')} в {time_str}\n\nШаг 4 из 4: Длительность приёма\n"
        f"Рекомендуемая: {suggested} минут (10–480)",
    )
    await remember_prompt(prompt, state)


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
    date_obj = parse_date_input(date_part)
    time_obj = parse_time_input(time_part)
    if not date_obj or not time_obj:
        await message.answer("Не удалось разобрать дату и время, укажите заново через меню.")
        await state.set_state(ProviderStates.schedule_dashboard)
        return

    start_local = datetime.combine(date_obj, time_obj, tzinfo=tzinfo_local)
    start_dt = start_local.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if start_dt < now_utc:
        logger.info(
            "provider.schedule: reject past slot user=%s provider_id=%s start=%s now=%s",
            message.from_user.id,
            provider_id,
            start_dt,
            now_utc,
        )
        await message.answer("Нельзя создавать слот в прошлом. Укажите дату/время в будущем.")
        await state.set_state(ProviderStates.schedule_dashboard)
        return

    end_local = start_local + timedelta(minutes=duration)
    pretty_start_local = start_local.strftime("%d.%m %H:%M")
    pretty_end_local = end_local.strftime("%H:%M")
    tz_label = fmt_offset(tz_offset)

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
        "provider.schedule: handle_add_slot user=%s provider_id=%s service_id=%s start=%s duration=%s",
        message.from_user.id,
        provider_id,
        service_id,
        start_dt.isoformat(),
        duration,
    )
    await clear_prev_prompt(message, state)
    try:
        await message.delete()
    except Exception:
        pass
    prompt = await message.answer(
        f"✅ Проверьте данные слота\n\n"
        f"📋 Услуга: {service_name}\n"
        f"📅 Дата и время: {pretty_start_local} ({tz_label})\n"
        f"⏱ Длительность: {duration} мин\n"
        f"🕒 Окончание: {pretty_end_local}",
        reply_markup=provider_add_slot_confirm(f"{pretty_start_local} ({duration} мин)"),
    )
    await remember_prompt(prompt, state)
    await state.set_state(ProviderStates.slot_create)


@router.callback_query(F.data.startswith("provider:slot:delete:"))
async def delete_slot(callback: CallbackQuery, state: FSMContext):
    _, _, _, slot_id = callback.data.split(":")
    data = await state.get_data()
    provider_id = data.get("provider_id")
    user_id = callback.from_user.id
    tz_offset_min = data.get("tz_offset_min", 180)
    
    if not provider_id:
        await callback.answer("Нет provider_id", show_alert=True)
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    logger.info(
        "provider.schedule: delete_slot user=%s provider_id=%s slot_id=%s corr_id=%s",
        user_id,
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
            "provider.schedule: delete_slot failed user=%s provider_id=%s slot_id=%s corr_id=%s",
            user_id,
            provider_id,
            slot_id,
            corr_id,
        )
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await callback.answer("Слот удалён ✓")
    
    # Возвращаемся к списку управления слотами
    now = datetime.now(timezone.utc)
    page_size = 10
    
    # Получаем текущие услуги провайдера для фильтрации
    current_service_ids = data.get("current_service_ids", [])
    current_service_set = set(current_service_ids) if current_service_ids else set()
    
    try:
        all_slots, _ = await cal_svc.list_provider_slots(
            stub,
            provider_id=provider_id,
            from_dt=now,
            to_dt=now + timedelta(days=365),
            include_bookings=True,
            page=1,
            page_size=1000,
            metadata=build_metadata(new_corr_id()),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError:
        all_slots = []
    
    # Фильтруем слоты по текущим услугам
    if current_service_set:
        filtered_slots = [ps for ps in all_slots if ps.slot.service_id in current_service_set]
    else:
        filtered_slots = all_slots
    
    if not filtered_slots:
        await callback.message.edit_text(
            "Все слоты удалены.\n\nГлавное меню:",
            reply_markup=provider_main_menu_keyboard()
        )
        return
    
    total_count = len(filtered_slots)
    slots = filtered_slots[:page_size]
    has_next = total_count > page_size
    
    await callback.message.edit_text(
        "🗂 Выберите слот для управления:\n\n🟢 — свободно\n🔴 — забронировано",
        reply_markup=provider_slots_list_keyboard(slots, tz_offset_min, page=1, has_prev=False, has_next=has_next)
    )


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
        "provider.schedule: confirm_slot_create user=%s provider_id=%s service_id=%s start=%s duration=%s corr_id=%s",
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
            "provider.schedule: confirm_slot_create failed user=%s provider_id=%s service_id=%s corr_id=%s",
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
    await clear_prev_prompt(callback.message, state)
    try:
        await callback.message.delete()
    except Exception:
        pass
    success_msg = await callback.message.answer(f"✅ Слот создан: {pretty}")
    await _show_schedule(callback.message, state)
    await callback.answer()
    # Удаляем сообщение об успехе через 3 секунды
    try:
        import asyncio
        await asyncio.sleep(3)
        await success_msg.delete()
    except Exception:
        pass


@router.callback_query(F.data == "provider:slot:create:cancel")
async def cancel_slot_create(callback: CallbackQuery, state: FSMContext):
    await state.update_data(pending_slot=None)
    await state.set_state(ProviderStates.schedule_dashboard)
    await clear_prev_prompt(callback.message, state)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Создание слота отменено", show_alert=True)


@router.callback_query(F.data.startswith("provider:slot:edit:"))
async def edit_slot_blocked(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Изменение слота не реализовано в текущей версии.")
    await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
    await callback.answer()


# ----- Week creation -----


@router.callback_query(F.data == "provider:slot:add_week")
async def start_add_week(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    provider_id = await _get_provider_id(state, user_id, callback.message.bot)
    if not provider_id:
        await callback.message.edit_text("Нет provider_id, выберите роль представителя /start")
        await callback.answer()
        return

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    logger.info("provider.schedule: start_add_week user=%s corr_id=%s", callback.from_user.id, corr_id)
    try:
        _, services = await cal_svc.list_provider_services(
            clients.calendar_stub(),
            provider_id=provider_id,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        logger.exception("provider.schedule: list_services failed corr_id=%s", corr_id)
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    if not services:
        await callback.message.edit_text(
            "Для вашего профиля пока нет услуг. Добавьте услугу и повторите."
        )
        await callback.message.answer("Главное меню представителя:", reply_markup=provider_main_menu_keyboard())
        await callback.answer()
        return

    await state.update_data(
        pending_week=None,
        week_services=[{"id": s.id, "name": s.name, "duration": s.default_duration_min} for s in services],
    )
    await state.set_state(ProviderStates.week_create_service)
    await clear_prev_prompt(callback.message, state)
    try:
        await callback.message.delete()
    except Exception:
        pass
    prompt = await callback.message.answer(
        "📅 Создание недели слотов\n\nШаг 1 из 5: Выберите услугу",
        reply_markup=provider_service_select_keyboard(services),
    )
    await remember_prompt(prompt, state)
    await callback.answer()


@router.callback_query(ProviderStates.week_create_service, F.data.startswith("provider:slot:service:"))
async def on_week_service_chosen(callback: CallbackQuery, state: FSMContext):
    _, _, _, service_id = callback.data.split(":")
    logger.info("provider.schedule: week_service_chosen user=%s service_id=%s", callback.from_user.id, service_id)
    data = await state.get_data()
    services_cache = data.get("week_services") or []
    service_name = next((s.get("name") for s in services_cache if s.get("id") == service_id), "")
    default_duration = next((s.get("duration") for s in services_cache if s.get("id") == service_id), 60)
    await state.update_data(
        pending_week={"service_id": service_id, "service_name": service_name, "default_duration": default_duration},
        week_days=[],
    )
    await state.set_state(ProviderStates.week_create_days)
    await clear_prev_prompt(callback.message, state)
    try:
        await callback.message.delete()
    except Exception:
        pass
    prompt = await callback.message.answer(
        f"📅 Создание недели слотов: {service_name}\n\nШаг 2 из 5: Выберите дни недели",
        reply_markup=provider_week_days_keyboard(set()),
    )
    await remember_prompt(prompt, state)
    await callback.answer()


@router.callback_query(ProviderStates.week_create_days, F.data.startswith("week:day:"))
async def on_week_days_chosen(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[2]
    data = await state.get_data()
    selected: set[int] = set(data.get("week_days") or [])

    if action == "done":
        if not selected:
            await callback.answer("Нужно выбрать хотя бы один день", show_alert=True)
            return
        pending = data.get("pending_week") or {}
        pending.update({"days": list(selected)})
        await state.update_data(pending_week=pending, week_days=list(selected))
        await state.set_state(ProviderStates.week_create_times)
        await clear_prev_prompt(callback.message, state)
        try:
            await callback.message.delete()
        except Exception:
            pass
        prompt = await callback.message.answer(
            f"📅 Создание недели: {pending.get('service_name', '')}\n"
            f"🗓 Дни: {fmt_weekday_set(selected)}\n\nШаг 3 из 5: Укажите время слотов\n"
            "Например: 10:00, 11:30, 14:00",
        )
        await remember_prompt(prompt, state)
        await callback.answer()
        return

    if action == "cancel":
        await state.update_data(pending_week=None, week_days=[])
        await state.set_state(ProviderStates.schedule_dashboard)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer("Создание недели отменено", show_alert=True)
        return

    try:
        day_idx = int(action)
    except ValueError:
        await callback.answer()
        return

    if day_idx in selected:
        selected.remove(day_idx)
    else:
        selected.add(day_idx)

    await state.update_data(week_days=list(selected))
    await callback.message.edit_reply_markup(reply_markup=provider_week_days_keyboard(selected))
    await callback.answer()


@router.message(ProviderStates.week_create_times)
async def on_week_times(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    data = await state.get_data()
    default_offset = data.get("tz_offset_min", 180)
    times_list = parse_time_list(text)
    if not times_list:
        await message.answer("Не понял времена. Пример: 10:00, 11:30, 14:00")
        return
    times = [t.strftime("%H:%M") for t in times_list]
    pending = data.get("pending_week") or {}
    pending.update({"times": times, "tz_offset_min": default_offset})
    await state.update_data(pending_week=pending, tz_offset_min=default_offset)
    await state.set_state(ProviderStates.week_create_span)
    await clear_prev_prompt(message, state)
    try:
        await message.delete()
    except Exception:
        pass
    prompt = await message.answer(
        f"📅 Создание недели: {pending.get('service_name', '')}\n"
        f"🗓 Дни: {fmt_weekday_set(set(pending.get('days', [])))}\n"
        f"🕒 Время: {fmt_times_list(times)}\n\nШаг 4 из 5: Период создания\n"
        "Введите количество дней вперёд (1–90)",
    )
    await remember_prompt(prompt, state)


@router.message(ProviderStates.week_create_span)
async def on_week_span(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    try:
        days = int(text)
    except ValueError:
        await message.answer("Введите число дней, пример: 14")
        return
    if days < 1 or days > 90:
        await message.answer("Можно создать слоты на период от 1 до 90 дней")
        return
    data = await state.get_data()
    pending = data.get("pending_week") or {}
    pending.update({"days_ahead": days})
    await state.update_data(pending_week=pending)
    await state.set_state(ProviderStates.week_create_duration)
    suggested = pending.get("default_duration") or 60
    await clear_prev_prompt(message, state)
    try:
        await message.delete()
    except Exception:
        pass
    prompt = await message.answer(
        f"📅 Создание недели: {pending.get('service_name', '')}\n"
        f"🗓 Дни: {fmt_weekday_set(set(pending.get('days', [])))}\n"
        f"🕒 Время: {fmt_times_list(pending.get('times', []))}\n"
        f"📆 Период: {days} дней\n\nШаг 5 из 5: Длительность приёма\n"
        f"Рекомендуемая: {suggested} минут (10–480)",
    )
    await remember_prompt(prompt, state)


@router.message(ProviderStates.week_create_duration)
async def on_week_duration(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    try:
        duration = int(text)
    except ValueError:
        await message.answer("Длительность должна быть числом, пример: 60")
        return
    if duration < 10 or duration > 480:
        await message.answer("Укажите длительность от 10 до 480 минут")
        return

    data = await state.get_data()
    pending = data.get("pending_week") or {}
    service_id = pending.get("service_id")
    if not service_id:
        await message.answer("Нет выбранной услуги, начните заново через меню.")
        await state.set_state(ProviderStates.schedule_dashboard)
        return
    days = pending.get("days") or []
    times = pending.get("times") or []
    days_ahead = pending.get("days_ahead") or 0
    if not days or not times or not days_ahead:
        await message.answer("Не хватает выбранных дней/времени/периода. Начните заново через меню.")
        await state.set_state(ProviderStates.schedule_dashboard)
        return

    tz_offset = pending.get("tz_offset_min", data.get("tz_offset_min", 180))
    tz_label = fmt_offset(tz_offset)
    await state.update_data(pending_week={**pending, "duration": duration, "tz_offset_min": tz_offset})
    await state.set_state(ProviderStates.week_create)
    await clear_prev_prompt(message, state)
    try:
        await message.delete()
    except Exception:
        pass
    times_pretty = fmt_times_list(times)
    days_pretty = fmt_weekday_set(set(days))
    prompt = await message.answer(
        "✅ Проверьте параметры недели слотов\n\n"
        f"📋 Услуга: {pending.get('service_name', '')}\n"
        f"🗓 Дни: {days_pretty}\n"
        f"🕒 Время: {times_pretty} ({tz_label})\n"
        f"📆 Период: {days_ahead} дней\n"
        f"⏱ Длительность: {duration} мин",
        reply_markup=provider_week_confirm_keyboard(),
    )
    await remember_prompt(prompt, state)


@router.callback_query(F.data == "week:create:cancel")
async def cancel_week_create(callback: CallbackQuery, state: FSMContext):
    await state.update_data(pending_week=None)
    await state.set_state(ProviderStates.schedule_dashboard)
    await clear_prev_prompt(callback.message, state)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Создание недели отменено", show_alert=True)


@router.callback_query(F.data == "week:create:confirm")
async def confirm_week_create(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pending = data.get("pending_week") or {}
    provider_id = data.get("provider_id")
    service_id = pending.get("service_id")
    days = pending.get("days") or []
    times = pending.get("times") or []
    days_ahead = pending.get("days_ahead")
    duration = pending.get("duration")
    tz_offset = pending.get("tz_offset_min", data.get("tz_offset_min", 180))
    if not provider_id or not service_id or not days or not times or not days_ahead or not duration:
        await callback.answer("Не хватает данных для создания", show_alert=True)
        return

    tzinfo_local = timezone(timedelta(minutes=tz_offset))
    now_local = datetime.now(tzinfo_local)
    today_weekday = now_local.weekday()
    # Если выбран сегодняшний день и все указанные времена уже прошли — сдвигаем старт на завтра
    future_today = today_weekday in days and any(parse_time_input(t) and parse_time_input(t) > now_local.time() for t in times)
    date_from = now_local.date() if not (today_weekday in days and not future_today) else (now_local.date() + timedelta(days=1))
    total_days = int(days_ahead)
    date_to = date_from + timedelta(days=total_days)
    tz_label = fmt_offset(tz_offset)
    ranges = []
    for t in times:
        try:
            t_obj = parse_time_input(t)
        except Exception:
            t_obj = None
        if not t_obj:
            await callback.answer("Не удалось разобрать время", show_alert=True)
            return
        ranges.append(t_obj)

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.calendar_stub()
    logger.info(
        "provider.schedule: confirm_week_create user=%s provider_id=%s service_id=%s days=%s times=%s corr_id=%s",
        callback.from_user.id,
        provider_id,
        service_id,
        days,
        times,
        corr_id,
    )

    try:
        created_slots = await cal_svc.create_week_slots(
            stub,
            provider_id=provider_id,
            service_id=service_id,
            weekday_indexes=days,
            times=ranges,
            date_from=date_from,
            date_to=date_to,
            duration_min=int(duration),
            tz_offset_min=int(tz_offset),
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError as exc:
        logger.exception(
            "provider.schedule: create_week_slots failed user=%s provider_id=%s service_id=%s corr_id=%s",
            callback.from_user.id,
            provider_id,
            service_id,
            corr_id,
        )
        await callback.message.edit_text(user_friendly_error(exc))
        await callback.answer()
        return

    await state.update_data(pending_week=None)
    await state.set_state(ProviderStates.schedule_dashboard)
    await clear_prev_prompt(callback.message, state)
    created_count = len(created_slots) if created_slots is not None else 0
    try:
        await callback.message.delete()
    except Exception:
        pass
    success_msg = await callback.message.answer(
        f"✅ Неделя слотов создана: {created_count} слотов\n\n"
        f"🗓 {fmt_weekday_set(set(days))} | {fmt_times_list(times)}\n"
        f"📆 Период: {days_ahead} дней"
    )
    await _show_schedule(callback.message, state)
    await callback.answer()
    # Удаляем сообщение об успехе через 5 секунд
    try:
        import asyncio
        await asyncio.sleep(5)
        await success_msg.delete()
    except Exception:
        pass


@router.message(ProviderStates.week_create)
async def week_create_fallback(message: Message, state: FSMContext):
    await message.answer(
        "Почти готово. Подтвердите созданное расписание кнопкой ниже или отмените.",
        reply_markup=provider_week_confirm_keyboard(),
    )


# ----- Misc -----


@router.message(ProviderStates.schedule_dashboard)
async def schedule_dashboard_text(message: Message, state: FSMContext):
    await message.answer(
        "Чтобы добавить слоты, используйте кнопки.\n"
        "• Добавить один слот\n"
        "• Создать неделю слотов\n"
        "• Обновить расписание",
        reply_markup=provider_schedule_keyboard(page=1, has_prev=False, has_next=False),
    )
