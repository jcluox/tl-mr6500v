import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from .auth import COOKIE_NAME, credentials_match
from .logging_setup import LOGGER_NAME, count_recent_errors, query_logs
from .router import RouterBusy, RouterError
from .runtime_settings import SettingsUpdate

log = logging.getLogger(f"{LOGGER_NAME}.api")

LEVELS = {"INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


class LoginBody(BaseModel):
    username: str
    password: str


def require_admin(request: Request) -> None:
    if not request.app.state.sessions.validate(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(401, "Please sign in first")


def _source(request: Request) -> str:
    return request.client.host if request.client else "unknown"


auth = APIRouter(prefix="/api/auth")
api = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


@auth.post("/login")
async def login(body: LoginBody, request: Request, response: Response):
    state = request.app.state
    source = _source(request)
    if wait := state.throttle.retry_after(source):
        raise HTTPException(429, f"Too many failed attempts. Try again in {wait} seconds")
    settings = state.settings
    if not credentials_match(settings.admin_username, settings.admin_password, body.username, body.password):
        state.throttle.failed(source)
        log.warning("Dashboard sign-in failed, source=%s", source)
        raise HTTPException(401, "Incorrect username or password")
    state.throttle.succeeded(source)
    token = state.sessions.create()
    response.set_cookie(
        COOKIE_NAME, token, max_age=int(state.sessions.ttl),
        httponly=True, samesite="strict", path="/",
    )
    log.info("Dashboard sign-in succeeded, source=%s", source)
    return {"username": settings.admin_username, "role": "admin"}


@auth.post("/logout")
async def logout(request: Request, response: Response):
    request.app.state.sessions.revoke(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@auth.get("/me", dependencies=[Depends(require_admin)])
async def me(request: Request):
    return {"username": request.app.state.settings.admin_username, "role": "admin"}


@api.get("/status")
async def status(request: Request):
    state = request.app.state
    poller = state.poller
    return {
        "server_time": time.time(),
        "ok": poller.last_error is None,
        "last_poll_at": poller.last_poll_at,
        "last_error": poller.last_error,
        "poll_interval_seconds": state.runtime.get().poll_interval_seconds,
        "providers": [p.name for p in poller.notifier.providers],
        "errors_24h": count_recent_errors(state.db),
        "messages_total": state.history.list(limit=0)["total"],
    }


@api.get("/router")
async def router_status(request: Request):
    poller = request.app.state.poller
    return {
        "state": poller.router_state,
        "checked_at": poller.router_checked_at,
        "network": poller.network,
        "network_at": poller.network_at,
        "error": poller.last_error,
    }


@api.get("/messages")
async def messages(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return request.app.state.history.list(limit, offset)


@api.delete("/messages")
async def clear_messages(request: Request):
    """Clear the Dashboard message records (database only; router messages and seen.json are not affected)."""
    deleted = request.app.state.history.clear()
    log.info("Cleared %d message records", deleted)
    return {"deleted": deleted}


@api.post("/messages/import")
async def import_messages(request: Request):
    """Import existing messages from the router inbox into the history (nothing is pushed)."""
    try:
        return {"added": await request.app.state.poller.import_existing()}
    except RouterBusy as exc:
        raise HTTPException(409, str(exc))
    except RouterError as exc:
        raise HTTPException(502, str(exc))


@api.get("/logs")
async def logs(
    request: Request,
    level: Literal["INFO", "WARNING", "ERROR"] = "INFO",
    limit: int = Query(200, ge=1, le=1000),
    before_id: int | None = None,
):
    return query_logs(request.app.state.db, LEVELS[level], limit, before_id)


@api.get("/settings")
async def get_settings(request: Request):
    return request.app.state.runtime.get()


@api.put("/settings")
async def put_settings(body: SettingsUpdate, request: Request):
    state = request.app.state
    changes = state.runtime.update(body)
    if changes:
        log.info("Settings updated: %s", ", ".join(f"{k} {old}→{new}" for k, (old, new) in changes.items()))
        state.poller.wake.set()
    return state.runtime.get()


@api.post("/poll")
async def poll_now(request: Request):
    new = await request.app.state.poller.poll_once()
    return {"new": [m.as_dict() for m in new]}
