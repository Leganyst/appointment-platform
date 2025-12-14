from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import grpc

from telegram_bot.keyboards import (
    main_menu_keyboard,
    provider_main_menu_keyboard,
    role_confirm_keyboard,
    role_keyboard,
)
from telegram_bot.services import calendar as cal_svc
from telegram_bot.services.grpc_clients import build_metadata
from telegram_bot.services.identity import set_role, update_contacts
from telegram_bot.states import ClientStates, ProviderStates
from telegram_bot.utils.corr import new_corr_id

router = Router()


@router.callback_query(F.data == "role:start")
async def start_role_selection(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ClientStates.role_setup)
    await callback.message.edit_text(
        (
            "Выберите роль для работы в системе.\n\n"
            "Клиент — запись на услуги, просмотр своих записей, поиск провайдеров.\n"
            "Представитель услуг — управление расписанием и записями клиентов."
        ),
        reply_markup=role_keyboard(),
    )
    await callback.answer()


@router.callback_query(ClientStates.role_setup, F.data.startswith("role:set:"))
async def set_client_role(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    role_code = parts[-1]

    await state.update_data(role=role_code)

    if role_code == "provider":
        await state.update_data(provider_setup={})
        await state.set_state(ProviderStates.role_setup_name)
        await callback.message.edit_text(
            (
                "Роль: представитель услуг.\n"
                "Заполните профиль для каталога: введите название услуги/профиля (как в каталоге)."
            ),
        )
        await callback.answer()
        return

    # Client branch: ask contact, then confirm
    await state.set_state(ClientStates.role_setup_contact)
    await callback.message.edit_text(
        (
            "Роль: клиент.\n"
            "Введите контакт для связи (телефон или @username). После ввода подтвердим сохранение."
        )
    )
    await callback.answer()


@router.message(ProviderStates.role_setup_name)
async def provider_setup_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Введите название услуги/профиля.")
        return
    data = await state.get_data()
    setup = data.get("provider_setup", {})
    setup["name"] = name
    await state.update_data(provider_setup=setup)
    await state.set_state(ProviderStates.role_setup_description)
    await message.answer("Опишите услугу/профиль (кратко):")


@router.message(ProviderStates.role_setup_description)
async def provider_setup_description(message: Message, state: FSMContext):
    desc = (message.text or "").strip()
    if not desc:
        await message.answer("Введите описание (можно кратко).")
        return
    data = await state.get_data()
    setup = data.get("provider_setup", {})
    setup["description"] = desc
    await state.update_data(provider_setup=setup)
    await state.set_state(ProviderStates.role_setup_contact)
    await message.answer("Укажите контакт (телефон или @username):")


@router.message(ProviderStates.role_setup_contact)
async def provider_setup_contact(message: Message, state: FSMContext):
    contact = (message.text or "").strip()
    if not contact:
        await message.answer("Введите контакт.")
        return
    data = await state.get_data()
    setup = data.get("provider_setup", {})
    setup["contact"] = contact
    await state.update_data(provider_setup=setup)
    await state.set_state(ProviderStates.role_setup_confirm)
    await message.answer(
        (
            "Проверьте данные роли представителя:\n"
            f"Название: {setup.get('name', '')}\n"
            f"Описание: {setup.get('description', '')}\n"
            f"Контакт: {contact}\n\n"
            "Сохранить роль и профиль?"
        ),
        reply_markup=role_confirm_keyboard("provider"),
    )


@router.message(ClientStates.role_setup_contact)
async def client_role_contact(message: Message, state: FSMContext):
    contact = (message.text or "").strip()
    if not contact:
        await message.answer("Введите контакт (телефон или @username).")
        return

    await state.update_data(client_contact=contact)
    await state.set_state(ClientStates.role_setup_confirm)
    await message.answer(
        (
            "Роль: клиент.\n"
            f"Контакт: {contact}\n\n"
            "Сохранить роль и контакт?"
        ),
        reply_markup=role_confirm_keyboard("client"),
    )


@router.callback_query(F.data.startswith("role:confirm:"))
async def confirm_role(callback: CallbackQuery, state: FSMContext):
    _, _, role_code = callback.data.split(":")
    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    stub = clients.identity_stub()
    corr_id = new_corr_id()
    data = await state.get_data()

    contact = None
    if role_code == "client":
        contact = data.get("client_contact")
    elif role_code == "provider":
        contact = (data.get("provider_setup") or {}).get("contact")

    try:
        user = await set_role(
            stub,
            telegram_id=callback.from_user.id,
            role_code=role_code,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
    except grpc.aio.AioRpcError:
        await callback.answer("Ошибка связи с Identity", show_alert=True)
        return

    # Save contacts if provided
    if contact:
        try:
            user = await update_contacts(
                stub,
                telegram_id=callback.from_user.id,
                display_name=callback.from_user.full_name,
                username=callback.from_user.username,
                contact_phone=contact,
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
        except grpc.aio.AioRpcError:
            await callback.answer("Не удалось сохранить контакты", show_alert=True)
            return

    # Provider profile update after role + contact
    if role_code == "provider":
        setup = data.get("provider_setup", {})
        try:
            await cal_svc.update_provider_profile(
                clients.calendar_stub(),
                provider_id=user.provider_id,
                display_name=setup.get("name", ""),
                description=setup.get("description", ""),
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
        except grpc.aio.AioRpcError:
            await callback.answer("Профиль сохранить не удалось", show_alert=True)
            return

        await state.set_state(ProviderStates.main_menu)
        reply_markup = provider_main_menu_keyboard()
        menu_text = "Профиль сохранён. Меню представителя:" 
    else:
        await state.set_state(ClientStates.main_menu)
        reply_markup = main_menu_keyboard()
        menu_text = "Роль сохранена. Главное меню:" 

    await state.update_data(
        client_id=user.client_id,
        provider_id=user.provider_id,
        role=user.role_code,
        contact_phone=user.contact_phone,
        display_name=callback.from_user.full_name,
        username=callback.from_user.username,
        provider_setup=None,
    )

    await callback.message.edit_text(menu_text)
    await callback.message.answer("Главное меню:", reply_markup=reply_markup)
    await callback.answer("Готово")


@router.callback_query(F.data == "role:cancel")
async def cancel_role(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ClientStates.role_setup)
    await callback.message.edit_text(
        (
            "Выбор роли отменён. Выберите роль для работы в системе.\n\n"
            "Клиент — запись на услуги, просмотр своих записей, поиск провайдеров.\n"
            "Представитель услуг — управление расписанием и записями клиентов."
        ),
        reply_markup=role_keyboard(),
    )
    await callback.answer()
