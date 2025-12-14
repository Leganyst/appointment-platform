from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import grpc

from telegram_bot.keyboards import main_menu_keyboard, role_keyboard
from telegram_bot.services.grpc_clients import build_metadata
from telegram_bot.services.identity import set_role
from telegram_bot.states import ClientStates
from telegram_bot.utils.corr import new_corr_id

router = Router()


@router.callback_query(F.data == "role:start")
async def start_role_selection(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ClientStates.role_setup)
    await callback.message.edit_text(
        "Выберите роль для работы в системе:", reply_markup=role_keyboard()
    )
    await callback.answer()


@router.callback_query(ClientStates.role_setup, F.data.startswith("role:set:"))
async def set_client_role(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    role_code = parts[-1]

    settings = callback.message.bot.dispatcher.workflow_data.get("settings")
    clients = callback.message.bot.dispatcher.workflow_data.get("grpc_clients")
    stub = clients.identity_stub()
    corr_id = new_corr_id()
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

    await state.update_data(client_id=user.client_id)
    await state.set_state(ClientStates.main_menu)
    await callback.message.edit_text(
        "Роль сохранена. Возвращаю в главное меню.",
    )
    await callback.message.answer(
        "Главное меню:", reply_markup=main_menu_keyboard()
    )
    await callback.answer("Готово")


@router.callback_query(ClientStates.role_setup, F.data == "role:cancel")
async def cancel_role(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Отмена выбора роли.")
    await callback.answer()
