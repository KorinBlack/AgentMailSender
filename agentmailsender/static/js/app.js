import { api, onUnauthorized, setCsrf } from "./api.js";
import { brandMark, clear, errorBlock, field, h, icon, withBusy } from "./ui.js";
import { createInbox } from "./views/inbox.js";
import { createProfiles } from "./views/profiles.js";
import { createSettings } from "./views/settings.js";

const root = document.getElementById("root");
const POLL_MS = 15000;

const state = { me: null, meta: null, stats: null };
const statsListeners = new Set();
let shell = null;
let current = null; // { name, view }
let currentHash = null;
let pollTimer = null;
let pageTitle = "";

const app = {
  state,
  navigate(hash, { replace = false } = {}) {
    if (replace) {
      history.replaceState(null, "", hash);
      route();
    } else if (location.hash === hash) {
      route();
    } else {
      location.hash = hash;
    }
  },
  refreshStats,
  onStats(fn) {
    statsListeners.add(fn);
    return () => statsListeners.delete(fn);
  },
  setTitle(title) {
    pageTitle = title;
    updateDocumentTitle();
  },
};

// ---------------------------------------------------------------------------- routing
const VIEWS = { inbox: createInbox, profiles: createProfiles, settings: createSettings };

function parseRoute(hash) {
  let m;
  if ((m = hash.match(/^#\/inbox(?:\/([a-f0-9]{32}))?\/?$/))) return { view: "inbox", id: m[1] || null };
  if ((m = hash.match(/^#\/profiles(?:\/(new|\d+))?\/?$/))) return { view: "profiles", id: m[1] || null };
  if ((m = hash.match(/^#\/settings(?:\/(general|notifications|operators|account))?(?:\/(new|\d+))?\/?$/))) {
    return { view: "settings", section: m[1] || "general", id: m[2] || null };
  }
  return null;
}

function route() {
  if (!shell) return;
  const hash = location.hash || "#/inbox";
  if (current && currentHash && hash !== currentHash && current.view.canLeave && !current.view.canLeave()) {
    if (!window.confirm("You have unsaved changes. Leave without saving?")) {
      history.replaceState(null, "", currentHash);
      return;
    }
  }
  const r = parseRoute(hash);
  if (!r) {
    history.replaceState(null, "", "#/inbox");
    route();
    return;
  }
  currentHash = hash;
  updateNav(r);
  shell.sidebar.classList.remove("open");
  if (current && current.name === r.view && current.view.update) {
    current.view.update(r);
    return;
  }
  current?.view.destroy?.();
  const view = VIEWS[r.view](app, r);
  clear(shell.main, view.el);
  current = { name: r.view, view };
}

window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", (event) => {
  if (current?.view.canLeave && !current.view.canLeave()) event.preventDefault();
});

// ---------------------------------------------------------------------------- shell
function navLink(href, iconName, label, extra) {
  return h("a", { class: "nav-item", href }, icon(iconName), h("span", {}, label), extra);
}

function themeSwitch() {
  const options = [
    ["system", "monitor", "Use the system theme"],
    ["light", "sun", "Light theme"],
    ["dark", "moon", "Dark theme"],
  ];
  let saved = "system";
  try { saved = localStorage.getItem("ams-theme") || "system"; } catch { /* ignore */ }
  const wrap = h("div", { class: "theme-switch", role: "group", "aria-label": "Theme" });
  const buttons = options.map(([value, iconName, label]) => {
    const btn = h("button", { type: "button", "aria-label": label, title: label, "aria-pressed": String(saved === value) }, icon(iconName, "sm"));
    btn.addEventListener("click", () => {
      try { value === "system" ? localStorage.removeItem("ams-theme") : localStorage.setItem("ams-theme", value); } catch { /* ignore */ }
      if (value === "system") delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = value;
      buttons.forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    });
    return btn;
  });
  wrap.append(...buttons);
  return wrap;
}

function renderShell() {
  const count = h("span", { class: "count", "aria-label": "requests waiting" });
  const links = {
    inbox: navLink("#/inbox", "inbox", "Approvals", count),
    profiles: navLink("#/profiles", "profiles", "Profiles"),
    settings: navLink("#/settings/general", "settings", "Settings"),
  };
  const sub = {
    general: h("a", { class: "nav-item", href: "#/settings/general" }, "General"),
    notifications: h("a", { class: "nav-item", href: "#/settings/notifications" }, "Notifications"),
    operators: h("a", { class: "nav-item", href: "#/settings/operators" }, "Operators"),
    account: h("a", { class: "nav-item", href: "#/settings/account" }, "Account"),
  };
  const subNav = h("div", { class: "nav-sub" }, Object.values(sub));
  const logout = h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Sign out", title: "Sign out" }, icon("logout"));
  logout.addEventListener("click", () => withBusy(logout, signOut));

  const brand = () => h("a", { class: "brand", href: "#/inbox" }, brandMark(), h("span", { class: "brand-name" }, "AgentMailSender"));
  const sidebar = h(
    "aside",
    { class: "sidebar", id: "sidebar" },
    brand(),
    h("nav", { class: "nav", "aria-label": "Main" }, links.inbox, links.profiles, links.settings, subNav),
    h(
      "div",
      { class: "sidebar-foot" },
      themeSwitch(),
      h(
        "div",
        { class: "user-row" },
        h("span", { class: "avatar", "aria-hidden": "true" }, (state.me.username || "?").slice(0, 2)),
        h("span", { class: "who", title: state.me.username }, state.me.username),
        logout,
      ),
    ),
  );
  const menuBtn = h("button", { class: "btn ghost icon-only", type: "button", "aria-label": "Open menu", "aria-controls": "sidebar", "aria-expanded": "false" }, icon("menu"));
  menuBtn.addEventListener("click", () => {
    const open = !sidebar.classList.contains("open");
    sidebar.classList.toggle("open", open);
    menuBtn.setAttribute("aria-expanded", String(open));
  });
  const closeBtn = h("button", { class: "btn ghost icon-only", type: "button", "aria-label": "Close menu" }, icon("x"));
  closeBtn.addEventListener("click", () => sidebar.classList.remove("open"));
  const mobileClose = h("div", { class: "mobile-close" }, closeBtn);
  sidebar.prepend(mobileClose);

  const topbar = h("header", { class: "topbar" }, brand(), menuBtn);
  const main = h("main", { class: "sheet", id: "main" });
  clear(root, h("div", { class: "app" }, topbar, sidebar, main));
  shell = { main, sidebar, links, sub, subNav, count };
}

function updateNav(r) {
  for (const [name, link] of Object.entries(shell.links)) {
    const active = name === r.view;
    link.classList.toggle("active", active && !(name === "settings"));
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  shell.links.settings.classList.toggle("active", r.view === "settings");
  shell.subNav.classList.toggle("hidden", r.view !== "settings");
  for (const [name, link] of Object.entries(shell.sub)) {
    const active = r.view === "settings" && r.section === name;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

function updateDocumentTitle() {
  const pending = state.stats?.counts?.pending || 0;
  document.title = `${pending ? `(${pending}) ` : ""}${pageTitle ? `${pageTitle} · ` : ""}AgentMailSender`;
}

// ---------------------------------------------------------------------------- stats polling
async function refreshStats() {
  if (!state.me) return;
  try {
    const stats = await api.get("api/admin/stats");
    const previous = state.stats;
    state.stats = stats;
    if (shell) shell.count.textContent = stats.counts.pending ? String(stats.counts.pending) : "";
    updateDocumentTitle();
    statsListeners.forEach((fn) => fn(stats, previous));
  } catch {
    /* transient: the next poll retries */
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => { if (!document.hidden) refreshStats(); }, POLL_MS);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

document.addEventListener("visibilitychange", () => { if (!document.hidden && shell) refreshStats(); });

// ---------------------------------------------------------------------------- session
async function signOut() {
  try { await api.post("api/auth/logout"); } catch { /* the session may already be gone */ }
  state.me = null;
  showLogin();
}

function teardown() {
  stopPolling();
  current?.view.destroy?.();
  current = null;
  currentHash = null;
  shell = null;
  statsListeners.clear();
}

function showLogin(message = "") {
  teardown();
  document.title = "Sign in · AgentMailSender";
  const status = h("div", { class: "form-status", role: "status" }, message);
  const user = h("input", { class: "input", name: "username", autocomplete: "username", required: true, autocapitalize: "none", spellcheck: "false" });
  const pass = h("input", { class: "input", type: "password", name: "password", autocomplete: "current-password", required: true });
  const submit = h("button", { class: "btn primary", type: "submit" }, "Sign in");
  const form = h(
    "form",
    { novalidate: true },
    field({ label: "Username", control: user }),
    field({ label: "Password", control: pass }),
    submit,
    status,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.textContent = "";
    if (!user.value.trim() || !pass.value) {
      status.textContent = "Enter your username and password.";
      (user.value.trim() ? pass : user).focus();
      return;
    }
    await withBusy(submit, async () => {
      try {
        const res = await api.post("api/auth/login", { username: user.value.trim(), password: pass.value });
        setCsrf(res.csrf_token);
        state.me = res;
        await start();
      } catch (err) {
        status.textContent = err.message;
        pass.select();
      }
    });
  });
  clear(
    root,
    h(
      "div",
      { class: "login-wrap" },
      h(
        "main",
        { class: "login" },
        h("div", { class: "brand" }, brandMark(), h("span", { class: "brand-name" }, "AgentMailSender")),
        h("div", {}, h("h1", {}, "Sign in"), h("p", { class: "muted" }, "Review what your agents want to send.")),
        form,
      ),
    ),
  );
  user.focus();
}

function showFatal(err) {
  teardown();
  clear(
    root,
    h(
      "div",
      { class: "login-wrap" },
      h("main", { class: "login" }, errorBlock("AgentMailSender could not start.", err.message, () => location.reload())),
    ),
  );
}

async function start() {
  try {
    state.meta = await api.get("api/admin/meta");
  } catch (err) {
    showFatal(err);
    return;
  }
  renderShell();
  route();
  await refreshStats();
  startPolling();
}

async function boot() {
  onUnauthorized(() => {
    if (state.me) {
      state.me = null;
      showLogin("Your session ended. Sign in again.");
    }
  });
  try {
    const me = await api.get("api/auth/me");
    setCsrf(me.csrf_token);
    state.me = me;
  } catch (err) {
    if (err.status === 401) showLogin();
    else showFatal(err);
    return;
  }
  await start();
}

boot();
