import re


def normalize_phone(phone: str) -> str | None:
    """Normalize phone to digits-only, with RU-friendly rules.

    - Keeps only digits
    - If 11 digits and starts with 8 -> replace with 7
    - If 10 digits -> prefix with 7
    - Accepts 11-15 digits after normalization
    """
    raw = (phone or "").strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) < 11 or len(digits) > 15:
        return None
    return digits


_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def normalize_tg_username(value: str) -> str | None:
    """Normalize Telegram username.

    Accepts with or without leading '@'. Returns username without '@' in lower case.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("@"):
        raw = raw[1:]
    raw = raw.strip()
    if not raw:
        return None
    if not _USERNAME_RE.fullmatch(raw):
        return None
    return raw.lower()


def parse_contact(value: str) -> tuple[str | None, str | None, str | None]:
    """Parse a contact string into (phone_digits, username, error)."""
    raw = (value or "").strip()
    if not raw:
        return None, None, "Контакт пустой"
    if raw.startswith("@") or any(ch.isalpha() for ch in raw):
        username = normalize_tg_username(raw)
        if not username:
            return None, None, "Неверный @username. Формат: @username, 5–32 символа, латиница/цифры/_."
        return None, username, None
    phone = normalize_phone(raw)
    if not phone:
        return None, None, "Неверный номер телефона. Пример: +79991234567 или 89991234567."
    return phone, None, None

