"use strict";

const REFRESH_MS = 5000;
const PAGE_SIZE = 30;

const $ = (id) => document.getElementById(id);

// Always write message and log content with textContent, to avoid XSS
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

class Unauthorized extends Error {}

class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

// Known errors are shown in the current language by status code; others are shown as the server sent them
function errText(err) {
  if (err instanceof ApiError) {
    if (err.status === 409) return t("err.routerBusy");
    if (err.status === 429) return t("login.throttled", { s: err.message.match(/\d+/)?.[0] ?? "" });
  }
  return err.message;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });
  if (res.status === 401 && !path.startsWith("/api/auth/login")) throw new Unauthorized();
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data;
}

const fmtEpoch = (s) => new Date(s * 1000).toLocaleString(locale(), { hour12: false });
const fmtIso = (s) => (s ? new Date(s).toLocaleString(locale(), { hour12: false }) : "—");

function fillKv(dl, rows) {
  dl.replaceChildren(
    ...rows.flatMap(([k, v, cls]) => [el("dt", {}, k), el("dd", cls ? { class: cls } : {}, v ?? "—")]),
  );
}

// ---------- Sign in / sign out ----------
let timer = null;

function showLogin() {
  clearInterval(timer);
  $("app-view").hidden = true;
  $("login-view").hidden = false;
}

function showApp() {
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  refresh();
  clearInterval(timer);
  timer = setInterval(() => !document.hidden && refresh(), REFRESH_MS);
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  $("login-error").textContent = "";
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
    });
    event.target.reset();
    showApp();
  } catch (err) {
    $("login-error").textContent = err.status === 401 ? t("login.invalid") : errText(err);
  }
});

$("logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  showLogin();
});

// ---------- Status ----------
const ROUTER_PILL = { ok: "ok", busy: "busy", error: "error", unknown: "" };
const NETWORK_TYPES = [0, 3]; // Network type codes that have a translation; others show "Type N"

function renderHero(state, n) {
  const hero = $("hero");
  const connected = n?.connected;
  hero.dataset.state = !n ? (state === "error" ? "error" : "unknown") : connected ? (state === "busy" ? "busy" : "ok") : "down";
  const signal = connected ? Math.min(n.signal, 4) : 0;
  [...$("signal").children].forEach((bar, i) => bar.classList.toggle("on", i < signal));
  $("signal").setAttribute("aria-label", n ? `${t("net.signal")} ${n.signal}` : "");
  if (!n) {
    $("hero-main").textContent = t("net.noData");
    $("hero-sub").textContent = t("net.noDataNote");
    $("hero-ip").textContent = "";
    return;
  }
  const type = NETWORK_TYPES.includes(n.network_type) ? t(`net.type.${n.network_type}`) : t("net.type.other", { n: n.network_type });
  $("hero-main").textContent = connected ? t("net.connected") : t("net.disconnected");
  $("hero-sub").textContent = type;
  $("hero-ip").textContent = n.ipv4 && n.ipv4 !== "0.0.0.0" ? n.ipv4 : "";
}

// Top line: starts at the last check and sweeps left to right over the check interval; restarts on every check
let sweepKey = null;
function renderSweep(checkedAt, serverTime, interval) {
  const sweep = $("sweep");
  if (!checkedAt || checkedAt === sweepKey) return;
  sweepKey = checkedAt;
  const elapsed = Math.max(0, serverTime - Date.parse(checkedAt) / 1000);
  sweep.style.setProperty("--interval", `${interval}s`);
  sweep.style.setProperty("--elapsed", `${Math.min(elapsed, interval)}s`);
  sweep.classList.remove("run");
  void sweep.offsetWidth; // Restart the animation
  sweep.classList.add("run");
}

function renderRouter(r) {
  const state = r.state in ROUTER_PILL ? r.state : "unknown";
  $("router-pill").className = `pill ${ROUTER_PILL[state]}`;
  $("router-pill").textContent = t(`pill.${state}`);
  renderHero(state, r.network);

  const n = r.network;
  if (!n) {
    fillKv($("network"), [[t("net.empty"), t("net.noData")]]);
    $("network-note").textContent = "";
    return;
  }
  fillKv($("network"), [
    [t("net.signal"), `${n.signal}`],
    [t("net.roaming"), n.roaming ? t("yes") : t("no")],
    [t("net.gateway"), n.gateway || "—"],
    ["DNS", [n.dns1, n.dns2].filter(Boolean).join(", ") || "—"],
    [t("net.interface"), n.interface || "—"],
  ]);
  $("network-note").textContent = t("net.note", { t: fmtIso(r.network_at) });
}

function renderStatus(s) {
  fillKv($("poll-info"), [
    [t("poll.last"), fmtIso(s.last_poll_at)],
    [t("poll.interval"), t("poll.intervalValue", { n: s.poll_interval_seconds })],
    [t("poll.error"), s.last_error || t("none"), s.last_error ? "status-off" : ""],
  ]);
  fillKv($("push-info"), [
    [t("push.providers"), s.providers.join(", ") || t("none"), s.providers.length ? "" : "status-off"],
    [t("push.messages"), t("push.messagesValue", { n: s.messages_total })],
    [t("push.errors24"), t("push.errorsValue", { n: s.errors_24h }), s.errors_24h ? "status-off" : ""],
  ]);
  $("messages-count").textContent = `(${s.messages_total})`;
  const banner = $("error-banner");
  banner.hidden = !s.last_error && !s.errors_24h;
  banner.textContent = s.last_error
    ? t("banner.current", { e: s.last_error })
    : t("banner.past", { n: s.errors_24h });
}

// ---------- Messages ----------
let messageCount = 0;

function splitTime(received) {
  const [date, time = ""] = received.split(" ");
  return [date, time.slice(0, 5)];
}

function messageCard(m) {
  const entries = Object.entries(m.delivered);
  const badges = (entries.length ? entries : [["", null]]).map(([name, ok]) =>
    ok === null
      ? el("span", { class: "badge" }, t("badge.notPushed"))
      : el("span", { class: `badge ${ok ? "ok" : "fail"}` }, t(ok ? "badge.delivered" : "badge.failed", { name })),
  );
  const [date, time] = splitTime(m.received_at);
  return el("article", { class: "msg" },
    el("div", { class: "msg-time" }, el("b", {}, time), date),
    el("div", {},
      el("div", { class: "msg-sender" }, m.sender),
      el("div", { class: "msg-body" }, m.content),
      el("div", { class: "badges" }, ...badges),
    ),
  );
}

async function loadMessages(append = false) {
  const offset = append ? messageCount : 0;
  // On auto refresh, keep the number of messages the user has already expanded
  const limit = append ? PAGE_SIZE : Math.min(Math.max(messageCount, PAGE_SIZE), 200);
  const data = await api(`/api/messages?limit=${limit}&offset=${offset}`);
  const cards = data.items.map(messageCard);
  if (append) $("messages-list").append(...cards);
  else $("messages-list").replaceChildren(...cards);
  messageCount = offset + data.items.length;
  $("messages-more").hidden = messageCount >= data.total;
  $("messages-empty").hidden = data.total > 0;
}

$("import-existing").addEventListener("click", async (event) => {
  const btn = event.target, msg = $("import-msg");
  btn.disabled = true;
  msg.textContent = t("msg.importing");
  try {
    const { added } = await api("/api/messages/import", { method: "POST" });
    msg.textContent = t("msg.imported", { n: added });
    await refresh();
  } catch (err) {
    if (err instanceof Unauthorized) showLogin();
    else msg.textContent = t("msg.importFailed", { e: errText(err) });
  } finally {
    btn.disabled = false;
  }
});

$("clear-messages").addEventListener("click", async (event) => {
  if (!confirm(t("msg.confirmClear"))) return;
  const btn = event.target, msg = $("import-msg");
  btn.disabled = true;
  try {
    const { deleted } = await api("/api/messages", { method: "DELETE" });
    msg.textContent = t("msg.cleared", { n: deleted });
    messageCount = 0;
    await refresh();
  } catch (err) {
    if (err instanceof Unauthorized) showLogin();
    else msg.textContent = t("msg.clearFailed", { e: errText(err) });
  } finally {
    btn.disabled = false;
  }
});

$("messages-more").addEventListener("click", () => loadMessages(true));

// ---------- Log ----------
async function loadLogs() {
  const logs = await api(`/api/logs?level=${$("log-level").value}&limit=200`);
  $("log-list").replaceChildren(
    ...(logs.length ? logs : [{ time: 0, level: "INFO", message: t("log.empty") }]).map((l) =>
      el("div", { class: `log-row ${l.level}` },
        el("span", { class: "t" }, l.time ? fmtEpoch(l.time) : ""),
        el("span", { class: "lv" }, l.level),
        el("span", { class: "log-text" }, l.message),
      ),
    ),
  );
}

$("log-level").addEventListener("change", loadLogs);

// ---------- Settings ----------
async function loadSettings() {
  const s = await api("/api/settings");
  const form = $("settings-form");
  form.poll_interval_seconds.value = s.poll_interval_seconds;
  for (const key of ["kick_existing_login", "allow_lan_access", "notify_errors"]) form[key].checked = s[key];
}

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const msg = $("settings-msg");
  msg.className = "small";
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        poll_interval_seconds: Number(form.poll_interval_seconds.value),
        kick_existing_login: form.kick_existing_login.checked,
        allow_lan_access: form.allow_lan_access.checked,
        notify_errors: form.notify_errors.checked,
      }),
    });
    msg.textContent = t("set.saved");
    msg.classList.add("status-on");
    refresh();
  } catch (err) {
    msg.textContent = t("set.saveFailed", { e: errText(err) });
    msg.classList.add("status-off");
  }
});

// ---------- Tabs and refresh ----------
let activeTab = "messages";
const LOADERS = { messages: () => loadMessages(), status: async () => {}, logs: loadLogs, settings: loadSettings };

function switchTab(name) {
  activeTab = name;
  for (const btn of document.querySelectorAll(".tabs button")) btn.classList.toggle("active", btn.dataset.tab === name);
  for (const tab of Object.keys(LOADERS)) $(`tab-${tab}`).hidden = tab !== name;
  LOADERS[name]().catch(handleError);
}

document.querySelector(".tabs").addEventListener("click", (event) => {
  // The button contains a <span> (for example the message count); a click on a child must count too
  const tab = event.target.closest("[data-tab]");
  if (tab) switchTab(tab.dataset.tab);
});

function handleError(err) {
  if (err instanceof Unauthorized) showLogin();
  else console.error(err);
}

async function refresh() {
  try {
    const [status, router] = await Promise.all([api("/api/status"), api("/api/router")]);
    renderStatus(status);
    renderRouter(router);
    renderSweep(router.checked_at, status.server_time, status.poll_interval_seconds);
    // The Settings tab may be mid-edit; do not overwrite it on auto refresh
    if (activeTab !== "settings") await LOADERS[activeTab]();
  } catch (err) {
    handleError(err);
  }
}

$("poll-now").addEventListener("click", async (event) => {
  const btn = event.target;
  btn.disabled = true;
  try {
    await api("/api/poll", { method: "POST" });
    await refresh();
  } catch (err) {
    handleError(err);
  } finally {
    btn.disabled = false;
  }
});

// ---------- Language ----------
for (const select of document.querySelectorAll(".lang-select")) {
  select.addEventListener("change", () => {
    setLang(select.value);
    if (!$("app-view").hidden) refresh(); // Redraw generated content (status, messages, log) in the new language
  });
}
applyI18n();

api("/api/auth/me").then(showApp, () => showLogin());
