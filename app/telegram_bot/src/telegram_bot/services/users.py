import asyncio
import uuid
from typing import Optional

from sqlalchemy import select

from telegram_bot.db import get_session
from telegram_bot.models.user import User


async def ensure_user(session_factory, *, telegram_id: int, display_name: str, username: str | None) -> User:
    def op():
        with get_session(session_factory) as session:
            user = session.execute(
                select(User).where(User.telegram_id == telegram_id)
            ).scalar_one_or_none()
            updated = False
            if user is None:
                user = User(
                    id=str(uuid.uuid4()),
                    telegram_id=telegram_id,
                    display_name=display_name,
                    username=username,
                )
                session.add(user)
                updated = True
            else:
                if display_name and user.display_name != display_name:
                    user.display_name = display_name
                    updated = True
                if username and user.username != username:
                    user.username = username
                    updated = True
            if updated:
                session.commit()
                session.refresh(user)
            return user

    return await asyncio.to_thread(op)


async def set_role(session_factory, *, telegram_id: int, role_code: str) -> User:
    def op():
        with get_session(session_factory) as session:
            user = session.execute(
                select(User).where(User.telegram_id == telegram_id)
            ).scalar_one_or_none()
            if user is None:
                raise ValueError("User must exist before setting role")
            user.role_code = role_code
            session.commit()
            session.refresh(user)
            return user

    return await asyncio.to_thread(op)


async def get_user(session_factory, *, telegram_id: int) -> Optional[User]:
    def op():
        with get_session(session_factory) as session:
            return session.execute(
                select(User).where(User.telegram_id == telegram_id)
            ).scalar_one_or_none()

    return await asyncio.to_thread(op)
