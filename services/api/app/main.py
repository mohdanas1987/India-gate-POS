from fastapi import FastAPI

from app.core.config import get_settings
from app.api.v1 import auth, health, website_orders

settings = get_settings()

if settings.env == "production" and settings.jwt_secret == "dev-only-insecure-secret-change-me":
    raise RuntimeError(
        "Refusing to start in production with the default development JWT secret. "
        "Set IGPOS_JWT_SECRET. (This check exists because the legacy POS shipped "
        "with a hardcoded secret — see Phase 0 audit.)"
    )

app = FastAPI(title="India Gate Smarter AI POS — API", version="0.1.0")

app.include_router(health.router, prefix=settings.api_v1_prefix)
app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(website_orders.router, prefix=settings.api_v1_prefix)
