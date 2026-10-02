"""Every route must use an HTTP method the CORS middleware allows.

TestClient skips the browser's CORS preflight, so a route on a disallowed
method (e.g. PUT) passes every API test yet fails in the browser with an opaque
network error — that is how PUT /api/auth/me/whatsapp shipped broken.
"""

from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.config import settings
from app.main import app


def _allowed_methods() -> set:
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    return set(cors.kwargs["allow_methods"])


def test_every_route_method_is_cors_allowed():
    allowed = _allowed_methods() | {"HEAD", "OPTIONS"}
    offenders = [
        f"{method} {route.path}"
        for route in app.routes if isinstance(route, APIRoute)
        for method in route.methods if method not in allowed
    ]
    assert offenders == [], f"Not allowed by CORS (browser preflight will fail): {offenders}"


def test_whatsapp_number_preflight_passes(client):
    res = client.options("/api/auth/me/whatsapp", headers={
        "Origin": settings.cors_origins_list[0],
        "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert res.status_code == 200
