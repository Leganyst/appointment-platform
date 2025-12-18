from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from telegram_bot.dto import BookingDTO, ProviderDTO, ServiceDTO, SlotDTO


def start_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Выбрать роль / Настроить профиль", callback_data="role:start")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu:main")],
        ]
    )


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Поиск услуг")],
            [KeyboardButton(text="Найти провайдера по телефону")],
            [KeyboardButton(text="Мои записи"), KeyboardButton(text="Профиль")],
            [KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
    )


def role_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Клиент", callback_data="role:set:client")],
            [InlineKeyboardButton(text="Представитель услуг", callback_data="role:set:provider")],
            [InlineKeyboardButton(text="Назад", callback_data="role:cancel")],
        ]
    )


def role_confirm_keyboard(role_code: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data=f"role:confirm:{role_code}")],
            [InlineKeyboardButton(text="Назад", callback_data="role:cancel")],
        ]
    )


def service_search_keyboard(services: list[ServiceDTO], page: int, has_prev: bool, has_next: bool):
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"service:choose:{s.id}")]
        for s in services
    ]
    nav_row = []
    if has_prev:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"service:page:{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр. {page}", callback_data="noop"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"service:page:{page+1}"))
    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="В главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def provider_keyboard(providers: list[ProviderDTO], page: int, has_prev: bool, has_next: bool):
    buttons = [
        [InlineKeyboardButton(text=p.display_name or f"ID {p.id[:8]}", callback_data=f"provider:choose:{p.id}")]
        for p in providers
    ]
    nav_row = []
    if has_prev:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"provider:page:{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр. {page}", callback_data="noop"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"provider:page:{page+1}"))
    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="В главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def services_for_provider_keyboard(provider_id: str, services: list[ServiceDTO]):
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"provider_service:choose:{s.id}")]
        for s in services[:20]
    ]
    buttons.append([InlineKeyboardButton(text="В главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def slots_keyboard(service_id: str, provider_id: str, slots: list[SlotDTO]):
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{s.starts_at.strftime('%d.%m %H:%M')}",
                callback_data=f"slot:choose:{s.id}",
            )
        ]
        for s in slots[:15]
    ]
    buttons.append(
        [InlineKeyboardButton(text="Назад к выбору представителя", callback_data=f"provider:back:{service_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def booking_confirm_keyboard(slot_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data=f"booking:confirm:{slot_id}")],
            [InlineKeyboardButton(text="Отменить", callback_data=f"booking:cancel:{slot_id}")],
        ]
    )


def booking_result_keyboard(success: bool):
    buttons = [
        [InlineKeyboardButton(text="Мои записи", callback_data="bookings:mine")],
        [InlineKeyboardButton(text="Главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def my_bookings_keyboard(bookings: list[BookingDTO], cancellable_ids: set[str]):
    buttons: list[list[InlineKeyboardButton]] = []
    for b in bookings[:20]:
        title = f"{b.service_name or b.service_id} @ {b.provider_name or b.provider_id}"
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"booking:detail:{b.id}")])
        if b.id in cancellable_ids:
            buttons.append([InlineKeyboardButton(text="Отменить", callback_data=f"booking:cancel_active:{b.id}")])
    buttons.append([InlineKeyboardButton(text="В главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def booking_details_keyboard(booking_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отменить эту запись", callback_data=f"booking:cancel_active:{booking_id}")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="bookings:mine")],
        ]
    )


def provider_bookings_keyboard(bookings: list[BookingDTO], cancellable_ids: set[str]):
    buttons: list[list[InlineKeyboardButton]] = []
    for b in bookings[:20]:
        if b.id in cancellable_ids:
            title = f"Отменить {b.service_name or b.service_id}"
            buttons.append([InlineKeyboardButton(text=title, callback_data=f"provider:booking:cancel:{b.id}")])
    buttons.append([InlineKeyboardButton(text="В главное меню", callback_data="provider:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Provider-specific keyboards


def provider_main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Управление расписанием")],
            [KeyboardButton(text="Мои записи (провайдер)")],
            [KeyboardButton(text="Профиль (провайдер)")],
        ],
        resize_keyboard=True,
    )


def provider_schedule_keyboard(page: int, has_prev: bool, has_next: bool):
    nav_row = []
    if has_prev:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"provider:slot:page:{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр. {page}", callback_data="noop"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"provider:slot:page:{page+1}"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить слот", callback_data="provider:slot:add")],
            [InlineKeyboardButton(text="Добавить неделю слотов", callback_data="provider:slot:add_week")],
            nav_row,
            [InlineKeyboardButton(text="Обновить список", callback_data="provider:slot:refresh")],
            [InlineKeyboardButton(text="В главное меню", callback_data="provider:menu")],
        ]
    )


def provider_slots_actions(slot_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить", callback_data=f"provider:slot:edit:{slot_id}")],
            [InlineKeyboardButton(text="Удалить", callback_data=f"provider:slot:delete:{slot_id}")],
            [InlineKeyboardButton(text="Назад", callback_data="provider:slot:list")],
        ]
    )


def provider_add_slot_confirm(slot_repr: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data="provider:slot:create:confirm")],
            [InlineKeyboardButton(text="Отменить", callback_data="provider:slot:create:cancel")],
        ]
    )


def provider_week_days_keyboard(selected: set[int]):
    labels = [
        (0, "Пн"),
        (1, "Вт"),
        (2, "Ср"),
        (3, "Чт"),
        (4, "Пт"),
        (5, "Сб"),
        (6, "Вс"),
    ]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for idx, title in labels:
        mark = "✅" if idx in selected else "▫️"
        row.append(InlineKeyboardButton(text=f"{mark} {title}", callback_data=f"week:day:{idx}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Готово", callback_data="week:day:done")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="week:day:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def provider_week_confirm_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать неделю слотов", callback_data="week:create:confirm")],
            [InlineKeyboardButton(text="Отмена", callback_data="week:create:cancel")],
        ]
    )


def provider_service_select_keyboard(services: list[ServiceDTO]):
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"provider:slot:service:{s.id}")]
        for s in services[:50]
    ]
    buttons.append([InlineKeyboardButton(text="В меню", callback_data="provider:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_result_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Вернуться к моим записям", callback_data="bookings:mine")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu:main")],
        ]
    )
