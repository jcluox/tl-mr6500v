"use strict";

const REFRESH_MS = 5000;
const MESSAGE_CHUNK = 200;  // How many messages one request loads (the API maximum); paging and filtering work on what is loaded
const PAGE_SIZES = [10, 20, 50, 100];
const PAGE_SIZE_KEY = "tl6500v.pageSize";

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
    if (err.status === 403 && err.message === "wrong_password") return t("err.wrongPassword");
    if (err.status === 403 && err.message === "edit_disabled") return t("err.editDisabled");
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
  route();
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
  const band = $("status-band");
  const connected = n?.connected;
  band.dataset.state = !n ? (state === "error" ? "error" : "unknown") : connected ? (state === "busy" ? "busy" : "ok") : "down";
  const signal = connected ? Math.min(n.signal, 4) : 0;
  [...$("signal").children].forEach((bar, i) => bar.classList.toggle("on", i < signal));
  $("signal").setAttribute("aria-label", n ? `${t("net.signal")} ${n.signal}` : "");
  if (!n) {
    $("hero-main").textContent = t("net.noData");
    $("hero-sub").textContent = t("net.noDataNote");
    return;
  }
  const type = NETWORK_TYPES.includes(n.network_type) ? t(`net.type.${n.network_type}`) : t("net.type.other", { n: n.network_type });
  $("hero-main").textContent = connected ? t("net.connected") : t("net.disconnected");
  $("hero-sub").textContent = type;
}

// One label/value cell of the status band; `cls` colours the value and `href` makes it a link
function cell(label, value, cls = "", href = "", title = "") {
  const dd = el("dd", cls ? { class: cls } : {}, href ? el("a", { href }, value) : value);
  const box = el("div", title ? { class: "cell", title } : { class: "cell" }, el("dt", {}, label), dd);
  return box;
}

// "More details" shows everything the old Status page listed; the choice is remembered in the browser
const STATUS_OPEN_KEY = "tl6500v.statusOpen";

function setDetailsOpen(open, save = true) {
  $("band-details").hidden = !open;
  $("band-toggle").setAttribute("aria-expanded", String(open));
  const label = t(open ? "band.less" : "band.more");
  $("band-toggle").setAttribute("aria-label", label);
  $("band-toggle").setAttribute("title", label);
  if (!save) return;
  try {
    localStorage.setItem(STATUS_OPEN_KEY, open ? "1" : "0");
  } catch {}
}

$("band-toggle").addEventListener("click", () => setDetailsOpen($("band-details").hidden));
setDetailsOpen((() => { try { return localStorage.getItem(STATUS_OPEN_KEY) === "1"; } catch { return false; } })(), false);

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

// Details cells that depend on the router's network status are kept here until the polling status arrives too
let netDetails = [];

function renderRouter(r) {
  const state = r.state in ROUTER_PILL ? r.state : "unknown";
  $("router-pill").className = `pill ${ROUTER_PILL[state]}`;
  $("router-pill").textContent = t(`pill.${state}`);
  renderHero(state, r.network);

  const n = r.network;
  const cells = [];
  netDetails = [];
  if (n) {
    const dns = [n.dns1, n.dns2].filter(Boolean);
    const hasIp = n.ipv4 && n.ipv4 !== "0.0.0.0";
    cells.push(cell("IP", hasIp ? n.ipv4 : "—"));
    cells.push(cell(t("net.gateway"), n.gateway || "—"));
    cells.push(cell("DNS", dns[0] || "—"));
    netDetails = [
      cell(t("net.signal"), `${n.signal}`),
      cell(t("net.roaming"), n.roaming ? t("yes") : t("no")),
      cell(t("net.interface"), n.interface || "—"),
      cell("DNS", dns.join(", ") || "—"),
      cell(t("net.dataTime"), fmtIso(r.network_at)),
    ];
  }
  $("band-network").replaceChildren(...cells);
  if (lastStatus) renderDetails(lastStatus);
}

const fmtTime = (s) => (s ? new Date(s).toLocaleTimeString(locale(), { hour12: false }) : "—");

let lastStatus = null;

function renderDetails(s) {
  const nothing = el("p", { class: "muted small wide" }, t("net.noData"));
  $("band-details").replaceChildren(
    ...(netDetails.length ? netDetails : [nothing]),
    cell(t("poll.last"), fmtIso(s.last_poll_at)),
    cell(t("push.messages"), t("push.messagesValue", { n: s.messages_total })),
    cell(t("poll.error"), s.last_error || t("none"), s.last_error ? "status-off wide" : ""),
  );
}

function renderStatus(s) {
  lastStatus = s;
  renderDetails(s);
  $("band-poll").replaceChildren(
    cell(t("band.lastCheck"), fmtTime(s.last_poll_at)),
    cell(t("poll.interval"), t("poll.intervalValue", { n: s.poll_interval_seconds })),
  );
  $("band-push").replaceChildren(
    cell(t("band.providers"), s.providers.join(", ") || t("none"), s.providers.length ? "" : "status-off"),
    cell(t("push.errors24"), t("push.errorsValue", { n: s.errors_24h }), s.errors_unread ? "status-off" : "", s.errors_24h ? "#/logs" : ""),
  );
  $("messages-count").textContent = `(${s.messages_total})`;
  errorsAckAt = s.errors_ack_at;
  errorsUnread = s.errors_unread;
  const badge = $("log-badge");
  badge.hidden = !s.errors_unread;
  badge.textContent = s.errors_unread > 99 ? "99+" : String(s.errors_unread);
  // A current error (the router cannot be checked right now) stays until a check succeeds; past errors can be marked as read
  const banner = $("error-banner");
  banner.hidden = !s.last_error && !s.errors_unread;
  $("banner-text").textContent = s.last_error
    ? t("banner.current", { e: s.last_error })
    : t("banner.unread", { n: s.errors_unread });
  $("banner-ack").hidden = Boolean(s.last_error);
}

// Errors are "read" once the user marks them; the server keeps that time, so every browser agrees. null = not known yet
let errorsAckAt = null;
let errorsUnread = 0;

async function ackErrors() {
  const note = $("ack-note");
  try {
    await api("/api/errors/ack", { method: "POST" });
    note.textContent = t("log.markedRead");
    await refresh();
  } catch (err) {
    if (err instanceof Unauthorized) showLogin();
    else note.textContent = errText(err);
  }
}

$("banner-ack").addEventListener("click", ackErrors);
$("ack-errors").addEventListener("click", ackErrors);

// ---------- Messages ----------
let loadedMessages = [];  // Every message loaded so far (the filter only looks at these)
let messagesTotal = 0;
let messagesEditable = false;  // Settings switch: when off, messages are read-only
let page = 1;
let pageSize = (() => {
  try {
    const saved = Number(localStorage.getItem(PAGE_SIZE_KEY));
    if (PAGE_SIZES.includes(saved)) return saved;
  } catch {}
  return 20;
})();
const openMessages = new Set();  // Expanded messages stay open when the list is redrawn

function splitTime(received) {
  const [date, time = ""] = received.split(" ");
  return [date, time.slice(0, 5)];
}

// Today: time only; this year: month-day and time; earlier years: the full date and time
function whenLabel(m) {
  const [date, time] = splitTime(m.received_at);
  const today = new Date().toLocaleDateString("sv");  // YYYY-MM-DD in local time, like the router's timestamps
  if (date === today) return time;
  return date.slice(0, 4) === today.slice(0, 4) ? `${date.slice(5)} ${time}` : `${date} ${time}`;
}


function messageCard(m) {
  const entries = Object.entries(m.delivered);
  const badges = (entries.length ? entries : [["", null]]).map(([name, ok]) =>
    ok === null
      ? el("span", { class: "badge idle" }, t("badge.notPushed"))
      : el("span", { class: `badge ${ok ? "ok" : "fail"}` }, t(ok ? "badge.delivered" : "badge.failed", { name })),
  );
  const [date, time] = splitTime(m.received_at);
  const row = el("button", { class: "msg-row", type: "button", "aria-expanded": "false" },
    el("span", { class: "msg-sender" }, m.unread ? el("i", { class: "unread-dot", title: t("msg.unread") }) : "", m.sender),
    el("span", { class: "msg-preview" }, m.content),
    el("span", { class: "badges" }, ...badges),
    el("span", { class: "msg-when" }, whenLabel(m)),
  );
  const detail = el("div", { class: "msg-detail" },
    el("div", { class: "msg-meta" }, `${date} ${time}`),
    el("div", { class: "msg-body" }, m.content),
  );
  const card = el("article", { class: `msg${m.unread ? " unread" : ""}${openMessages.has(m.id) ? " open" : ""}` }, row, detail);
  row.setAttribute("aria-expanded", String(card.classList.contains("open")));
  if (messagesEditable) {
    const del = el("button", { class: "danger", type: "button" }, t("msg.delete"));
    del.addEventListener("click", () => deleteMessage(m));
    detail.append(el("div", { class: "msg-actions" }, del));
  }
  row.addEventListener("click", () => {
    const open = card.classList.toggle("open");
    row.setAttribute("aria-expanded", String(open));
    open ? openMessages.add(m.id) : openMessages.delete(m.id);
    // Read-only mode: opening a message changes nothing on the router
    if (open && messagesEditable && card.classList.contains("unread")) markRead(m.id, card);
  });
  return card;
}

// Resolves with the dialog's return value ("ok" or "cancel"; closing with Esc counts as cancel)
function showDialog(dialog) {
  dialog.returnValue = "cancel";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue), { once: true });
    dialog.showModal();
  });
}

// Deleting removes the message from the router (SIM) as well as from the Dashboard
async function deleteMessage(m) {
  $("delete-summary").textContent = `${m.sender} · ${m.received_at}`;
  if ((await showDialog($("delete-dialog"))) !== "ok") return;
  const msg = $("import-msg");
  try {
    await api(`/api/messages/${m.id}`, { method: "DELETE" });
    loadedMessages = loadedMessages.filter((x) => x.id !== m.id);
    openMessages.delete(m.id);
    messagesTotal -= 1;
    $("messages-count").textContent = `(${messagesTotal})`;
    renderMessages();
    msg.textContent = t("del.done");
  } catch (err) {
    if (err instanceof Unauthorized) showLogin();
    else {
      msg.textContent = t("del.failed", { e: errText(err) });
      if (err.status === 403) refresh(); // The switch was turned off somewhere else
    }
  }
}

// Opening a message marks it read on the router too; if that fails it stays unread so the next click retries
async function markRead(id, card) {
  const msg = $("import-msg");
  try {
    await api(`/api/messages/${id}/read`, { method: "POST" });
    card.classList.remove("unread");
    card.querySelector(".unread-dot")?.remove();
    msg.textContent = "";
  } catch (err) {
    if (err instanceof Unauthorized) showLogin();
    else msg.textContent = t("msg.readFailed", { e: errText(err) });
  }
}

// The filter works on the messages that are loaded; ones that are not loaded yet are ignored
function messageMatches(m) {
  const keyword = $("search-keyword").value.trim().toLowerCase();
  const from = $("search-from").value.replace("T", " ");              // "YYYY-MM-DD HH:MM", like the router's timestamps
  const to = $("search-to").value.replace("T", " ");
  if (keyword && !`${m.content}\n${m.sender}`.toLowerCase().includes(keyword)) return false;
  if (from && m.received_at < from) return false;
  if (to && m.received_at > `${to}:59`) return false;
  return true;
}

// Page numbers to show: the first, the last and the current page with its neighbours; gaps are "…"
function pageItems(current, last) {
  const items = [1];
  const start = Math.max(2, current - 1), end = Math.min(last - 1, current + 1);
  if (start > 2) items.push("…");
  for (let i = start; i <= end; i++) items.push(i);
  if (end < last - 1) items.push("…");
  if (last > 1) items.push(last);
  return items;
}

function renderPager(count, last) {
  $("pager").hidden = loadedMessages.length === 0;
  const from = (page - 1) * pageSize + 1;
  $("page-range").textContent = count ? t("page.range", { from, to: Math.min(page * pageSize, count), total: count }) : "";
  $("page-loaded").hidden = loadedMessages.length >= messagesTotal;
  $("page-loaded-text").textContent = t("page.loaded", { n: loadedMessages.length, total: messagesTotal });
  const nav = (label, target, text, disabled) => {
    const btn = el("button", { type: "button", "aria-label": label }, text);
    btn.disabled = disabled;
    btn.addEventListener("click", () => goToPage(target));
    return btn;
  };
  $("page-nav").replaceChildren(...(last <= 1 ? [] : [
    nav(t("page.prev"), page - 1, "‹", page <= 1),
    ...pageItems(page, last).map((item) => {
      if (item === "…") return el("span", { class: "gap", "aria-hidden": "true" }, "…");
      const btn = nav(t("page.go", { n: item }), item, String(item), false);
      if (item === page) btn.setAttribute("aria-current", "page");
      return btn;
    }),
    nav(t("page.next"), page + 1, "›", page >= last),
  ]));
}

function goToPage(n) {
  page = n;
  renderMessages();
  $("messages-list").scrollIntoView({ block: "start" });
}

function renderMessages() {
  const filtering = Boolean($("search-keyword").value.trim() || $("search-from").value || $("search-to").value);
  const shown = loadedMessages.filter(messageMatches);
  const last = Math.max(1, Math.ceil(shown.length / pageSize));
  page = Math.min(Math.max(page, 1), last);  // After a delete or refresh the current page may no longer exist
  const visible = shown.slice((page - 1) * pageSize, page * pageSize);
  // Give every provider a fixed column so the delivery results line up across rows (and across pages)
  const slots = Math.max(1, ...loadedMessages.map((m) => Object.keys(m.delivered).length));
  $("messages-list").style.setProperty("--slots", slots);
  // Every row's time column is as wide as the longest label, so today's time and dated ones take the same space
  $("messages-list").style.setProperty("--when-ch", Math.max(5, ...loadedMessages.map((m) => whenLabel(m).length)));
  $("messages-list").replaceChildren(...visible.map(messageCard));
  $("search-reset").hidden = !filtering;
  $("search-count").textContent = filtering ? t("search.count", { n: shown.length, total: loadedMessages.length }) : "";
  $("messages-nomatch").hidden = shown.length > 0 || loadedMessages.length === 0;
  $("messages-empty").hidden = messagesTotal > 0;
  renderPager(shown.length, last);
}

// Loads the newest chunk and merges it with what is already loaded (by id); older messages that were loaded with "Load more" are kept
async function loadMessages(append = false) {
  const offset = append ? loadedMessages.length : 0;
  const data = await api(`/api/messages?limit=${MESSAGE_CHUNK}&offset=${offset}`);
  const known = new Set(data.items.map((m) => m.id));
  loadedMessages = append
    ? [...loadedMessages, ...data.items.filter((m) => !loadedMessages.some((x) => x.id === m.id))]
    : [...data.items, ...loadedMessages.slice(MESSAGE_CHUNK).filter((m) => !known.has(m.id))];
  messagesTotal = data.total;
  messagesEditable = data.editable;
  renderMessages();
}

const refilter = () => { page = 1; renderMessages(); };
for (const id of ["search-keyword", "search-from", "search-to"]) $(id).addEventListener("input", refilter);
$("search-form").addEventListener("reset", () => setTimeout(refilter));  // After the browser has cleared the fields

for (const n of PAGE_SIZES) $("page-size").add(new Option(String(n), String(n)));
$("page-size").value = String(pageSize);
$("page-size").addEventListener("change", () => {
  pageSize = Number($("page-size").value);
  page = 1;
  try {
    localStorage.setItem(PAGE_SIZE_KEY, String(pageSize));
  } catch {}
  renderMessages();
});
$("search-form").addEventListener("submit", (event) => event.preventDefault());

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
    loadedMessages = [];
    page = 1;
    await refresh();
  } catch (err) {
    if (err instanceof Unauthorized) showLogin();
    else msg.textContent = t("msg.clearFailed", { e: errText(err) });
  } finally {
    btn.disabled = false;
  }
});

$("messages-more").addEventListener("click", () => loadMessages(true).catch(handleError));

// ---------- Log ----------
async function loadLogs() {
  const logs = await api(`/api/logs?level=${$("log-level").value}&limit=200`);
  $("log-list").replaceChildren(
    ...(logs.length ? logs : [{ time: 0, level: "INFO", message: t("log.empty") }]).map((l) =>
      el("div", { class: `log-row ${l.level}${isUnreadError(l) ? " unread" : ""}` },
        el("span", { class: "t" }, l.time ? fmtEpoch(l.time) : ""),
        el("span", { class: "lv" }, l.level),
        el("span", { class: "log-text" }, l.message),
      ),
    ),
  );
  $("ack-errors").disabled = !errorsUnread;
  $("ack-errors").title = errorsUnread ? "" : t("log.noUnread");
  if (errorsUnread) $("ack-note").textContent = "";
}

const isUnreadError = (l) => errorsAckAt !== null && ["ERROR", "CRITICAL"].includes(l.level) && l.time > errorsAckAt;

$("log-level").addEventListener("change", loadLogs);

// ---------- Settings ----------
async function loadSettings() {
  const s = await api("/api/settings");
  const form = $("settings-form");
  form.poll_interval_seconds.value = s.poll_interval_seconds;
  for (const key of ["kick_existing_login", "allow_lan_access", "notify_errors", "allow_edit_messages"]) form[key].checked = s[key];
  editEnabled = s.allow_edit_messages;
}

let editEnabled = false;  // Whether message editing is on as saved (turning it on needs the password)

// Ask for the Dashboard password; null if cancelled
async function askPassword() {
  const input = $("password-input");
  input.value = "";
  const result = await showDialog($("password-dialog"));
  const password = input.value;
  input.value = "";
  return result === "ok" ? password : null;
}

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const msg = $("settings-msg");
  msg.className = "small";
  const body = {
    poll_interval_seconds: Number(form.poll_interval_seconds.value),
    kick_existing_login: form.kick_existing_login.checked,
    allow_lan_access: form.allow_lan_access.checked,
    notify_errors: form.notify_errors.checked,
    allow_edit_messages: form.allow_edit_messages.checked,
  };
  if (body.allow_edit_messages && !editEnabled) {
    // Turning editing on needs the password once
    body.edit_password = await askPassword();
    if (body.edit_password === null) {
      form.allow_edit_messages.checked = false;
      return;
    }
  }
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
    editEnabled = body.allow_edit_messages;
    msg.textContent = t("set.saved");
    msg.classList.add("status-on");
    refresh();
  } catch (err) {
    msg.textContent = t("set.saveFailed", { e: errText(err) });
    msg.classList.add("status-off");
  }
});

// ---------- Pages and refresh ----------
// Main page (status + messages), App Log and Settings are separate pages, chosen by the URL hash so the browser's back button works
const PAGES = { "#/logs": "logs", "#/settings": "settings" };
let activePage = "main";

function route() {
  activePage = PAGES[location.hash] ?? "main";
  for (const name of ["main", "logs", "settings"]) $(`page-${name}`).hidden = name !== activePage;
  for (const name of ["main", "logs", "settings"]) {
    const link = $(`nav-${name}`);
    activePage === name ? link.setAttribute("aria-current", "page") : link.removeAttribute("aria-current");
  }
  window.scrollTo(0, 0);
  updateToTop();
  const load = { main: loadMessages, logs: loadLogs, settings: loadSettings }[activePage];
  load().catch(handleError);
}

window.addEventListener("hashchange", () => !$("app-view").hidden && route());

// Back-to-top button (main page only): shown once the top of the messages has scrolled out of view
function updateToTop() {
  $("to-top").hidden = activePage !== "main" || $("messages-top").getBoundingClientRect().top >= 0;
}

window.addEventListener("scroll", updateToTop, { passive: true });
$("to-top").addEventListener("click", () => {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  $("messages-top").scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
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
    // The Settings page may be mid-edit; it is only loaded when opened and after saving
    if (activePage === "main") await loadMessages();
    else if (activePage === "logs") await loadLogs();
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
    setDetailsOpen(!$("band-details").hidden, false);  // Redraw the toggle's label in the new language
    if (!$("app-view").hidden) refresh(); // Redraw generated content (status, messages, log) in the new language
  });
}
applyI18n();

api("/api/auth/me").then(showApp, () => showLogin());
