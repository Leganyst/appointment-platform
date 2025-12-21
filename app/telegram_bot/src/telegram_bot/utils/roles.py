def format_username(username: str | None) -> str | None:
    if not username:
        return None
    u = username.strip()
    if not u:
        return None
    return u if u.startswith("@") else f"@{u}"


def format_contact(contact_phone: str | None, username: str | None) -> str:
    phone = (contact_phone or "").strip()
    if phone:
        return phone
    u = format_username(username)
    return u or "—"


def role_label(role_code: str | None) -> str:
    code = (role_code or "").strip().lower()
    return {
        "provider": "представитель услуг",
        "client": "клиент",
        "admin": "администратор",
    }.get(code, code or "—")

