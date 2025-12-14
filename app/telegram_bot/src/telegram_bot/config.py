from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(..., alias="BOT_TOKEN")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./app.db", alias="BOT_DATABASE_URL"
    )
    log_level: str = Field(default="INFO", alias="BOT_LOG_LEVEL")

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")
