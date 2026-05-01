from fastapi import APIRouter

from app.api.dashboard import router as dashboard_router
from app.api.finance import router as finance_router
from app.api.login import router as login_router
from app.api.profile import router as profile_router
from app.api.register import router as register_router

def _register_routes(parent_router: APIRouter, *, include_in_schema: bool) -> None:
	parent_router.include_router(login_router, prefix="/auth", include_in_schema=include_in_schema)
	parent_router.include_router(register_router, prefix="/auth", include_in_schema=include_in_schema)
	parent_router.include_router(dashboard_router, prefix="/auth", include_in_schema=include_in_schema)
	parent_router.include_router(profile_router, prefix="/auth", include_in_schema=include_in_schema)
	parent_router.include_router(finance_router, prefix="/finance", include_in_schema=include_in_schema)


router = APIRouter(prefix="/api")
_register_routes(router, include_in_schema=True)

compatibility_router = APIRouter()
_register_routes(compatibility_router, include_in_schema=False)