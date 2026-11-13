// DOM helpers, icons, formatting, dialogs and toasts. Text is always set as text, never as HTML.

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  let value;
  if (props) {
    for (const [key, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (key === "class") el.className = v;
      else if (key === "dataset") Object.assign(el.dataset, v);
      else if (key === "text") el.textContent = v;
      else if (key === "value") value = v;
      else if (key === "checked") el.checked = Boolean(v);
      else if (key === "ref") v(el);
      else if (key.startsWith("on") && typeof v === "function") el.addEventListener(key.slice(2).toLowerCase(), v);
      else if (v === true) el.setAttribute(key, "");
      else el.setAttribute(key, String(v));
    }
  }
  append(el, children);
  if (value !== undefined) el.value = value;
  return el;
}

export function append(el, children) {
  for (const child of [children].flat(Infinity)) {
    if (child == null || child === false || child === "") continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

// ---------------------------------------------------------------------------- icons (outline, 1.5px)
const ICONS = {
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  profiles: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  settings: '<path d="M21 4h-7"/><path d="M10 4H3"/><path d="M21 12h-9"/><path d="M8 12H3"/><path d="M21 20h-5"/><path d="M12 20H3"/><path d="M14 2v4"/><path d="M8 10v4"/><path d="M16 18v4"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
  paperclip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  copy: '<rect width="13" height="13" x="9" y="9" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  back: '<path d="m15 18-6-6 6-6"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  file: '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5z"/><path d="M14 2v6h6"/>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
  menu: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/>',
  alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/>',
  monitor: '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>',
  key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3L22 7l-3-3"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  send: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  reply: '<path d="m9 17-5-5 5-5"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>',
};

export function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", `icon ${cls}`.trim());
  svg.innerHTML = ICONS[name] || "";
  return svg;
}

export function brandMark() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 32 32");
  svg.setAttribute("class", "brand-mark");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML =
    '<rect class="bm-bg" width="32" height="32" rx="8"/>' +
    '<path class="bm-line" d="M8 11.5h16v10a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 8 21.5z"/>' +
    '<path class="bm-line" d="m8.5 12 7.5 5.5 7.5-5.5"/>' +
    '<circle class="bm-dot" cx="24" cy="10" r="4.5"/>';
  return svg;
}

// ---------------------------------------------------------------------------- formatting
const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

export function relTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  const now = new Date();
  const seconds = (now - date) / 1000;
  if (seconds < 45) return "Just now";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  if (sameDay(date, now)) return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(date, yesterday)) return "Yesterday";
  if (seconds < 6 * 86400) return date.toLocaleDateString([], { weekday: "short" });
  if (date.getFullYear() === now.getFullYear()) return date.toLocaleDateString([], { day: "numeric", month: "short" });
  return date.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

export function fullTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export function bytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

export function plural(n, one, many = `${one}s`) {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

export function addrText(a) {
  return a.name ? `${a.name} <${a.email}>` : a.email;
}

export function addrNode(a) {
  if (!a.name) return h("span", { class: "addr" }, a.email);
  return h("span", { class: "addr" }, a.name, " ", h("span", { class: "addr-mail" }, `<${a.email}>`));
}

export const STATUS_LABELS = {
  pending: "Pending",
  sending: "Sending",
  sent: "Sent",
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

export function statusPill(status) {
  return h("span", { class: `pill ${status}` }, STATUS_LABELS[status] || status);
}

// ---------------------------------------------------------------------------- feedback
export function toast(message, kind = "info") {
  const host = document.getElementById("toasts");
  const el = h("div", { class: `toast ${kind}` }, icon(kind === "error" ? "alert" : "check"), h("span", {}, message));
  host.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

export async function withBusy(button, fn) {
  if (button.disabled) return undefined;
  const original = [...button.childNodes];
  const width = button.offsetWidth;
  button.disabled = true;
  button.style.minWidth = `${width}px`;
  button.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }));
  button.setAttribute("aria-busy", "true");
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.style.minWidth = "";
    button.replaceChildren(...original);
  }
}

export function copyText(text, label = "Copied") {
  const done = () => toast(label);
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, done) {
  const area = h("textarea", { class: "sr-only", readonly: true });
  area.value = text;
  document.body.append(area);
  area.select();
  try { document.execCommand("copy"); done(); } catch { toast("Copy failed. Select the text and copy it manually.", "error"); }
  area.remove();
}

/**
 * Modal dialog. `actions`: [{label, value, kind: "primary"|"danger"|"", autofocus}].
 * Resolves with the chosen action's value, or null when dismissed.
 */
export function openDialog({ title, body, actions, onAction }) {
  return new Promise((resolve) => {
    const dialog = h("dialog", { class: "modal", "aria-labelledby": "dialog-title" });
    const foot = h("div", { class: "modal-foot" });
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      dialog.close();
      dialog.remove();
      resolve(value);
    };
    for (const action of actions) {
      const btn = h("button", { class: `btn ${action.kind || ""}`, type: "button" }, action.label);
      btn.addEventListener("click", async () => {
        if (onAction && action.value !== null) {
          const ok = await withBusy(btn, () => onAction(action.value));
          if (ok === false) return;
        }
        finish(action.value);
      });
      if (action.autofocus) queueMicrotask(() => btn.focus());
      foot.append(btn);
    }
    dialog.append(h("div", { class: "modal-body" }, h("h2", { id: "dialog-title" }, title), body), foot);
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); finish(null); });
    dialog.addEventListener("click", (e) => { if (e.target === dialog) finish(null); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

export async function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  const value = await openDialog({
    title,
    body: typeof message === "string" ? h("p", {}, message) : message,
    actions: [
      { label: "Cancel", value: null },
      { label: confirmLabel, value: true, kind: danger ? "danger" : "primary", autofocus: true },
    ],
  });
  return value === true;
}

// ---------------------------------------------------------------------------- form pieces
let uid = 0;
export const nextId = (prefix = "f") => `${prefix}-${++uid}`;

export function field({ label, control, help, error, id }) {
  const controlId = id || control.id || nextId();
  control.id = controlId;
  const helpId = help ? `${controlId}-help` : null;
  if (helpId) control.setAttribute("aria-describedby", helpId);
  return h(
    "div",
    { class: "field" },
    h("label", { for: controlId }, label),
    control,
    help ? h("div", { class: "help", id: helpId }, help) : null,
    error ? h("div", { class: "error-text" }, error) : null,
  );
}

export function switchControl({ label, checked, onChange, description }) {
  const input = h("input", { type: "checkbox", role: "switch", checked });
  input.addEventListener("change", () => onChange(input.checked));
  return h(
    "label",
    { class: "switch" },
    input,
    h("span", { class: "track", "aria-hidden": "true" }),
    h("span", {}, label, description ? h("span", { class: "check-desc" }, description) : null),
  );
}

export function skeleton(width, height = 12) {
  const el = h("span", { class: "skeleton" });
  el.style.width = typeof width === "number" ? `${width}px` : width;
  el.style.height = `${height}px`;
  return el;
}

export function errorBlock(title, message, onRetry) {
  return h(
    "div",
    { class: "error-block", role: "alert" },
    icon("alert"),
    h(
      "div",
      {},
      h("div", { class: "error-title" }, title),
      h("div", { class: "error-msg" }, message),
      onRetry ? h("button", { class: "text-btn", type: "button", onClick: onRetry }, "Try again") : null,
    ),
  );
}

export function isTyping(event) {
  const t = event.target;
  return t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
}
