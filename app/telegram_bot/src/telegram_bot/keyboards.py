from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from telegram_bot.dto import BookingDTO, ServiceDTO, SlotDTO


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
            [KeyboardButton(text="Мои записи"), KeyboardButton(text="Профиль")],
            [KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
    )


def role_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Клиент", callback_data="role:set:client")],
            [InlineKeyboardButton(text="Назад", callback_data="role:cancel")],
        ]
    )


def service_search_keyboard(services: list[ServiceDTO]):
    buttons = [
        [InlineKeyboardButton(text=s.name, callback_data=f"service:choose:{s.id}")]
        for s in services[:15]
    ]
    buttons.append([InlineKeyboardButton(text="В главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def provider_keyboard(service_id: str):
    # TODO: заменить на реальный список провайдеров, когда появится соответствующий метод API
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Провайдер 1", callback_data=f"provider:choose:{service_id}:p1")],
            [InlineKeyboardButton(text="Провайдер 2", callback_data=f"provider:choose:{service_id}:p2")],
            [InlineKeyboardButton(text="В главное меню", callback_data="menu:main")],
        ]
    )


def slots_keyboard(service_id: str, provider_id: str, slots: list[SlotDTO]):
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{s.starts_at.strftime('%d.%m %H:%M')}",
                callback_data=f"slot:choose:{service_id}:{provider_id}:{s.id}",
            )
        ]
        for s in slots[:15]
    ]
    buttons.append(
        [InlineKeyboardButton(text="Назад к выбору представителя", callback_data=f"provider:back:{service_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def booking_confirm_keyboard(service_id: str, provider_id: str, slot_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data=f"booking:confirm:{service_id}:{provider_id}:{slot_id}")],
            [InlineKeyboardButton(text="Отменить", callback_data=f"booking:cancel:{service_id}:{provider_id}")],
        ]
    )


def booking_result_keyboard(success: bool):
    buttons = [
        [InlineKeyboardButton(text="Мои записи", callback_data="bookings:mine")],
        [InlineKeyboardButton(text="Главное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def my_bookings_keyboard(bookings: list[BookingDTO]):
    buttons: list[list[InlineKeyboardButton]] = []
    for b in bookings[:15]:
        title = f"{b.service_name or b.service_id} @ {b.provider_name or b.provider_id}"
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"booking:detail:{b.id}")])
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


def cancel_result_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Вернуться к моим записям", callback_data="bookings:mine")],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu:main")],
        ]
    )
