import grpc

from telegram_bot.generated import calendar_pb2_grpc, identity_pb2_grpc


class GrpcClients:
    def __init__(self, *, identity_endpoint: str, calendar_endpoint: str, deadline: float, use_tls: bool = False, root_cert: str | None = None):
        self.identity_endpoint = identity_endpoint
        self.calendar_endpoint = calendar_endpoint
        self.deadline = deadline
        self.use_tls = use_tls
        self.root_cert = root_cert
        self._channels: dict[str, grpc.aio.Channel] = {}

    def _channel(self, endpoint: str) -> grpc.aio.Channel:
        if endpoint in self._channels:
            return self._channels[endpoint]
        if self.use_tls:
            creds = grpc.ssl_channel_credentials(
                root_certificates=self._load_root_cert() if self.root_cert else None
            )
            channel = grpc.aio.secure_channel(endpoint, creds)
        else:
            channel = grpc.aio.insecure_channel(endpoint)
        self._channels[endpoint] = channel
        return channel

    def _load_root_cert(self) -> bytes:
        if not self.root_cert:
            return b""
        with open(self.root_cert, "rb") as f:
            return f.read()

    def identity_stub(self) -> identity_pb2_grpc.IdentityServiceStub:
        return identity_pb2_grpc.IdentityServiceStub(self._channel(self.identity_endpoint))

    def calendar_stub(self) -> calendar_pb2_grpc.CalendarServiceStub:
        return calendar_pb2_grpc.CalendarServiceStub(self._channel(self.calendar_endpoint))

    async def close(self):
        for ch in self._channels.values():
            await ch.close()
        self._channels.clear()


def build_metadata(corr_id: str) -> list[tuple[str, str]]:
    return [("x-corr-id", corr_id)]
