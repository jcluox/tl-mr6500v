"""Login and SMS reading for the TP-Link TL-MR6500v web interface.

Protocol (reverse-engineered from the router's front-end JS):
- /cgi/getParm returns the RSA public key (nn, ee) and a sequence number seq
- /cgi/login encrypts "admin\\n<password>" with AES-CBC, and signs with RSA (no padding, 64-byte blocks, zero-padded on the right)
  the AES key/iv, the password hash and the length
- After login the home page embeds `var token`; every later /cgi_gdpr request must send it in a TokenID header
"""

import asyncio
import base64
import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .logging_setup import LOGGER_NAME
from .models import Sms

log = logging.getLogger(f"{LOGGER_NAME}.router")

RSA_BLOCK = 64
ACT_GET, ACT_SET, ACT_GL = 1, 2, 5
# When forcing a login, if the other user's request is still being handled (isBusy=1), the web interface waits a moment before logging in
KICK_BUSY_RETRIES = 2
_ENTRY_RE = re.compile(
    r"index=(\d+)\nfrom=(.*?)\ncontent=(.*?)\nreceivedTime=(.*?)\nunread=(\d)",
    re.DOTALL,
)
_BLOCK_RE = re.compile(r"^\[\d+(?:,\d+){5}\]1\s*$", re.MULTILINE)


@dataclass
class PollResult:
    messages: list[Sms]
    network: dict | None  # None if reading failed (this does not affect the message check)


class RouterError(Exception):
    pass


class RouterBusy(RouterError):
    """Someone is already logged in to the router (for example in a browser). We do not log in this time, so we do not kick them out."""


def _rsa(text: str, nn: str, ee: str) -> str:
    n, e = int(nn, 16), int(ee, 16)
    raw = text.encode()
    out = []
    for i in range(0, len(raw), RSA_BLOCK):
        block = int.from_bytes(raw[i : i + RSA_BLOCK].ljust(RSA_BLOCK, b"\0"), "big")
        out.append(format(pow(block, e, n), "0128x"))
    return "".join(out)


def _rand16() -> str:
    return (str(int(time.time() * 1000)) + str(int(1e9 * random.random())))[:16]


def parse_kv(text: str) -> dict[str, str]:
    """Parse an object in `key=value` line format (ignoring the `[...]` header and the `[error]` footer)."""
    fields = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if "=" in line and not line.startswith("["):
            key, _, value = line.partition("=")
            fields[key] = value
    return fields


# Keep only network fields. WAN_LTE_LINK_CFG also holds the SIM phone number, IMSI and other personal data, which we deliberately do not read
_NETWORK_FIELDS = {
    "connectStatus": "connect_status",
    "networkType": "network_type",
    "roamingStatus": "roaming",
    "signalStrength": "signal",
    "ipv4": "ipv4",
    "netmask": "netmask",
    "gatewayV4": "gateway",
    "dns1v4": "dns1",
    "dns2v4": "dns2",
    "ipv6": "ipv6",
    "ifName": "interface",
}
_INT_FIELDS = {"connect_status", "network_type", "roaming", "signal"}
_CONNECTED = 4  # connectStatus: in testing, 4 = connected and 0 = disconnected; the other codes are unconfirmed


def parse_network(text: str) -> dict:
    raw = parse_kv(text)
    network: dict = {}
    for src, dst in _NETWORK_FIELDS.items():
        value = raw.get(src, "")
        network[dst] = int(value) if dst in _INT_FIELDS and value.lstrip("-").isdigit() else value
    network["connected"] = (
        network["connect_status"] == _CONNECTED and network["ipv4"] not in ("", "0.0.0.0")
    )
    return network


def parse_sms(text: str) -> list[Sms]:
    text = text.replace("\r\n", "\n")
    messages = []
    for block in _BLOCK_RE.split(text)[1:]:
        match = _ENTRY_RE.search(block)
        if match:
            index, sender, content, received, unread = match.groups()
            messages.append(Sms(int(index), sender, content, received, unread == "1"))
    return messages


class RouterClient:
    """Log in, read and log out on every poll: the router allows only one logged-in user at a time, so release it when done."""

    def __init__(self, host: str, password: str, timeout: float = 10):
        self._host = host.rstrip("/")
        self._password = password
        self._timeout = timeout

    async def poll(self, should_continue, kick: bool = False, max_pages: int = 5) -> PollResult:
        """Log in once, read the network status and the inbox (newest first, until should_continue(page_msgs) is False), then log out.

        With kick=True we log in even if someone is already logged in to the router (they are logged out)."""
        async with httpx.AsyncClient(
            headers={"Referer": self._host + "/"}, timeout=self._timeout
        ) as http:
            session = await self._login(http, kick)
            try:
                try:
                    network = await session.network_status()
                except RouterError as exc:
                    log.warning("Reading the network status failed: %s", exc)
                    network = None
                messages: list[Sms] = []
                for page in range(1, max_pages + 1):
                    msgs = await session.recv_page(page)
                    messages.extend(msgs)
                    if not msgs or not should_continue(msgs):
                        break
                return PollResult(messages, network)
            finally:
                await session.logout()

    async def _login(self, http: httpx.AsyncClient, kick: bool = False) -> "_Session":
        try:
            busy = (await http.post(f"{self._host}/cgi/getBusy")).text
            if re.search(r"isLogined=1", busy):
                if not kick:
                    raise RouterBusy("Someone is logged in to the router. Skipping this check.")
                log.warning("Someone is logged in to the router. Forcing login as configured (they will be logged out)")
                for _ in range(KICK_BUSY_RETRIES):
                    if not re.search(r"isBusy=1", busy):
                        break
                    await asyncio.sleep(1)
                    busy = (await http.post(f"{self._host}/cgi/getBusy")).text
        except httpx.HTTPError as exc:
            raise RouterError(f"Could not check the router login state: {exc!r}") from exc
        try:
            params = (await http.post(f"{self._host}/cgi/getParm")).text
        except httpx.HTTPError as exc:
            raise RouterError(f"Could not get the router login parameters: {exc!r}") from exc
        found = [re.search(rf'{k}="(\w+)"', params) for k in ("nn", "ee", "seq")]
        if not all(found):
            raise RouterError(f"Unexpected router login parameters. The response starts with: {params[:200]!r}")
        nn, ee, seq = found[0].group(1), found[1].group(1), int(found[2].group(1))

        session = _Session(http, self._host, nn, ee, seq, self._password)
        await session.login()
        return session


class _Session:
    def __init__(self, http, host, nn, ee, seq, password):
        self.http, self.host, self.nn, self.ee, self.seq = http, host, nn, ee, seq
        self.password = password
        self.key, self.iv = _rand16(), _rand16()
        self.hash = hashlib.md5(("admin" + password).encode()).hexdigest()
        self.token = "0"

    def _aes_encrypt(self, text: str) -> str:
        cipher = AES.new(self.key.encode(), AES.MODE_CBC, self.iv.encode())
        return base64.b64encode(cipher.encrypt(pad(text.encode(), 16))).decode()

    def _aes_decrypt(self, text: str) -> str:
        cipher = AES.new(self.key.encode(), AES.MODE_CBC, self.iv.encode())
        return unpad(cipher.decrypt(base64.b64decode(text)), 16).decode()

    def _sign(self, data_len: int, login: bool) -> str:
        sign = f"h={self.hash}&s={self.seq + data_len}"
        if login:
            sign = f"key={self.key}&iv={self.iv}&" + sign
        return _rsa(sign, self.nn, self.ee)

    async def login(self) -> None:
        data = self._aes_encrypt(f"admin\n{self.password}")
        url = (
            f"{self.host}/cgi/login?data={quote(data, safe='')}"
            f"&sign={self._sign(len(data), True)}&Action=1&LoginStatus=0&isMobile=0"
        )
        try:
            resp = await self.http.post(url)
            ret = re.search(r"\$\.ret=(\d+)", resp.text)
            if not ret or ret.group(1) != "0":
                code = ret.group(1) if ret else resp.status_code
                hint = "wrong password" if code == "71233" else "someone else may be logged in to the router, or it is temporarily locked"
                raise RouterError(f"Login failed (code={code}): {hint}")
            page = (await self.http.get(f"{self.host}/")).text
            token = re.search(r'var token="(\w+)"', page)
            if not token:
                raise RouterError("TokenID not found after login")
            self.token = token.group(1)
        except httpx.HTTPError as exc:
            raise RouterError(f"Could not connect to the router: {exc!r}") from exc

    async def logout(self) -> None:
        try:
            await self.http.post(f"{self.host}/cgi/logout", headers={"TokenID": self.token})
        except httpx.HTTPError:
            pass

    async def _cgi(self, acts: list[int], body: str) -> str:
        data = self._aes_encrypt("&".join(map(str, acts)) + "\r\n" + body)
        payload = f"sign={self._sign(len(data), False)}\r\ndata={data}\r\n"
        try:
            resp = await self.http.post(
                f"{self.host}/cgi_gdpr",
                content=payload,
                headers={"Content-Type": "text/plain", "TokenID": self.token},
            )
            resp.raise_for_status()
            return self._aes_decrypt(resp.text)
        except (httpx.HTTPError, ValueError) as exc:
            raise RouterError(f"Could not read data from the router: {exc!r}") from exc

    async def recv_page(self, page: int) -> list[Sms]:
        stack = "#0,0,0,0,0,0#0,0,0,0,0,0]"
        text = await self._cgi(
            [ACT_SET, ACT_GL],
            f"[LTE_SMS_RECVMSGBOX{stack}0,1\r\npageNumber={page}\r\n"
            f"[LTE_SMS_RECVMSGENTRY{stack}1,0\r\n",
        )
        return parse_sms(text)

    async def network_status(self) -> dict:
        text = await self._cgi(
            [ACT_GET], "[WAN_LTE_LINK_CFG#2,1,0,0,0,0#0,0,0,0,0,0]0,0\r\n"
        )
        if not text.lstrip().startswith("[2,1,0,0,0,0]"):
            raise RouterError(f"Unexpected network status response: {text[:100]!r}")
        return parse_network(text)
