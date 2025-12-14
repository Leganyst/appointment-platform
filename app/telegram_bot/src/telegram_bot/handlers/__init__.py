from aiogram import Router

from . import start, role, client_flow

router = Router()
router.include_router(start.router)
router.include_router(role.router)
router.include_router(client_flow.router)
