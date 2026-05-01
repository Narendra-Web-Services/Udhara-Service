from fastapi import APIRouter

from app.api.dashboard import router as dashboard_router
from app.api.finance import router as finance_router
from app.api.login import router as login_router
from app.api.profile import router as profile_router
from app.api.register import router as register_router

router = APIRouter(prefix="/api")
router.include_router(login_router, prefix="/auth")
router.include_router(register_router, prefix="/auth")
router.include_router(dashboard_router, prefix="/auth")
router.include_router(profile_router, prefix="/auth")
router.include_router(finance_router, prefix="/finance")