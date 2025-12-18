import logging

from aiogram import Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import grpc

from telegram_bot.keyboards import start_keyboard
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.services.identity import get_profile, register_user, set_role
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
    try:
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

    text = (
        "Привет! Я помогу записаться на приём.\n"
        "Идентификация по твоему Telegram ID. Пользователь создан/найден.\n"
        "Можно сразу перейти в главное меню или настроить роль."
    )

    await state.set_state(ProviderStates.main_menu if user.role_code == "provider" else ClientStates.welcome)
    await message.answer(text, reply_markup=start_keyboard())
