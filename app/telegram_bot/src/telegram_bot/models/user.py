from sqlalchemy import BigInteger, Column, String

from telegram_bot.models.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)
    username = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    role_code = Column(String, nullable=True)
    client_id = Column(String, nullable=True)
    provider_id = Column(String, nullable=True)
