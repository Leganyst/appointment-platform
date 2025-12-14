import os


class Settings:
    def __init__(self):
        self.bot_token = os.getenv("BOT_TOKEN", "")
        self.database_url = os.getenv(
            "BOT_DATABASE_URL",
            "postgresql+psycopg2://user:password@localhost:5432/appointment",
        )
        self.log_level = os.getenv("BOT_LOG_LEVEL", "INFO")
        self.identity_endpoint = os.getenv("IDENTITY_GRPC_ENDPOINT", "localhost:50051")
        self.calendar_endpoint = os.getenv("CALENDAR_GRPC_ENDPOINT", "localhost:50052")
        self.grpc_deadline_sec = float(os.getenv("GRPC_DEADLINE_SEC", "3.0"))
        self.grpc_tls = os.getenv("GRPC_TLS", "false").lower() == "true"
        self.grpc_root_cert = os.getenv("GRPC_ROOT_CERT", "")
