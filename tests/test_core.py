import asyncio
import logging

from tl_mr6500v.db import Database
from tl_mr6500v.history import MessageStore
from tl_mr6500v.logging_setup import setup_logging
from tl_mr6500v.models import Sms
from tl_mr6500v.notifier import Notifier
from tl_mr6500v.poller import Poller
from tl_mr6500v.providers.base import Provider
from tl_mr6500v.router import PollResult, RouterBusy, parse_network, parse_sms, parse_sms_entries
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


    async def mark_read(self, sms, kick=False):
        self.marked = [*getattr(self, "marked", []), sms.uid]
        return not getattr(self, "missing", False)

    async def delete_message(self, sms, kick=False):
        self.deleted = [*getattr(self, "deleted", []), sms.uid]
        return not getattr(self, "missing", False)


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


class Flaky(Provider):
    """Fails while `down` is set; counts the sends that went through."""
    name = "flaky"

    def __init__(self, down=True):
        self.down, self.sent = down, []

    async def send_text(self, text):
        if self.down:
            raise RuntimeError("no network")
        self.sent.append(text)


def make_poller(tmp_path, router, providers):
    db = Database(tmp_path / "app.db")
    return Poller(router, Notifier(providers), SeenStore(tmp_path / "seen.json"),
                  MessageStore(db), SettingsStore(db, AppSettings()))


def test_parse_sms_entries_keep_the_stack_of_each_entry():
    entries = parse_sms_entries(RAW)
    assert [(stack, m.index) for stack, m in entries] == [("1,0,0,0,0,0", 239), ("2,0,0,0,0,0", 238)]


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


def pushed_with_failure(tmp_path, flaky):
    """A poller that already pushed one new message while `flaky` was down, plus the recording provider."""
    rec = Recorder()
    router = FakeRouter([[sms(1)]])
    poller = make_poller(tmp_path, router, [rec, flaky])
    asyncio.run(poller.poll_once())
    router.pages = [[sms(2), sms(1)]]
    asyncio.run(poller.poll_once())
    message = poller.history.list()["items"][0]
    return poller, rec, message


def test_resend_only_pushes_to_the_providers_that_failed(tmp_path):
    flaky = Flaky()
    poller, rec, message = pushed_with_failure(tmp_path, flaky)
    assert message["delivered"] == {"rec": True, "flaky": False} and message["retries"] == 0
    assert len(rec.sent) == 1

    flaky.down = False
    result = asyncio.run(poller.resend(message["id"]))
    assert result == {"delivered": {"rec": True, "flaky": True}, "retries": 1, "sent": True}
    assert len(flaky.sent) == 1 and len(rec.sent) == 1  # The provider that worked got no duplicate
    assert asyncio.run(poller.resend(message["id"]))["sent"] is False  # Nothing left to resend
    assert asyncio.run(poller.resend(9999)) is None


def test_retry_waits_for_the_backoff_and_stops_after_the_last_one(tmp_path):
    flaky = Flaky()
    poller, rec, message = pushed_with_failure(tmp_path, flaky)
    history = poller.history
    notified = history._db.query("SELECT notified_at FROM messages")[0]["notified_at"]

    assert history.pending_retries(poller.retry_delays, notified + 30) == []
    assert [d[0] for d in history.pending_retries(poller.retry_delays, notified + 61)] == [message["id"]]

    poller.retry_delays = (0, 0)
    assert asyncio.run(poller.retry_pending()) == 1
    assert asyncio.run(poller.retry_pending()) == 1
    assert asyncio.run(poller.retry_pending()) == 0  # Used up; only the manual button is left
    assert asyncio.run(poller.resend(message["id"]))["retries"] == 3


def test_failed_retries_are_warnings_and_only_the_last_one_is_an_error(tmp_path):
    flaky = Flaky()
    poller, rec, message = pushed_with_failure(tmp_path, flaky)
    poller.retry_delays = (0, 0)
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("tl_mr6500v")
    logger.addHandler(handler)
    try:
        asyncio.run(poller.retry_pending())
        assert [r.levelname for r in records if "again failed" in r.getMessage()] == ["WARNING"]
        assert not [r for r in records if r.levelno >= logging.ERROR]
        asyncio.run(poller.retry_pending())
    finally:
        logger.removeHandler(handler)
    assert [r.getMessage() for r in records if r.levelno >= logging.ERROR][-1].startswith("Gave up pushing")


def test_imported_and_unconfigured_providers_are_not_retried(tmp_path):
    flaky = Flaky()
    poller, rec, message = pushed_with_failure(tmp_path, flaky)
    poller.history.add(sms(7), {})  # Imported only
    poller.notifier.providers.remove(flaky)  # No longer configured
    poller.retry_delays = (0,)
    assert asyncio.run(poller.retry_pending()) == 1  # Still "due", but nothing is sent
    assert asyncio.run(poller.resend(message["id"]))["sent"] is False
    assert flaky.sent == []
