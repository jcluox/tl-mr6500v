import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from tl_mr6500v.access import is_allowed
from tl_mr6500v.alerts import ErrorAlertHandler
from tl_mr6500v.config import Settings
from tl_mr6500v.db import Database
from tl_mr6500v.logging_setup import setup_logging
from tl_mr6500v.main import create_app
from tl_mr6500v.notifier import Notifier
from tl_mr6500v.router import PollResult, parse_network
from tl_mr6500v.runtime_settings import AppSettings, SettingsStore, SettingsUpdate

from test_core import FakeRouter, Recorder, sms

NETWORK_RAW = (
    "[2,1,0,0,0,0]0\r\nenable=1\r\nnetworkType=3\r\nconnectStatus=4\r\nsignalStrength=3\r\n"
    "roamingStatus=0\r\nsimCardNumber=886000000000\r\nsimCardImsi=466000000000000\r\n"
    "ipv4=10.0.0.2\r\ndns1v4=1.1.1.1\r\ndns2v4=8.8.8.8\r\ngatewayV4=10.0.0.1\r\n"
    "netmask=255.255.255.240\r\nipv6=\r\nifName=wwan0\r\n[error]0\r\n"
)
LOCAL = Settings(tplink_password="x", admin_password="x", _env_file=None).local_networks


@pytest.fixture
def env(tmp_path):
    router, rec = FakeRouter([[sms(2), sms(1)]]), Recorder()
    settings = Settings(
        tplink_password="router-pw", admin_password="s3cret", data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs", poll_interval_seconds=3600, _env_file=None,
    )
    app = create_app(settings, router=router, providers=[rec])
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client, router, rec


def login(client, password="s3cret"):
    return client.post("/api/auth/login", json={"username": "admin", "password": password})


def test_parse_network_exposes_only_network_fields():
    net = parse_network(NETWORK_RAW)
    assert net["connected"] and net["ipv4"] == "10.0.0.2" and net["signal"] == 3
    assert net["dns1"] == "1.1.1.1" and net["interface"] == "wwan0"
    assert "886000000000" not in str(net) and "466000000000000" not in str(net)


def test_not_connected_when_no_ip():
    assert not parse_network("connectStatus=4\r\nipv4=0.0.0.0\r\n")["connected"]
    assert not parse_network("connectStatus=0\r\nipv4=10.0.0.2\r\n")["connected"]


def test_api_requires_login(env):
    client, *_ = env
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/health").status_code == 200  # The Docker healthcheck does not need a login
    assert client.get("/").status_code == 200  # The login page is a static file


def test_login_logout_and_throttle(env):
    client, *_ = env
    assert login(client, "wrong").status_code == 401
    ok = login(client)
    assert ok.status_code == 200 and ok.json()["role"] == "admin"
    assert "httponly" in ok.headers["set-cookie"].lower()
    assert client.get("/api/status").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/status").status_code == 401

    for _ in range(5):
        login(client, "wrong")
    assert login(client).status_code == 429  # While locked out, even the correct password is rejected


def test_session_slides_and_expires(tmp_path):
    from tl_mr6500v.auth import SessionStore

    db = Database(tmp_path / "a.db")
    store = SessionStore(db, ttl_seconds=100)
    token = store.create()
    db.execute("UPDATE sessions SET expires = expires - 60")  # Less than half left -> renewed on use
    assert store.validate(token)
    assert db.query("SELECT expires - strftime('%s','now') AS left FROM sessions")[0]["left"] > 90
    db.execute("UPDATE sessions SET expires = 1")
    assert not store.validate(token)
    assert not store.validate(None) and not store.validate("garbage")


def test_settings_persist_and_validate(env, tmp_path):
    client, *_ = env
    login(client)
    assert client.get("/api/settings").json()["poll_interval_seconds"] == 3600
    resp = client.put("/api/settings", json={"poll_interval_seconds": 15, "kick_existing_login": True})
    assert resp.json()["poll_interval_seconds"] == 15 and resp.json()["kick_existing_login"]
    assert client.put("/api/settings", json={"poll_interval_seconds": 1}).status_code == 422
    assert client.put("/api/settings", json={"bogus": 1}).status_code == 422

    # Still there after reloading (stored in SQLite)
    db = Database(tmp_path / "data" / "app.db")
    reloaded = SettingsStore(db, AppSettings(poll_interval_seconds=3600)).get()
    assert reloaded.poll_interval_seconds == 15 and reloaded.kick_existing_login


def test_kick_setting_reaches_router_and_history_is_served(env):
    client, router, rec = env
    login(client)
    client.post("/api/poll")  # Make sure the first-start baseline is done (it does not matter whether the background poll or this call goes first)
    client.put("/api/settings", json={"kick_existing_login": True})
    router.pages = [[sms(3), sms(2), sms(1)]]
    router.network = {"connected": True, "ipv4": "10.0.0.2"}
    assert [m["index"] for m in client.post("/api/poll").json()["new"]] == [3]
    assert router.kick is True

    items = client.get("/api/messages").json()["items"]
    assert items and items[0]["delivered"] == {"rec": True} and items[0]["content"] == "hi"
    assert client.get("/api/router").json()["network"]["ipv4"] == "10.0.0.2"
    assert client.get("/api/status").json()["messages_total"] == len(items)


def test_logs_endpoint_filters_by_level(env):
    client, *_ = env
    login(client)
    logging.getLogger("tl_mr6500v.test").error("something broke")
    levels = {l["level"] for l in client.get("/api/logs?level=ERROR").json()}
    assert levels == {"ERROR"}
    assert any("something broke" in l["message"] for l in client.get("/api/logs").json())
    assert client.get("/api/logs?level=bogus").status_code == 422
    assert client.get("/api/status").json()["errors_24h"] >= 1


def test_lan_access_rules():
    allow = lambda host, lan: is_allowed(host, LOCAL, lan)
    assert allow("127.0.0.1", False) and allow("::1", False) and allow("::ffff:127.0.0.1", False)
    assert not allow("192.168.1.50", False)
    assert allow("192.168.1.50", True) and allow("10.1.2.3", True) and allow("fd00::1", True)
    assert not allow("8.8.8.8", True)  # Public addresses are always rejected
    assert not allow("testclient", True) and not allow(None, True)


def test_lan_middleware_blocks_and_follows_setting(tmp_path):
    settings = Settings(tplink_password="x", admin_password="s3cret", data_dir=tmp_path / "d",
                        log_dir=tmp_path / "l", _env_file=None)
    app = create_app(settings, router=FakeRouter([[sms(1)]]), providers=[])
    with TestClient(app, client=("127.0.0.1", 1)) as local, TestClient(app, client=("192.168.1.50", 1)) as lan:
        assert lan.get("/health").status_code == 403
        assert lan.get("/").status_code == 403
        login(local)
        local.put("/api/settings", json={"allow_lan_access": True})
        assert lan.get("/health").status_code == 200
        local.put("/api/settings", json={"allow_lan_access": False})
        assert lan.get("/health").status_code == 403


def test_error_alerts_respect_setting_and_dedupe(tmp_path):
    async def scenario():
        db = Database(tmp_path / "a.db")
        settings = SettingsStore(db, AppSettings())
        rec = Recorder()
        logger = setup_logging(tmp_path / "logs", db)
        handler = ErrorAlertHandler(Notifier([rec]), settings, asyncio.get_running_loop())
        logger.addHandler(handler)

        logger.error("off")
        await asyncio.sleep(0.05)
        assert rec.texts == []

        settings.update(SettingsUpdate(notify_errors=True))
        logger.error("boom")
        logger.error("boom")  # Repeated within the cooldown -> not pushed again
        logger.error("other", extra={"no_alert": True})
        logger.warning("not an error")
        await asyncio.sleep(0.05)
        assert [t.splitlines()[-1] for t in rec.texts] == ["boom"]

    asyncio.run(scenario())


def test_import_existing_saves_history_without_pushing(env):
    client, router, rec = env
    login(client)
    client.post("/api/poll")
    router.pages = [[sms(5), sms(4)], [sms(3)]]
    assert client.post("/api/messages/import").json()["added"]
    items = client.get("/api/messages?limit=200").json()["items"]
    assert {m["index"] for m in items} >= {3, 4, 5}
    assert rec.sent == []
    assert all(m["delivered"] == {} for m in items if m["index"] in (3, 4, 5))
    assert client.post("/api/messages/import").json()["added"] == 0  # Importing again adds nothing
    assert client.post("/api/poll").json()["new"] == []  # Imported messages are not treated as new


def test_import_limit_keeps_newest_and_marks_all_seen(tmp_path):
    from test_core import make_poller

    rec = Recorder()
    router = FakeRouter([[sms(5), sms(4), sms(3)], [sms(2), sms(1)]])
    poller = make_poller(tmp_path, router, [rec])
    poller.import_limit = 2
    assert asyncio.run(poller.import_existing()) == 2
    assert [m["index"] for m in poller.history.list()["items"]] == [5, 4]
    assert asyncio.run(poller.poll_once()) == []  # Older messages that were not imported are not pushed either
    assert rec.sent == []


def test_clear_messages_only_touches_history(env):
    client, router, rec = env
    assert client.delete("/api/messages").status_code == 401
    login(client)
    client.post("/api/poll")
    router.pages = [[sms(3), sms(2), sms(1)]]
    client.post("/api/poll")
    assert client.get("/api/status").json()["messages_total"] >= 1

    deleted = client.delete("/api/messages").json()["deleted"]
    assert deleted >= 1
    assert client.get("/api/messages").json() == {"total": 0, "items": []}
    assert client.get("/api/status").json()["messages_total"] == 0
    sent_before = len(rec.sent)
    assert client.post("/api/poll").json()["new"] == []  # Not pushed again after clearing
    assert len(rec.sent) == sent_before


def test_i18n_dictionaries_are_complete():
    """Every language must have the same keys as English, and every key used by the HTML / JS must exist."""
    import json
    import re
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed to load i18n.js")
    static = Path(__file__).parent.parent / "src" / "tl_mr6500v" / "static"
    script = (
        "const fs=require('fs');"
        f"eval(fs.readFileSync({json.dumps(str(static / 'i18n.js'))},'utf8')+';globalThis.S=STRINGS;');"
        "console.log(JSON.stringify(Object.fromEntries(Object.entries(S).map(([k,v])=>[k,Object.keys(v)]))))"
    )
    keys = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    assert set(keys) == {"en", "zh-TW", "zh-CN", "ja"}
    for lang, lang_keys in keys.items():
        assert set(lang_keys) == set(keys["en"]), f"keys of {lang} do not match en"

    used = set(re.findall(r'data-i18n(?:-label)?="([\w.]+)"', (static / "index.html").read_text()))
    used |= set(re.findall(r'\bt\(\s*"([\w.]+)"', (static / "app.js").read_text()))
    used |= {f"pill.{s}" for s in ("ok", "busy", "error", "unknown")} | {"net.type.0", "net.type.3"}
    assert used <= set(keys["en"]), f"missing translations: {used - set(keys['en'])}"


def test_static_assets_served(env):
    client, *_ = env
    assert client.get("/i18n.js").status_code == 200


@pytest.mark.parametrize("value", ["", "  ", "change_me", "CHANGE_ME", "your_password_here"])
def test_placeholder_admin_password_is_rejected(value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
        Settings(tplink_password="x", admin_password=value, _env_file=None)


def test_health_is_minimal(env):
    client, *_ = env
    assert set(client.get("/health").json()) == {"ok", "last_poll_at"}
