from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings
from app.routers import issues, tasks, approvals, outlets, categories, pics, analytics, auth, audit_logs
from app.routers import assets, work_orders, notifications, vendors, training_programs, campaigns

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_DEFAULT])

app = FastAPI(
    title="RestaurantOps — Issue Core API",
    description="Backend for Issue Core: the single source of truth for all operational issues.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(issues.router)
app.include_router(tasks.router)
app.include_router(approvals.router)
app.include_router(outlets.router)
app.include_router(categories.router)
app.include_router(pics.router)
app.include_router(analytics.router)
app.include_router(audit_logs.router)
app.include_router(assets.router)
app.include_router(work_orders.router)
app.include_router(notifications.router)
app.include_router(vendors.router)
app.include_router(training_programs.router)
app.include_router(campaigns.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "environment": settings.ENVIRONMENT}
