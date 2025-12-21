import logging

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
from telegram_bot.services.identity import get_profile, set_role, update_contacts
from telegram_bot.states import ClientStates, ProviderStates
from telegram_bot.utils.corr import new_corr_id
from telegram_bot.utils.contacts import parse_contact

router = Router()
logger = logging.getLogger(__name__)


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
    phone, username, err = parse_contact(contact)
    if err:
        await message.answer(err)
        return
    data = await state.get_data()
    setup = data.get("provider_setup", {})
    setup["contact_raw"] = contact
    setup["contact_phone"] = phone
    setup["contact_username"] = username
    await state.update_data(provider_setup=setup)
    await state.set_state(ProviderStates.role_setup_confirm)
    await message.answer(
        (
            "Проверьте данные роли представителя:\n"
            f"Название: {setup.get('name', '')}\n"
            f"Описание: {setup.get('description', '')}\n"
            f"Контакт: {phone or ('@' + username if username else '')}\n\n"
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

    phone, username, err = parse_contact(contact)
    if err:
        await message.answer(err)
        return

    await state.update_data(client_contact_raw=contact, client_contact_phone=phone, client_contact_username=username)
    await state.set_state(ClientStates.role_setup_confirm)
    await message.answer(
        (
            "Роль: клиент.\n"
            f"Контакт: {phone or ('@' + username if username else '')}\n\n"
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
        contact = data.get("client_contact_raw")
    elif role_code == "provider":
        contact = (data.get("provider_setup") or {}).get("contact_raw")

    logger.info(
        "role:confirm start tg=%s role=%s provider_setup=%s client_contact=%s corr=%s",
        callback.from_user.id,
        role_code,
        data.get("provider_setup"),
        data.get("client_contact_raw"),
        corr_id,
    )

    try:
        user = await set_role(
            stub,
            telegram_id=callback.from_user.id,
            role_code=role_code,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        logger.info(
            "role:set_role ok tg=%s role=%s client_id=%s provider_id=%s corr=%s",
            callback.from_user.id,
            user.role_code,
            user.client_id,
            user.provider_id,
            corr_id,
        )
    except grpc.aio.AioRpcError:
        await callback.answer("Ошибка связи с Identity", show_alert=True)
        return

    if role_code == "provider" and not user.provider_id:
        try:
            profile = await get_profile(
                stub,
                telegram_id=callback.from_user.id,
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
            if profile and profile.provider_id:
                user = profile
                logger.info(
                    "role:get_profile filled provider_id tg=%s provider_id=%s corr=%s",
                    callback.from_user.id,
                    user.provider_id,
                    corr_id,
                )
        except grpc.aio.AioRpcError:
            logger.warning("role:get_profile failed tg=%s corr=%s", callback.from_user.id, corr_id)

    # Save contacts if provided
    if contact:
        phone, username, err = parse_contact(contact)
        if err:
            await callback.answer(err, show_alert=True)
            return
        try:
            user = await update_contacts(
                stub,
                telegram_id=callback.from_user.id,
                display_name=callback.from_user.full_name,
                username=username or callback.from_user.username,
                contact_phone=phone or None,
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
            logger.info(
                "role:update_contacts ok tg=%s contact=%s role=%s corr=%s",
                callback.from_user.id,
                contact,
                user.role_code,
                corr_id,
            )
        except grpc.aio.AioRpcError:
            await callback.answer("Не удалось сохранить контакты", show_alert=True)
            return

    # Provider profile update after role + contact
    if role_code == "provider":
        setup = data.get("provider_setup", {})
        try:
            prof = await cal_svc.update_provider_profile(
                clients.calendar_stub(),
                provider_id=user.provider_id,
                display_name=setup.get("name", ""),
                description=setup.get("description", ""),
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
            logger.info(
                "role:update_provider_profile ok tg=%s provider_id=%s display_name=%s description=%s corr=%s",
                callback.from_user.id,
                user.provider_id,
                prof.display_name if hasattr(prof, "display_name") else setup.get("name", ""),
                prof.description if hasattr(prof, "description") else setup.get("description", ""),
                corr_id,
            )
        except grpc.aio.AioRpcError:
            await callback.answer("Профиль сохранить не удалось", show_alert=True)
            return

        # Create default service and link to provider so scheduling works immediately
        try:
            service = await cal_svc.create_service(
                clients.calendar_stub(),
                name=setup.get("name", ""),
                description=setup.get("description", ""),
                default_duration_min=60,
                is_active=True,
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
            _, services = await cal_svc.set_provider_services(
                clients.calendar_stub(),
                provider_id=user.provider_id,
                service_ids=[service.id],
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
            logger.info(
                "role:create_service ok tg=%s provider_id=%s service_id=%s linked_services=%s corr=%s",
                callback.from_user.id,
                user.provider_id,
                service.id,
                ",".join(s.id for s in services),
                corr_id,
            )
        except grpc.aio.AioRpcError:
            logger.warning(
                "role:create_service failed tg=%s provider_id=%s corr=%s",
                callback.from_user.id,
                user.provider_id,
                corr_id,
            )

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
        display_name=user.display_name or callback.from_user.full_name,
        username=user.username or callback.from_user.username,
        provider_setup=None,
    )
    try:
        from telegram_bot.handlers.client.utils import remember_client_chat, remember_provider_chat
        chat_id = callback.message.chat.id if callback.message and callback.message.chat else callback.from_user.id
        if user.provider_id:
            remember_provider_chat(callback.message.bot, user.provider_id, chat_id)
        if user.client_id:
            remember_client_chat(callback.message.bot, user.client_id, chat_id)
        logger.info(
            "role:cached chats tg=%s chat_id=%s client_id=%s provider_id=%s",
            callback.from_user.id,
            chat_id,
            user.client_id,
            user.provider_id,
        )
    except Exception:
        logger.exception("role:failed to cache chats tg=%s", callback.from_user.id)

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
