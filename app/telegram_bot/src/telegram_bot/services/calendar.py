from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram_bot.dto import BookingDTO, ProviderDTO, ProviderSlotDTO, ServiceDTO, SlotDTO
from telegram_bot.generated import calendar_pb2, calendar_pb2_grpc, common_pb2
from telegram_bot.utils.time import to_datetime, to_timestamp

DEFAULT_SLOTS_WINDOW_DAYS = 3


def _set_ts_field(field, dt) -> None:
    ts = to_timestamp(dt)
    if ts is None:
        return
    field.CopyFrom(ts)


def _to_service(pb: common_pb2.Service) -> ServiceDTO:
    return ServiceDTO(
        id=pb.id,
        name=pb.name,
        description=pb.description,
        default_duration_min=pb.default_duration_min,
        is_active=pb.is_active,
    )


def _to_provider(pb: common_pb2.Provider) -> ProviderDTO:
    return ProviderDTO(id=pb.id, display_name=pb.display_name, description=pb.description)


def _to_slot(pb: common_pb2.Slot) -> SlotDTO:
    return SlotDTO(
        id=pb.id,
        provider_id=pb.provider_id,
        service_id=pb.service_id,
        starts_at=to_datetime(pb.starts_at),
        ends_at=to_datetime(pb.ends_at),
        status=common_pb2.SlotStatus.Name(pb.status),
    )


def _to_booking(pb: common_pb2.Booking) -> BookingDTO:
    return BookingDTO(
        id=pb.id,
        client_id=pb.client_id,
        slot_id=pb.slot_id,
        provider_id=pb.provider_id,
        provider_name=pb.provider_name,
        service_id=pb.service_id,
        service_name=pb.service_name,
        status=common_pb2.BookingStatus.Name(pb.status),
        created_at=to_datetime(pb.created_at),
        cancelled_at=to_datetime(pb.cancelled_at),
        comment=pb.comment,
    )


def _to_slot_with_booking(pb: calendar_pb2.SlotWithBooking) -> ProviderSlotDTO:
    slot = _to_slot(pb.slot)
    booking = _to_booking(pb.booking) if pb.HasField("booking") else None
    return ProviderSlotDTO(slot=slot, booking=booking)


async def list_services(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    only_active: bool | None = None,
    page: int | None = None,
    page_size: int | None = None,
    metadata,
    timeout: float,
) -> tuple[list[ServiceDTO], int]:
    req = calendar_pb2.ListServicesRequest()
    if only_active is not None:
        req.only_active = only_active
    if page:
        req.page = page
    if page_size:
        req.page_size = page_size
    resp = await stub.ListServices(req, metadata=metadata, timeout=timeout)
    return [_to_service(s) for s in resp.services], resp.total_count


async def list_providers(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    service_id: str,
    page: int,
    page_size: int,
    metadata,
    timeout: float,
) -> tuple[list[ProviderDTO], int]:
    req = calendar_pb2.ListProvidersRequest(service_id=service_id, page=page, page_size=page_size)
    resp = await stub.ListProviders(req, metadata=metadata, timeout=timeout)
    return [_to_provider(p) for p in resp.providers], resp.total_count


async def list_provider_services(
    stub: calendar_pb2_grpc.CalendarServiceStub, *, provider_id: str, metadata, timeout: float
) -> tuple[ProviderDTO, list[ServiceDTO]]:
    resp = await stub.ListProviderServices(
        calendar_pb2.ListProviderServicesRequest(provider_id=provider_id), metadata=metadata, timeout=timeout
    )
    provider = _to_provider(resp.provider) if resp.HasField("provider") else ProviderDTO(provider_id, "", "")
    return provider, [_to_service(s) for s in resp.services]


async def create_service(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    name: str,
    description: str,
    default_duration_min: int,
    is_active: bool,
    metadata,
    timeout: float,
) -> ServiceDTO:
    req = calendar_pb2.CreateServiceRequest(
        name=name,
        description=description,
        default_duration_min=default_duration_min,
        is_active=is_active,
    )
    resp = await stub.CreateService(req, metadata=metadata, timeout=timeout)
    return _to_service(resp.service)


async def set_provider_services(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    provider_id: str,
    service_ids: list[str],
    metadata,
    timeout: float,
) -> tuple[ProviderDTO, list[ServiceDTO]]:
    req = calendar_pb2.SetProviderServicesRequest(provider_id=provider_id, service_ids=service_ids)
    resp = await stub.SetProviderServices(req, metadata=metadata, timeout=timeout)
    provider = _to_provider(resp.provider) if resp.HasField("provider") else ProviderDTO(provider_id, "", "")
    return provider, [_to_service(s) for s in resp.services]


async def find_free_slots(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    provider_id: str,
    service_id: str,
    from_dt: datetime | None,
    days: int,
    limit: int,
    metadata,
    timeout: float,
) -> list[SlotDTO]:
    if from_dt is None:
        from_dt = datetime.now(timezone.utc)
    start_ts = to_timestamp(from_dt)
    end_ts = to_timestamp(from_dt + timedelta(days=days))
    req = calendar_pb2.FindFreeSlotsRequest(
        provider_id=provider_id,
        service_id=service_id,
        start=start_ts,
        end=end_ts,
        limit=limit,
    )
    resp = await stub.FindFreeSlots(req, metadata=metadata, timeout=timeout)
    return [_to_slot(s) for s in resp.slots]


async def list_provider_slots(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    provider_id: str,
    from_dt: datetime,
    to_dt: datetime,
    include_bookings: bool,
    page: int,
    page_size: int,
    metadata,
    timeout: float,
) -> tuple[list[ProviderSlotDTO], int]:
    req = calendar_pb2.ListProviderSlotsRequest(
        provider_id=provider_id,
        include_bookings=include_bookings,
        page=page,
        page_size=page_size,
    )
    _set_ts_field(getattr(req, "from"), from_dt)
    _set_ts_field(getattr(req, "to"), to_dt)
    resp = await stub.ListProviderSlots(req, metadata=metadata, timeout=timeout)
    return ([_to_slot_with_booking(s) for s in resp.slots], resp.total_count)


async def create_slot(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    provider_id: str,
    service_id: str,
    start: datetime,
    duration_min: int,
    metadata,
    timeout: float,
) -> SlotDTO:
    end_dt = start + timedelta(minutes=duration_min)
    req = calendar_pb2.CreateSlotRequest(
        provider_id=provider_id,
        service_id=service_id,
        range=common_pb2.TimeRange(
            start=to_timestamp(start),
            end=to_timestamp(end_dt),
        ),
    )
    resp = await stub.CreateSlot(req, metadata=metadata, timeout=timeout)
    return _to_slot(resp.slot)


async def check_availability(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    client_id: str,
    slot_id: str,
    metadata,
    timeout: float,
) -> tuple[bool, str]:
    resp = await stub.CheckAvailability(
        calendar_pb2.CheckAvailabilityRequest(client_id=client_id, slot_id=slot_id), metadata=metadata, timeout=timeout
    )
    return resp.available, resp.reason


async def create_booking(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    client_id: str,
    slot_id: str,
    comment: str | None,
    metadata,
    timeout: float,
) -> BookingDTO:
    req = calendar_pb2.CreateBookingRequest(client_id=client_id, slot_id=slot_id, comment=comment or "")
    resp = await stub.CreateBooking(req, metadata=metadata, timeout=timeout)
    return _to_booking(resp.booking)


async def cancel_booking(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    booking_id: str,
    reason: str | None,
    metadata,
    timeout: float,
) -> BookingDTO:
    req = calendar_pb2.CancelBookingRequest(booking_id=booking_id, reason=reason or "")
    resp = await stub.CancelBooking(req, metadata=metadata, timeout=timeout)
    return _to_booking(resp.booking)


async def confirm_booking(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    booking_id: str,
    metadata,
    timeout: float,
) -> BookingDTO:
    req = calendar_pb2.ConfirmBookingRequest(booking_id=booking_id)
    resp = await stub.ConfirmBooking(req, metadata=metadata, timeout=timeout)
    return _to_booking(resp.booking)


async def list_provider_bookings(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    provider_id: str,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    metadata,
    timeout: float,
) -> list[BookingDTO]:
    now = datetime.now(timezone.utc)
    start = from_dt or now
    end = to_dt or (start + timedelta(days=30))
    req = calendar_pb2.ListProviderBookingsRequest(
        provider_id=provider_id,
        **{"from": to_timestamp(start), "to": to_timestamp(end)},
    )
    resp = await stub.ListProviderBookings(req, metadata=metadata, timeout=timeout)
    return [_to_booking(b) for b in resp.bookings]


async def delete_slot(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    slot_id: str,
    metadata,
    timeout: float,
) -> None:
    req = calendar_pb2.DeleteSlotRequest(slot_id=slot_id)
    await stub.DeleteSlot(req, metadata=metadata, timeout=timeout)


async def update_slot(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    slot_id: str,
    service_id: Optional[str],
    start: datetime,
    duration_min: int,
    status: Optional[str],
    metadata,
    timeout: float,
) -> SlotDTO:
    end_dt = start + timedelta(minutes=duration_min)
    status_enum = None
    if status:
        status_enum = getattr(common_pb2.SlotStatus, status, None)
    req = calendar_pb2.UpdateSlotRequest(
        slot_id=slot_id,
        service_id=service_id or "",
        range=common_pb2.TimeRange(start=to_timestamp(start), end=to_timestamp(end_dt)),
    )
    if status_enum is not None:
        req.status = status_enum
    resp = await stub.UpdateSlot(req, metadata=metadata, timeout=timeout)
    return _to_slot(resp.slot)


async def list_bookings(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    client_id: str,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    metadata,
    timeout: float,
) -> list[BookingDTO]:
    now = datetime.now(timezone.utc)
    start = from_dt or (now - timedelta(days=30))
    end = to_dt or (now + timedelta(days=60))
    req = calendar_pb2.ListBookingsRequest(
        client_id=client_id,
        **{"from": to_timestamp(start), "to": to_timestamp(end)},
    )
    resp = await stub.ListBookings(req, metadata=metadata, timeout=timeout)
    return [_to_booking(b) for b in resp.bookings]


async def get_booking(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    booking_id: str,
    metadata,
    timeout: float,
) -> BookingDTO:
    resp = await stub.GetBooking(calendar_pb2.GetBookingRequest(booking_id=booking_id), metadata=metadata, timeout=timeout)
    return _to_booking(resp.booking)


async def update_provider_profile(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    provider_id: str,
    display_name: str,
    description: str,
    metadata,
    timeout: float,
) -> ProviderDTO:
    req = calendar_pb2.UpdateProviderProfileRequest(
        provider_id=provider_id,
        display_name=display_name,
        description=description,
    )
    resp = await stub.UpdateProviderProfile(req, metadata=metadata, timeout=timeout)
    return _to_provider(resp.provider)
