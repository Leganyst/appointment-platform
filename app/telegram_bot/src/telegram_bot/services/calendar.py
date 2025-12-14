from datetime import datetime, timedelta, timezone

import grpc

from telegram_bot.dto import BookingDTO, ProviderDTO, ServiceDTO, SlotDTO
from telegram_bot.generated import calendar_pb2, calendar_pb2_grpc, common_pb2
from telegram_bot.utils.time import to_datetime, to_timestamp

DEFAULT_SLOTS_WINDOW_DAYS = 3


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


async def list_services(stub: calendar_pb2_grpc.CalendarServiceStub, *, metadata, timeout: float) -> list[ServiceDTO]:
    resp = await stub.ListServices(calendar_pb2.ListServicesRequest(), metadata=metadata, timeout=timeout)
    return [_to_service(s) for s in resp.services]


async def list_provider_services(
    stub: calendar_pb2_grpc.CalendarServiceStub, *, provider_id: str, metadata, timeout: float
) -> tuple[ProviderDTO, list[ServiceDTO]]:
    resp = await stub.ListProviderServices(
        calendar_pb2.ListProviderServicesRequest(provider_id=provider_id), metadata=metadata, timeout=timeout
    )
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


async def list_bookings(
    stub: calendar_pb2_grpc.CalendarServiceStub,
    *,
    client_id: str,
    metadata,
    timeout: float,
) -> list[BookingDTO]:
    req = calendar_pb2.ListBookingsRequest(client_id=client_id)
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
