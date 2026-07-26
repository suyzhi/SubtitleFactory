"""Loopback API authentication and origin policy."""

from __future__ import annotations

import hmac
import hashlib
import os
import secrets
import sys
import time
from urllib.parse import urlencode

from fastapi import Request
from starlette.responses import JSONResponse


DEV_TOKEN = "subtitle-factory-local-development"
API_TOKEN = os.getenv("SUBTITLE_FACTORY_API_TOKEN") or secrets.token_hex(32)
if getattr(sys, "frozen", False) and not os.getenv("SUBTITLE_FACTORY_API_TOKEN"):
    # A sidecar launched outside Tauri remains locked instead of falling back
    # to a public, predictable development credential.
    API_TOKEN = secrets.token_hex(32)
ALLOWED_ORIGINS = tuple(
    origin.strip()
    for origin in os.getenv(
        "SUBTITLE_FACTORY_ALLOWED_ORIGINS",
        "tauri://localhost,http://tauri.localhost,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
)

QUERY_TOKEN_SUFFIXES = ("/video", "/thumbnail", "/export/download")


def signed_media_url(path: str, ttl_seconds: int = 21_600) -> str:
    expires = int(time.time()) + ttl_seconds
    payload = f"{path}|{expires}".encode("utf-8")
    signature = hmac.new(API_TOKEN.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{path}?{urlencode({'expires': expires, 'signature': signature})}"


def _valid_media_signature(request: Request) -> bool:
    signed_player = request.url.path.startswith("/api/player/youtube/")
    if request.method != "GET" or (
        not request.url.path.endswith(QUERY_TOKEN_SUFFIXES) and not signed_player
    ):
        return False
    try:
        expires = int(request.query_params.get("expires", "0"))
    except ValueError:
        return False
    if expires < int(time.time()):
        return False
    payload = f"{request.url.path}|{expires}".encode("utf-8")
    expected = hmac.new(API_TOKEN.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(request.query_params.get("signature", ""), expected)


def _provided_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


async def require_loopback_session(request: Request, call_next):
    if request.method == "OPTIONS" or not request.url.path.startswith("/api"):
        return await call_next(request)
    provided = _provided_token(request)
    if not _valid_media_signature(request) and (
        not provided or not hmac.compare_digest(provided, API_TOKEN)
    ):
        error = {
            "code": "UNAUTHORIZED_LOCAL_SESSION",
            "message": "本地会话无效或已过期",
            "suggestion": "请重新启动字幕工厂",
            "details": {},
            "recoverable": True,
        }
        return JSONResponse(status_code=401, content={"error": error, "detail": error})
    return await call_next(request)
