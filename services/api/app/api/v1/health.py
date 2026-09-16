"""Plan §81 — health status endpoint. Real-only checks: DB connectivity.
Hardware/printer/scanner/website-sync checks belong to the desktop app
(they're per-machine, not per-server) — this endpoint reports what the
*server* can actually see."""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - defensive
        checks["database"] = f"error: {exc}"
    return {"status": "ok" if all(v == "ok" for v in checks.values()) else "degraded", "checks": checks}
