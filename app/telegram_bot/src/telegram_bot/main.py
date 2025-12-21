import asyncio
import logging

from aiogram import Dispatcher

from telegram_bot.bot import create_bot, create_dispatcher
from telegram_bot.config import Settings
from telegram_bot.handlers import router as handlers_router
from telegram_bot.services.grpc_clients import GrpcClients


def setup_logging(level):
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def setup_dispatcher(session_factory):
    dispatcher = create_dispatcher()
    dispatcher.include_router(handlers_router)
    dispatcher.workflow_data["session_factory"] = session_factory
    return dispatcher


async def main():
    settings = Settings()
    setup_logging(settings.log_level)

    clients = GrpcClients(
        identity_endpoint=settings.identity_endpoint,
        calendar_endpoint=settings.calendar_endpoint,
        deadline=settings.grpc_deadline_sec,
        use_tls=settings.grpc_tls,
        root_cert=settings.grpc_root_cert or None,
    )

    bot = create_bot(settings.bot_token)
    dispatcher = setup_dispatcher(None)
    # Сохраняем диспетчер на боте для совместимости с существующими хендлерами
    # (они читают settings/grpc_clients через message.bot.dispatcher.workflow_data).
    bot.dispatcher = dispatcher  # type: ignore[attr-defined]
    dispatcher.workflow_data["settings"] = settings
    dispatcher.workflow_data["grpc_clients"] = clients

    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await clients.close()


if __name__ == "__main__":
    asyncio.run(main())
