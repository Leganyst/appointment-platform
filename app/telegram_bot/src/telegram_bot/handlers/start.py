import logging

from aiogram import Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import grpc

from telegram_bot.keyboards import start_keyboard
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.services.identity import get_profile, register_user, reset_account, set_role
from telegram_bot.states import ClientStates, ProviderStates
from telegram_bot.utils.corr import new_corr_id

router = Router()
logger = logging.getLogger(__name__)


@router.message(CommandStart(), StateFilter("*"))
async def handle_start(message: Message, state: FSMContext) -> None:
    settings = message.bot.dispatcher.workflow_data.get("settings")
    clients: GrpcClients = message.bot.dispatcher.workflow_data.get("grpc_clients")
    corr_id = new_corr_id()
    stub = clients.identity_stub()
    reset_ok = False
    try:
        # "Реальное пересоздание" аккаунта: сбрасываем роль/контакты на бэке.
        try:
            await reset_account(
                stub,
                telegram_id=message.from_user.id,
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
            reset_ok = True
            logger.info("start: account reset tg=%s corr=%s", message.from_user.id, corr_id)
        except grpc.aio.AioRpcError as e:
            if e.code() == grpc.StatusCode.UNIMPLEMENTED:
                logger.warning("start: ResetAccount not implemented on backend tg=%s", message.from_user.id)
            else:
                logger.exception("start: ResetAccount failed tg=%s", message.from_user.id)
        except Exception:
            logger.exception("start: ResetAccount failed unexpectedly tg=%s", message.from_user.id)

        user = await register_user(
            stub,
            telegram_id=message.from_user.id,
            display_name=message.from_user.full_name,
            username=message.from_user.username,
            metadata=build_metadata(corr_id),
            timeout=settings.grpc_deadline_sec,
        )
        await state.clear()
        await state.update_data(
            client_id=user.client_id,
            provider_id=user.provider_id,
            role=user.role_code,
            contact_phone=user.contact_phone,
            display_name=user.display_name,
            username=user.username,
        )
        if user.provider_id:
            from telegram_bot.handlers.client.utils import remember_provider_chat
            remember_provider_chat(message.bot, user.provider_id, message.chat.id)
        if user.client_id:
            from telegram_bot.handlers.client.utils import remember_client_chat
            remember_client_chat(message.bot, user.client_id, message.chat.id)
        logger.info(
            "start: user loaded/created tg=%s role=%s client_id=%s provider_id=%s",
            message.from_user.id,
            user.role_code,
            user.client_id,
            user.provider_id,
        )
    except grpc.aio.AioRpcError:
        await message.answer("Не удалось связаться с Identity сервисом")
        return

    # Дополнительная попытка получить provider_id, если у роли provider он пуст (гарантия id на бэке)
    if user.role_code == "provider" and not user.provider_id:
        try:
            user = await set_role(
                stub,
                telegram_id=message.from_user.id,
                role_code="provider",
                metadata=build_metadata(corr_id),
                timeout=settings.grpc_deadline_sec,
            )
            logger.info(
                "start: ensured provider via set_role tg=%s provider_id=%s",
                message.from_user.id,
                user.provider_id,
            )
        except grpc.aio.AioRpcError:
            pass
        if not user.provider_id:
            try:
                prof = await get_profile(
                    stub,
                    telegram_id=message.from_user.id,
                    metadata=build_metadata(corr_id),
                    timeout=settings.grpc_deadline_sec,
                )
                user = prof
                logger.info(
                    "start: fallback get_profile tg=%s provider_id=%s",
                    message.from_user.id,
                    user.provider_id,
                )
            except grpc.aio.AioRpcError:
                logger.warning("start: get_profile failed for tg=%s", message.from_user.id)

    if reset_ok:
        text = (
            "Привет! Я помогу записаться на приём.\n"
            "Аккаунт пересоздан (сброшены роль и контакты на сервере).\n"
            "Выбери роль или переходи в главное меню."
        )
    else:
        text = (
            "Привет! Я помогу записаться на приём.\n"
            "Пользователь создан/обновлён по твоему Telegram ID.\n"
            "Выбери роль или переходи в главное меню."
        )

    await state.set_state(ProviderStates.main_menu if user.role_code == "provider" else ClientStates.welcome)
    await message.answer(text, reply_markup=start_keyboard())
