import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from telegram_bot.keyboards import main_menu_keyboard, provider_main_menu_keyboard
from telegram_bot.states import ClientStates, ProviderStates
from telegram_bot.utils.roles import format_contact, format_username, role_label
from .utils import ensure_client_context

router = Router()
logger = logging.getLogger(__name__)


def _profile_text(message: Message, data: dict) -> str:
    display_name = data.get("display_name") or message.from_user.full_name
    username = format_username(data.get("username") or message.from_user.username) or "—"
    role = role_label(data.get("role"))
    contact = format_contact(data.get("contact_phone"), data.get("username") or message.from_user.username)
    return (
        "Профиль\n"
        f"Имя: {display_name}\n"
        f"Username: {username}\n"
        f"Роль: {role}\n"
        f"Контакт: {contact}\n\n"
        "Основные разделы:\n"
        "• Поиск услуг — выбор услуги, провайдера и слота.\n"
        "• Мои записи — активные и прошедшие бронирования.\n"
        "• Профиль — контакт и роль.\n"
        "• Помощь — краткая инструкция."
    )


@router.message(F.text == "Профиль")
async def on_profile(message: Message, state: FSMContext):
    data = await ensure_client_context(state, message.bot, message.from_user.id)
    await state.set_state(ClientStates.profile_help)
    await message.answer(
        _profile_text(message, data),
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == "Помощь")
async def on_help(message: Message, state: FSMContext):
    await state.set_state(ClientStates.profile_help)
    await message.answer(
        (
            "Помощь\n"
            "• Поиск услуг — выберите услугу, затем представителя и слот.\n"
            "• Мои записи — смотрите активные и прошедшие бронирования, отменяйте активные.\n"
            "• Профиль — роль и контакт для связи.\n"
            "• Главное меню — вернуться из любого экрана.\n\n"
            "При ошибках бронирования бот покажет причину (конфликт, слот занят)."
        ),
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "menu:main")
async def on_menu_any(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    role = data.get("role")
    if role == "provider":
        await state.set_state(ProviderStates.main_menu)
        reply_markup = provider_main_menu_keyboard()
    else:
        await state.set_state(ClientStates.main_menu)
        reply_markup = main_menu_keyboard()

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Главное меню:", reply_markup=reply_markup)
    await callback.answer()
