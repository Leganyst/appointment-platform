import asyncio
import logging

from aiogram import Dispatcher

from telegram_bot.bot import create_bot, create_dispatcher
from telegram_bot.config import Settings
from telegram_bot.db import make_engine, make_session_factory
from telegram_bot.handlers import router as handlers_router


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def setup_dispatcher(session_factory) -> Dispatcher:
    dispatcher = create_dispatcher()
    dispatcher.include_router(handlers_router)
    dispatcher.workflow_data["session_factory"] = session_factory
    return dispatcher


async def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    bot = create_bot(settings.bot_token)
    dispatcher = setup_dispatcher(session_factory)

    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
