from datetime import datetime, timedelta, timezone
import logging

import grpc
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from telegram_bot.services.identity import get_profile
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.utils.corr import new_corr_id
from telegram_bot.dto import SlotDTO

logger = logging.getLogger(__name__)

SLOT_CONTEXT_CACHE_KEY = "slot_context_cache"
SLOT_CONTEXT_TTL_SECONDS = 3 * 24 * 3600  # keep slot context for 3 days
SLOT_CONTEXT_MAX = 1000
PROVIDER_CHAT_MAP_KEY = "provider_chat_map"
CLIENT_CHAT_MAP_KEY = "client_chat_map"
SLOT_BLACKLIST_KEY = "slot_blacklist"


def title_with_id(name: str | None, entity_id: str) -> str:
    short = entity_id[:8]
    return name or f"ID {short}"


def is_active_booking(status: str) -> bool:
    status_upper = (status or "").upper()
    return status_upper not in {"CANCELLED", "BOOKING_STATUS_CANCELLED"}


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    try:
        now = datetime.now(dt.tzinfo or timezone.utc)
    except Exception:
        now = datetime.now(timezone.utc)
    fmt = "%d.%m.%Y %H:%M" if dt.year != now.year else "%d.%m %H:%M"
    return dt.strftime(fmt)


def slot_is_future(dt: datetime | None) -> bool:
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= datetime.now(timezone.utc)


def slot_is_bookable(slot: SlotDTO | None) -> bool:
    if not slot:
        return False
    return slot.status == "SLOT_STATUS_FREE" and slot_is_future(slot.starts_at)


async def filter_available_slots(bot, clients: GrpcClients, settings, provider_id: str, slots: list[SlotDTO]) -> list[SlotDTO]:
    """Remove slots that are not free or already have active booking (defensive against stale backend)."""
    if not slots:
        return []
    # Убираем слоты из чёрного списка (помеченные как занятые при ошибках)
    slot_ids = {s.id for s in slots if not is_slot_blacklisted(bot, s.id)}
    slots = [s for s in slots if s.id in slot_ids]
    from_dt = datetime.now(timezone.utc) - timedelta(days=180)
    to_dt = datetime.now(timezone.utc) + timedelta(days=365)
    page = 1
    page_size = 200
    slot_bookings: dict[str, bool] = {}
    # Фолбэк по списку бронирований, если статус/booking в слотах несогласован
    try:
        provider_bookings = await cal_svc.list_provider_bookings(
            clients.calendar_stub(),
            provider_id=provider_id,
            from_dt=from_dt,
            to_dt=to_dt,
            metadata=build_metadata(new_corr_id()),
            timeout=settings.grpc_deadline_sec,
        )
        booked_slot_ids = {b.slot_id for b in provider_bookings}  # любой booking блокирует слот из-за уникального индекса
        for sid in booked_slot_ids:
            slot_bookings[sid] = True
        logger.info(
            "filter_available_slots: provider=%s bookings_total=%s active_booked=%s sample=%s",
            provider_id,
            len(provider_bookings),
            len([b for b in provider_bookings if is_active_booking(b.status)]),
            list(booked_slot_ids)[:5],
        )
    except Exception:
        logger.exception("filter_available_slots: list_provider_bookings failed provider=%s", provider_id)
    while slot_ids:
        page_slots, _ = await cal_svc.list_provider_slots(
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
        for ps in page_slots:
            if ps.slot.id not in slot_ids:
                continue
            has_any_booking = ps.booking is not None
            blocked = has_any_booking or ps.slot.status != "SLOT_STATUS_FREE"
            slot_bookings[ps.slot.id] = slot_bookings.get(ps.slot.id, False) or blocked
            slot_ids.discard(ps.slot.id)
        if len(page_slots) < page_size or not slot_ids:
            break
        page += 1
    final = [s for s in slots if not slot_bookings.get(s.id, False) and slot_is_bookable(s)]
    logger.info(
        "filter_available_slots: provider=%s input=%s filtered_out=%s after_precheck=%s",
        provider_id,
        len(slots),
        len(slots) - len(final),
        len(slots) - len(final),
    )
    return final


def _get_slot_cache(bot) -> dict:
    return bot.dispatcher.workflow_data.setdefault(SLOT_CONTEXT_CACHE_KEY, {})


def _prune_slot_cache(cache: dict) -> None:
    now_ts = datetime.now(timezone.utc).timestamp()
    expired = [slot_id for slot_id, ctx in cache.items() if ctx.get("cached_at", now_ts) < now_ts - SLOT_CONTEXT_TTL_SECONDS]
    for slot_id in expired:
        cache.pop(slot_id, None)
    if len(cache) > SLOT_CONTEXT_MAX:
        to_remove = sorted(cache.items(), key=lambda kv: kv[1].get("cached_at", 0))[: len(cache) - SLOT_CONTEXT_MAX]
        for slot_id, _ in to_remove:
            cache.pop(slot_id, None)


def cache_slot_context(bot, slots: list[SlotDTO], provider_id: str, service_id: str):
    """Store minimal slot context to recover booking flow if FSM data is lost."""
    cache = _get_slot_cache(bot)
    _prune_slot_cache(cache)
    now_ts = datetime.now(timezone.utc).timestamp()
    for s in slots or []:
        if not getattr(s, "id", None):
            continue
        cache[s.id] = {
            "provider_id": provider_id,
            "service_id": service_id,
            "starts_at": s.starts_at.isoformat() if s.starts_at else None,
            "cached_at": now_ts,
        }


def get_slot_context(bot, slot_id: str) -> dict | None:
    cache = _get_slot_cache(bot)
    _prune_slot_cache(cache)
    return cache.get(slot_id)


def remember_provider_chat(bot, provider_id: str | None, telegram_id: int | None):
    if not provider_id or not telegram_id:
        return
    bot.dispatcher.workflow_data.setdefault(PROVIDER_CHAT_MAP_KEY, {})[provider_id] = telegram_id


def get_provider_chat(bot, provider_id: str | None) -> int | None:
    if not provider_id:
        return None
    return (bot.dispatcher.workflow_data.get(PROVIDER_CHAT_MAP_KEY) or {}).get(provider_id)


def remember_client_chat(bot, client_id: str | None, telegram_id: int | None):
    if not client_id or not telegram_id:
        return
    bot.dispatcher.workflow_data.setdefault(CLIENT_CHAT_MAP_KEY, {})[client_id] = telegram_id


def get_client_chat(bot, client_id: str | None) -> int | None:
    if not client_id:
        return None
    return (bot.dispatcher.workflow_data.get(CLIENT_CHAT_MAP_KEY) or {}).get(client_id)


def blacklist_slot(bot, slot_id: str):
    if not slot_id:
        return
    bot.dispatcher.workflow_data.setdefault(SLOT_BLACKLIST_KEY, set()).add(slot_id)


def is_slot_blacklisted(bot, slot_id: str) -> bool:
    if not slot_id:
        return False
    return slot_id in (bot.dispatcher.workflow_data.get(SLOT_BLACKLIST_KEY) or set())


def format_bookings_split(bookings, slot_map: dict[str, SlotDTO]):
    if not bookings:
        return "Записей нет."
    active = [b for b in bookings if is_active_booking(b.status)]
    past = [b for b in bookings if not is_active_booking(b.status)]

    def _status_text(status: str) -> str:
        return {
            "BOOKING_STATUS_PENDING": "Ожидает подтверждения",
            "BOOKING_STATUS_CONFIRMED": "Подтверждена",
            "BOOKING_STATUS_CANCELLED": "Отменена",
        }.get(status, status)

    def _line(b):
        created = fmt_dt(b.created_at)
        slot = slot_map.get(b.slot_id)
        slot_dt = fmt_dt(slot.starts_at) if slot else "—"
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


def truncate(text: str, limit: int = 120) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


async def safe_edit(message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return False
        raise


async def ensure_client_context(state: FSMContext, bot, telegram_id: int):
    data = await state.get_data()
    if data.get("client_id"):
        try:
            remember_client_chat(bot, data.get("client_id"), telegram_id)
        except Exception:
            logger.exception("ensure_client_context: failed to remember client chat tg=%s", telegram_id)
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
    try:
        remember_client_chat(bot, merged.get("client_id"), telegram_id)
    except Exception:
        logger.exception("ensure_client_context: failed to remember client chat (loaded) tg=%s", telegram_id)
    return merged


async def build_slot_map_for_bookings(clients: GrpcClients, settings, bookings) -> dict[str, SlotDTO]:
    if not bookings:
        return {}
    slot_map: dict[str, SlotDTO] = {}
    per_provider: dict[str, set[str]] = {}
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

# Circular import guard: place late to avoid import cycles
from telegram_bot.services import calendar as cal_svc  # noqa: E402
