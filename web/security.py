"""Session-bound OAuth and CSRF protection for the dashboard."""

import hmac
import secrets
import time

from quart import abort, request, session


CSRF_SESSION_KEY = "_csrf_token"
OAUTH_STATE_SESSION_KEY = "_oauth_state"
OAUTH_STATE_TIME_SESSION_KEY = "_oauth_state_created_at"
OAUTH_STATE_TTL_SECONDS = 300
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def create_oauth_state() -> str:
    state = secrets.token_urlsafe(32)
    session[OAUTH_STATE_SESSION_KEY] = state
    session[OAUTH_STATE_TIME_SESSION_KEY] = time.time()
    return state


def consume_oauth_state(supplied_state: str | None, *, now: float | None = None) -> bool:
    expected = session.pop(OAUTH_STATE_SESSION_KEY, None)
    created_at = session.pop(OAUTH_STATE_TIME_SESSION_KEY, None)
    if not supplied_state or not expected or created_at is None:
        return False
    current_time = time.time() if now is None else now
    try:
        is_fresh = 0 <= current_time - float(created_at) <= OAUTH_STATE_TTL_SECONDS
    except (TypeError, ValueError):
        return False
    return is_fresh and hmac.compare_digest(str(expected), supplied_state)


def init_web_security(app) -> None:
    @app.context_processor
    def inject_security_helpers():
        return {"csrf_token": get_csrf_token}

    @app.before_request
    async def verify_csrf_token():
        if request.method not in UNSAFE_METHODS:
            return None

        expected = session.get(CSRF_SESSION_KEY)
        supplied = request.headers.get("X-CSRF-Token")
        if supplied is None:
            form = await request.form
            supplied = form.get("_csrf_token")

        if not expected or not supplied or not hmac.compare_digest(str(expected), str(supplied)):
            abort(403, description="CSRF token validation failed")
        return None

    @app.after_request
    async def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=()")
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
