from aiogram import Router

from . import start, role, provider_flow
from .client import booking as client_booking
from .client import bookings as client_bookings
from .client import profile as client_profile
from .client import search as client_search
from .provider import schedule

router = Router()
router.include_router(start.router)
router.include_router(role.router)
router.include_router(client_search.router)
router.include_router(client_booking.router)
router.include_router(client_bookings.router)
router.include_router(client_profile.router)
router.include_router(provider_flow.router)
router.include_router(schedule.router)
