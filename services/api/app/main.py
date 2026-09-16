from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.v1 import auth, health, website_orders, products, cash, orders

settings = get_settings()

if settings.env == "production" and settings.jwt_secret == "dev-only-insecure-secret-change-me":
    raise RuntimeError(
        "Refusing to start in production with the default development JWT secret. "
        "Set IGPOS_JWT_SECRET. (This check exists because the legacy POS shipped "
        "with a hardcoded secret — see Phase 0 audit.)"
    )

app = FastAPI(title="India Gate Smarter AI POS — API", version="0.1.0")

# CORS: the legacy server.js had `app.use(cors())` (allow-all) — this was
# never ported when the backend was rewritten, which silently broke every
# browser-based client (Electron renderer, Vite dev server) until caught by
# a live Playwright smoke test (see Phase 0 audit / docs/PHASE-STATUS.md).
# Dev default is permissive; production should set IGPOS_CORS_ORIGINS to an
# explicit allowlist rather than "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix=settings.api_v1_prefix)
app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(website_orders.router, prefix=settings.api_v1_prefix)
app.include_router(products.router, prefix=settings.api_v1_prefix)
app.include_router(cash.router, prefix=settings.api_v1_prefix)
app.include_router(orders.router, prefix=settings.api_v1_prefix)
