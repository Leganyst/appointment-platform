# Telegram Bot

Базовый каркас бота для appointment-platform.

## Быстрый старт
1. Создать `.env` на основе `.env.example`.
2. Установить зависимости: `pip install -r requirements.txt`.
3. Запуск: `python -m telegram_bot.main` из каталога `src` в `PYTHONPATH` или `python -m telegram_bot.main` при установленном editable/venv.

## Стек
- aiogram 3.x
- SQLAlchemy 2.x (async)
- pydantic settings
