from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class IdentityUser:
    id: str
    telegram_id: int
    display_name: str
    username: str
    contact_phone: str
    role_code: str
    client_id: Optional[str]
    provider_id: Optional[str]


@dataclass
class ServiceDTO:
    id: str
    name: str
    description: str
    default_duration_min: int
    is_active: bool


@dataclass
class ProviderDTO:
    id: str
    display_name: str
    description: str


@dataclass
class SlotDTO:
    id: str
    provider_id: str
    service_id: str
    starts_at: datetime
    ends_at: datetime
    status: str


@dataclass
class BookingDTO:
    id: str
    client_id: str
    slot_id: str
    provider_id: str
    provider_name: str
    service_id: str
    service_name: str
    status: str
    created_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    comment: str
