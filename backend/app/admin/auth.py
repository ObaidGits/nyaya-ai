"""Admin authentication (Settings page access; DECISIONS.md D-080).

Credentials come only from the environment (ADMIN_USERNAME / ADMIN_PASSWORD).
Login issues a signed, expiring session cookie (HMAC-SHA256); the signing
secret is ADMIN_SESSION_SECRET when set, otherwise derived from the admin
password (documented fallback — restart-safe but set the explicit secret in
production). Cookies are HttpOnly + SameSite=Lax, and every mutating admin
request additionally requires the ``X-Nyaya-Admin: 1`` custom header, which
cross-site requests cannot send without a CORS preflight the API never
grants (CSRF defense in depth).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as py_secrets
import time

from fastapi import Request, Response

from app.core.config import Settings
from app.core.errors import AppError

COOKIE_NAME = "nyaya_admin"
SESSION_TTL_SECONDS = 8 * 3600
ADMIN_HEADER = "X-Nyaya-Admin"


class AdminDisabledError(AppError):
    status_code = 503
    code = "ADMIN_DISABLED"


class AdminUnauthorizedError(AppError):
    status_code = 401
    code = "ADMIN_UNAUTHORIZED"


def admin_enabled(settings: Settings) -> bool:
    return bool(settings.admin_username and settings.admin_password)


def _signing_secret(settings: Settings) -> bytes:
    if settings.admin_session_secret is not None:
        return settings.admin_session_secret.get_secret_value().encode()
    # Fallback: derive from the password so sessions survive restarts without
    # extra configuration. A password change invalidates all sessions.
    assert settings.admin_password is not None
    return hashlib.sha256(
        b"nyaya-admin-session:" + settings.admin_password.get_secret_value().encode()
    ).digest()


def _issue_token(settings: Settings) -> str:
    expires = int(time.time()) + SESSION_TTL_SECONDS
    nonce = py_secrets.token_hex(8)
    payload = f"{expires}.{nonce}"
    signature = hmac.new(_signing_secret(settings), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _token_valid(settings: Settings, token: str | None) -> bool:
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    payload = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(_signing_secret(settings), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[2]):
        return False
    try:
        expires = int(parts[0])
    except ValueError:
        return False
    return expires > time.time()


def verify_credentials(settings: Settings, username: str, password: str) -> bool:
    """Constant-time credential check against the env-provided admin user."""
    assert settings.admin_username is not None and settings.admin_password is not None
    user_ok = hmac.compare_digest(username.encode(), settings.admin_username.encode())
    pass_ok = hmac.compare_digest(
        password.encode(), settings.admin_password.get_secret_value().encode()
    )
    return user_ok and pass_ok


def start_session(response: Response, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        _issue_token(settings),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )


def end_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def require_admin(request: Request, mutating: bool = False) -> None:
    """Dependency guard: valid session cookie required for every admin call.

    Mutating calls additionally require the custom admin header (CSRF
    defense in depth — a cross-site form post cannot set it).
    """
    settings = request.app.state.settings
    if not admin_enabled(settings):
        raise AdminDisabledError(
            "The admin console is disabled. Set ADMIN_USERNAME and ADMIN_PASSWORD."
        )
    if not _token_valid(settings, request.cookies.get(COOKIE_NAME)):
        raise AdminUnauthorizedError("Admin authentication required.")
    if mutating and request.headers.get(ADMIN_HEADER) != "1":
        raise AdminUnauthorizedError("Missing admin request header.")


__all__ = [
    "ADMIN_HEADER",
    "AdminDisabledError",
    "AdminUnauthorizedError",
    "admin_enabled",
    "end_session",
    "require_admin",
    "start_session",
    "verify_credentials",
]
