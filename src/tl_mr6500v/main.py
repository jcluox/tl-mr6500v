import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .access import LanAccessMiddleware
from .alerts import ErrorAlertHandler
from .api import api, auth
from .auth import LoginThrottle, SessionStore
from .config import Settings
from .db import Database
from .history import MessageStore
from .logging_setup import LOGGER_NAME, setup_logging
from .notifier import Notifier
from .poller import Poller
from .providers import build_providers
from .router import RouterClient
from .runtime_settings import AppSettings, SettingsStore
from .state import SeenStore

STATIC_DIR = Path(__file__).parent / "static"
SECURITY_HEADERS = [
    (b"content-security-policy", b"default-src 'self'; frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"cache-control", b"no-store"),
]


def create_app(settings: Settings | None = None, router=None, providers=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.data_dir / "app.db")
        log = setup_logging(settings.log_dir, db)
        runtime = SettingsStore(db, AppSettings(poll_interval_seconds=settings.poll_interval_seconds))
        active = providers if providers is not None else build_providers(settings)
        if not active:
            log.warning("No provider is enabled (ENABLED_PROVIDERS is empty), so new messages will not be pushed")
        notifier = Notifier(active)
        log.addHandler(ErrorAlertHandler(notifier, runtime, asyncio.get_running_loop()))
        client = router or RouterClient(settings.router_host, settings.tplink_password)
        poller = Poller(
            client, notifier, SeenStore(settings.data_dir / "seen.json"), MessageStore(db), runtime,
            import_limit=settings.import_limit,
        )
        for name, value in dict(
            settings=settings, db=db, runtime=runtime, poller=poller, history=poller.history,
            sessions=SessionStore(db, settings.session_days * 86400), throttle=LoginThrottle(),
        ).items():
            setattr(app.state, name, value)
        task = asyncio.create_task(poller.run())
        log.info("Started. providers=%s, checking every %ss",
                 [p.name for p in active], runtime.get().poll_interval_seconds)
        yield
        task.cancel()
        db.close()

    app = FastAPI(title="TL-MR6500v SMS notifier", lifespan=lifespan)

    @app.get("/health")
    async def health():
        poller: Poller = app.state.poller
        # No login needed, so keep it minimal: the error text can contain the router address
        return {"ok": poller.last_error is None, "last_poll_at": poller.last_poll_at}

    app.include_router(auth)
    app.include_router(api)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS:
            response.headers.setdefault(name.decode(), value.decode())
        return response

    app.add_middleware(
        LanAccessMiddleware,
        local_networks=settings.local_networks,
        allow_lan=lambda: app.state.runtime.get().allow_lan_access,
    )
    return app


def __getattr__(name: str):
    # Create the app only for `uvicorn tl_mr6500v.main:app`; importing this module (for example in tests) does not need .env
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
