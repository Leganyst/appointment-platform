from telegram_bot.dto import IdentityUser
from telegram_bot.generated import identity_pb2, identity_pb2_grpc


def _to_user(pb: identity_pb2.User) -> IdentityUser:
    return IdentityUser(
        id=pb.id,
        telegram_id=pb.telegram_id,
        display_name=pb.display_name,
        username=pb.username,
        contact_phone=pb.contact_phone,
        role_code=pb.role_code,
        client_id=pb.client_id or None,
        provider_id=pb.provider_id or None,
    )


async def register_user(
    stub: identity_pb2_grpc.IdentityServiceStub,
    *,
    telegram_id: int,
    display_name: str,
    username: str | None,
    contact_phone: str | None = None,
    metadata=None,
    timeout: float | None = None,
) -> IdentityUser:
    req = identity_pb2.RegisterUserRequest(
        telegram_id=telegram_id,
        display_name=display_name or "",
        username=username or "",
        contact_phone=contact_phone or "",
    )
    resp = await stub.RegisterUser(req, metadata=metadata, timeout=timeout)
    return _to_user(resp.user)


async def set_role(
    stub: identity_pb2_grpc.IdentityServiceStub,
    *,
    telegram_id: int,
    role_code: str,
    metadata=None,
    timeout: float | None = None,
) -> IdentityUser:
    req = identity_pb2.SetRoleRequest(telegram_id=telegram_id, role_code=role_code)
    resp = await stub.SetRole(req, metadata=metadata, timeout=timeout)
    return _to_user(resp.user)


async def update_contacts(
    stub: identity_pb2_grpc.IdentityServiceStub,
    *,
    telegram_id: int,
    display_name: str | None = None,
    username: str | None = None,
    contact_phone: str | None = None,
    metadata=None,
    timeout: float | None = None,
) -> IdentityUser:
    req = identity_pb2.UpdateContactsRequest(
        telegram_id=telegram_id,
        display_name=display_name or "",
        username=username or "",
        contact_phone=contact_phone or "",
    )
    resp = await stub.UpdateContacts(req, metadata=metadata, timeout=timeout)
    return _to_user(resp.user)


async def find_provider_by_phone(
    stub: identity_pb2_grpc.IdentityServiceStub,
    *,
    phone: str,
    metadata=None,
    timeout: float | None = None,
) -> IdentityUser | None:
    req = identity_pb2.FindProviderByPhoneRequest(phone=phone)
    resp = await stub.FindProviderByPhone(req, metadata=metadata, timeout=timeout)
    if not resp or not resp.HasField("user"):
        return None
    return _to_user(resp.user)


async def get_profile(
    stub: identity_pb2_grpc.IdentityServiceStub,
    *,
    telegram_id: int,
    metadata=None,
    timeout: float | None = None,
) -> IdentityUser:
    req = identity_pb2.GetProfileRequest(telegram_id=telegram_id)
    resp = await stub.GetProfile(req, metadata=metadata, timeout=timeout)
    return _to_user(resp.user)
