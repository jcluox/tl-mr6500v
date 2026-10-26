import asyncio
import logging

from tl_mr6500v.db import Database
from tl_mr6500v.history import MessageStore
from tl_mr6500v.logging_setup import setup_logging
from tl_mr6500v.models import Sms
from tl_mr6500v.notifier import Notifier
from tl_mr6500v.poller import Poller
from tl_mr6500v.providers.base import Provider
from tl_mr6500v.router import PollResult, RouterBusy, parse_network, parse_sms
from tl_mr6500v.runtime_settings import AppSettings, SettingsStore
from tl_mr6500v.state import SeenStore

RAW = (
    "[1,0,0,0,0,0]1\r\nindex=239\r\nfrom=0900\r\ncontent=line one\r\nline two\r\n"
    "receivedTime=2026-10-25 21:39:41\r\nunread=1\r\n"
    "[2,0,0,0,0,0]1\r\nindex=238\r\nfrom=0911\r\ncontent=hello\r\n"
    "receivedTime=2026-10-24 21:03:33\r\nunread=0\r\n[error]0\r\n"
)


def sms(i, content="hi"):
    return Sms(i, "0900", content, f"2026-10-25 00:00:{i:02d}", True)


class FakeRouter:
    def __init__(self, pages):
        self.pages = pages  # list of lists, newest first

    async def poll(self, should_continue, kick=False, max_pages=5):
        self.kick = kick
        out = []
        for page in self.pages[:max_pages]:
            out.extend(page)
            if not should_continue(page):
                break
        return PollResult(out, getattr(self, "network", None))


class Recorder(Provider):
    name = "rec"

    def __init__(self):
        self.sent = []
        self.texts = []

    async def send_text(self, text):
        self.texts.append(text)

    async def send(self, s):
        self.sent.append(s)


class Broken(Provider):
    name = "broken"

    async def send_text(self, text):
        raise RuntimeError("boom")


def make_poller(tmp_path, router, providers):
    db = Database(tmp_path / "app.db")
    return Poller(router, Notifier(providers), SeenStore(tmp_path / "seen.json"),
                  MessageStore(db), SettingsStore(db, AppSettings()))


def test_parse_sms_multiline_content():
    msgs = parse_sms(RAW)
    assert [m.index for m in msgs] == [239, 238]
    assert msgs[0].content == "line one\nline two"
    assert msgs[0].unread and not msgs[1].unread


def test_first_run_records_without_sending_then_sends_new(tmp_path):
    rec = Recorder()
    router = FakeRouter([[sms(2), sms(1)]])
    poller = make_poller(tmp_path, router, [rec])

    assert asyncio.run(poller.poll_once()) == []
    assert rec.sent == []

    router.pages = [[sms(4), sms(3), sms(2), sms(1)]]
    new = asyncio.run(poller.poll_once())
    assert [m.index for m in new] == [4, 3]
    assert [m.index for m in rec.sent] == [3, 4]  # oldest to newest

    assert asyncio.run(poller.poll_once()) == []
    assert len(rec.sent) == 2

    # State is kept after a restart
    again = make_poller(tmp_path, router, [rec])
    assert asyncio.run(again.poll_once()) == []
    assert len(rec.sent) == 2


def test_pages_until_seen(tmp_path):
    rec = Recorder()
    router = FakeRouter([[sms(2), sms(1)]])
    poller = make_poller(tmp_path, router, [rec])
    asyncio.run(poller.poll_once())
    router.pages = [[sms(6), sms(5)], [sms(4), sms(3)], [sms(2), sms(1)]]
    asyncio.run(poller.poll_once())
    assert sorted(m.index for m in rec.sent) == [3, 4, 5, 6]


def test_provider_failure_is_logged_and_isolated(tmp_path):
    setup_logging(tmp_path / "logs")
    rec = Recorder()
    outcome = asyncio.run(Notifier([Broken(), rec]).notify(sms(1)))
    assert outcome == {"broken": False, "rec": True}
    assert len(rec.sent) == 1
    for h in logging.getLogger("tl_mr6500v").handlers:
        h.flush()
    assert "provider=broken" in (tmp_path / "logs" / "error.log").read_text()


def test_busy_router_is_skipped_without_error(tmp_path):
    class BusyRouter:
        async def poll(self, should_continue, kick=False, max_pages=5):
            raise RouterBusy("busy")

    poller = make_poller(tmp_path, BusyRouter(), [Recorder()])
    assert asyncio.run(poller.poll_once()) == []
    assert poller.last_error is None


def test_busy_skip_is_recorded_in_app_log(tmp_path):
    setup_logging(tmp_path / "logs")

    class BusyRouter:
        async def poll(self, should_continue, kick=False, max_pages=5):
            raise RouterBusy("Someone is logged in to the router. Skipping this check.")

    asyncio.run(make_poller(tmp_path, BusyRouter(), []).poll_once())
    for h in logging.getLogger("tl_mr6500v").handlers:
        h.flush()
    assert "Skipping this check" in (tmp_path / "logs" / "app.log").read_text()
    assert "Skipping this check" not in (tmp_path / "logs" / "error.log").read_text()
