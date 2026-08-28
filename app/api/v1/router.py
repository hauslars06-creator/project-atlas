from fastapi import APIRouter

from app.api.v1.trades import router as trades_router
from app.api.v1.statistics import router as statistics_router
from app.api.v1.risk_settings import router as risk_settings_router


router = APIRouter(
    prefix="/api/v1",
)

router.include_router(trades_router)
router.include_router(statistics_router)
router.include_router(risk_settings_router)
