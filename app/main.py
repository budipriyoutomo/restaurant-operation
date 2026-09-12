import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings
from app.routers import issues, tasks, approvals, outlets, categories, pics, analytics, auth, audit_logs
from app.routers import assets, work_orders, notifications, vendors, training_programs, campaigns
from app.routers import pm_schedules, approval_policies, parts, procurement, budgets

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

logger = logging.getLogger("restaurantops")


# Middleware order matters here. add_middleware() prepends, so the CORS
# middleware below — added last — ends up outermost and wraps this one.
#
# Starlette's own 500 handler sits *above* all user middleware, so an unhandled
# exception bypasses CORSMiddleware entirely: the browser then reports a
# missing Access-Control-Allow-Origin header and hides the real error. Catching
# it here, underneath CORS, turns a server fault back into an ordinary response
# that gets the CORS headers — and a logged traceback instead of a silent one.
@app.middleware("http")
async def unhandled_exception_to_response(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


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
app.include_router(pm_schedules.router)
app.include_router(approval_policies.router)
app.include_router(parts.router)
app.include_router(procurement.pr_router)
app.include_router(procurement.po_router)
app.include_router(budgets.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "environment": settings.ENVIRONMENT}
