"""Local demo identities and explicit hotel scope; not a production identity provider."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from fastapi import Cookie, Header, HTTPException


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    hotel_ids: tuple[str, ...] = ()
    synthetic: bool = True

    def permits(self, hotel_id: str) -> bool:
        return self.role == "demo_admin" or hotel_id in self.hotel_ids


# These credentials are intentionally public demo fixtures, never real accounts.
DEMO_ADMIN = Principal("hotelier", "demo_admin")
DEMO_ACCOUNTS = {
    "hotelier": ("hotelier123", DEMO_ADMIN),
    "cascade": ("cascade-demo-2026", Principal("cascade", "hotelier", ("osm-node-11866202895",))),
    "brevis": ("brevis-demo-2026", Principal("brevis", "hotelier", ("osm-way-201474330",))),
}
SESSION_COOKIE = "okno_run_session"
SESSION_SECONDS = 12 * 3600
_SESSION_KEY = secrets.token_bytes(32)  # Restart revokes every local demo session.


def authenticate(username: str, password: str) -> Principal:
    account = DEMO_ACCOUNTS.get(username)
    expected = account[0] if account else "invalid-demo-account"
    if not hmac.compare_digest(password.encode(), expected.encode()) or account is None:
        raise HTTPException(401, "Неверный логин или пароль")
    return account[1]


def issue_token(principal: Principal) -> str:
    payload = {"sub": principal.username, "role": principal.role, "hotel_ids": list(principal.hotel_ids),
               "exp": int(time.time()) + SESSION_SECONDS, "nonce": secrets.token_hex(12)}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(_SESSION_KEY, encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def _unauthorized():
    return HTTPException(401, "Войдите в демо-кабинет отельера", headers={"WWW-Authenticate": "Bearer"})


def read_token(token: str) -> Principal:
    try:
        if len(token) > 4096:
            raise ValueError
        encoded, signature = token.split(".")
        expected = hmac.new(_SESSION_KEY, encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        account = DEMO_ACCOUNTS[payload["sub"]][1]
        if (type(payload["exp"]) is not int or payload["exp"] <= time.time()
                or payload["role"] != account.role or payload["hotel_ids"] != list(account.hotel_ids)):
            raise ValueError
        return account
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise _unauthorized() from None


def optional_principal(authorization: str | None = Header(default=None),
                       run_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> Principal | None:
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _unauthorized()
        return read_token(token)
    return read_token(run_session) if run_session else None


def require_hotelier(authorization: str | None = Header(default=None)) -> Principal:
    # Private writes/cabinets require explicit Bearer even if a client manually
    # copies the read-only SSE cookie outside its browser-enforced Path.
    principal = optional_principal(authorization, None)
    if principal is None:
        raise _unauthorized()
    return principal


def authorize_hotel(principal: Principal, hotel_id: str) -> None:
    if not isinstance(principal, Principal) or not hotel_id or not principal.permits(hotel_id):
        raise HTTPException(403, "Этот отель не входит в область доступа демо-аккаунта")


def contains_runtime_secret(value, additional: tuple[str, ...] = ()) -> bool:
    """Reject configured secret echoes, including in schema-valid free text/cache.

    Values and provider content must never appear in exceptions or logs. This is
    an exact-value output guard, not a general secret detector or DLP service.
    """
    protected = [secret for secret in additional if secret]
    for name, secret in os.environ.items():
        if secret and re.search(r"(?:API_KEY|SECRET_KEY|_TOKEN|_SECRET|_PASSWORD)$", name):
            protected.append(secret)
        if secret and name in {"DB_URL", "DATABASE_URL"}:
            protected.append(secret)
            uri_credential = urlsplit(secret).password
            if uri_credential:
                protected.append(unquote(uri_credential))
    def strings(item):
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for key, content in item.items():
                yield str(key)
                yield from strings(content)
        elif isinstance(item, list | tuple):
            for content in item:
                yield from strings(content)
    return any(secret in text for text in strings(value) for secret in protected)
