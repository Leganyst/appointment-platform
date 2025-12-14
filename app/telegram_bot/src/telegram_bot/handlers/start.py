from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import grpc

from telegram_bot.keyboards import main_menu_keyboard, provider_main_menu_keyboard, start_keyboard
from telegram_bot.services.grpc_clients import GrpcClients, build_metadata
from telegram_bot.services.identity import register_user
from telegram_bot.states import ClientStates, ProviderStates
from telegram_bot.utils.corr import new_corr_id

router = Router()


@router.message(CommandStart())
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
        await state.update_data(
            client_id=user.client_id,
            provider_id=user.provider_id,
            role=user.role_code,
            contact_phone=user.contact_phone,
            display_name=user.display_name,
            username=user.username,
        )
    except grpc.aio.AioRpcError:
        await message.answer("Не удалось связаться с Identity сервисом")
        return

    text = (
        "Привет! Я помогу записаться на приём.\n"
        "Идентификация по твоему Telegram ID. Пользователь создан/найден.\n"
        "Можно сразу перейти в главное меню или настроить роль."
    )

    reply_markup = start_keyboard()
    if user.role_code == "provider":
        await state.set_state(ProviderStates.main_menu)
        reply_markup = provider_main_menu_keyboard()
    else:
        await state.set_state(ClientStates.welcome)

    await message.answer(text, reply_markup=reply_markup)

