from datetime import datetime, timedelta, timezone
import re
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

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

WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def is_active_booking(status: str) -> bool:
    status_upper = (status or "").upper()
    return status_upper not in {"CANCELLED", "BOOKING_STATUS_CANCELLED"}


def fmt_offset(offset_min: int) -> str:
    sign = "+" if offset_min >= 0 else "-"
    minutes = abs(offset_min)
    hours = minutes // 60
    mins = minutes % 60
    return f"UTC{sign}{hours:02d}:{mins:02d}"


def parse_date_input(text: str):
    candidates = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m", "%d/%m", "%d-%m"]
    clean = text.strip()
    today = datetime.utcnow().date()
    for fmt in candidates:
        try:
            dt = datetime.strptime(clean, fmt).date()
            if "%Y" not in fmt:
                dt = dt.replace(year=today.year)
                # Если дата более чем на 60 дней в прошлом, берём следующий год
                if (today - dt).days > 60:
                    dt = dt.replace(year=today.year + 1)
            return dt
        except ValueError:
            continue
    return None


def parse_time_input(text: str):
    clean = text.strip().replace(".", ":").replace("-", ":").replace(" ", ":")
    if re.fullmatch(r"\d{3,4}", clean):
        clean = f"{clean[:-2]}:{clean[-2:]}"
    if re.fullmatch(r"\d{1,2}", clean):
        clean = f"{clean}:00"
    try:
        return datetime.strptime(clean, "%H:%M").time()
    except ValueError:
        return None


def parse_tz_offset(text: str):
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


def parse_time_with_offset(text: str, default_offset_min: int = 0):
    raw = (text or "").strip().lower()
    time_part = raw
    offset_part = None
    tokens = raw.split()
    if len(tokens) >= 2:
        time_part = " ".join(tokens[:-1])
        offset_part = tokens[-1]
    if offset_part is None:
        m = re.match(r"(.+?)([+-]\d{1,2}(?::?\d{2})?)$", raw)
        if m:
            time_part = m.group(1).strip()
            offset_part = m.group(2)
    offset_min = parse_tz_offset(offset_part) if offset_part else None
    time_obj = parse_time_input(time_part)
    if time_obj and offset_min is None:
        offset_min = default_offset_min
    return time_obj, offset_min


def parse_time_list(text: str):
    raw = (text or "").replace(";", ",")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    times = []
    for part in parts:
        t = parse_time_input(part)
        if not t:
            return None
        times.append(t)
    return times if times else None


def week_range_from_date(date_obj):
    weekday = date_obj.weekday()
    monday = date_obj - timedelta(days=weekday)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def fmt_slots(slots, tz_offset_min: int = 180):
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
        "BOOKING_STATUS_CANCELLED": "отменена",
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


def fmt_weekday_set(days: set[int]) -> str:
    if not days:
        return "не выбрано"
    ordered = [d for d in range(7) if d in days]
    return ", ".join(WEEKDAY_NAMES[d] for d in ordered)


def fmt_times_list(times: list[str]) -> str:
    return ", ".join(times) if times else "—"


def fmt_bookings(bookings, slot_map: dict | None = None):
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


def clear_prev_prompt(message: Message, state: FSMContext):
    async def _clear():
        data = await state.get_data()
        prev_id = data.get("last_prompt_message_id")
        if prev_id and prev_id != message.message_id:
            try:
                await message.bot.delete_message(message.chat.id, prev_id)
            except Exception:
                pass
    return _clear()


def remember_prompt(message: Message, state: FSMContext):
    return state.update_data(last_prompt_message_id=message.message_id)
