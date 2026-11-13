// Approvals: request list (left) and reading pane with the decision bar (right).
import { api } from "../api.js";
import {
  addrNode, addrText, bytes, clear, copyText, errorBlock, field, fullTime, h, icon, isTyping, openDialog,
  plural, relTime, skeleton, statusPill, toast, withBusy,
} from "../ui.js";

const TABS = [
  { key: "pending", label: "Pending", counted: true },
  { key: "sent", label: "Sent" },
  { key: "rejected", label: "Rejected" },
  { key: "failed", label: "Failed", counted: true },
  { key: "all", label: "All" },
];
const PAGE = 50;
const EVENT_TEXT = {
  created: "Submitted",
  approved: "Approved",
  rejected: "Rejected",
  sent: "Delivered to the mail server",
  failed: "Delivery failed",
  retried: "Delivery retried",
  cancelled: "Cancelled",
};
const EMPTY_TEXT = {
  pending: "No requests are waiting for review.",
  sent: "No e-mail has been sent yet.",
  rejected: "No request has been rejected.",
  failed: "No delivery has failed.",
  all: "No agent has submitted a request yet.",
};

function loadTab() {
  try { return sessionStorage.getItem("ams-inbox-tab") || "pending"; } catch { return "pending"; }
}
function saveTab(tab) {
  try { sessionStorage.setItem("ams-inbox-tab", tab); } catch { /* ignore */ }
}
const kbd = (key) => h("span", { class: "kbd", "aria-hidden": "true" }, key);

export function createInbox(app, route) {
  const s = {
    tab: TABS.some((t) => t.key === loadTab()) ? loadTab() : "pending",
    q: "",
    items: [],
    total: 0,
    loading: true,
    error: null,
    selectedId: route.id,
    detail: null,
    detailLoading: false,
    detailError: null,
    rejectOpen: false,
    rejectDraft: "",
    remote: false,
    bodyTab: "html",
  };
  let listToken = 0;
  let detailToken = 0;
  let searchTimer = null;

  // ------------------------------------------------------------------ list pane
  const subline = h("p", { class: "subline" });
  const refreshBtn = h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Refresh", title: "Refresh" }, icon("refresh"));
  refreshBtn.addEventListener("click", () => withBusy(refreshBtn, async () => { await Promise.all([loadList(), app.refreshStats()]); }));
  const tabButtons = new Map();
  const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "Filter by status" });
  for (const t of TABS) {
    const count = h("span", { class: "count" });
    const btn = h("button", { class: "tab", type: "button", role: "tab", "aria-selected": String(t.key === s.tab) }, t.label, count);
    btn.addEventListener("click", () => {
      if (s.tab === t.key) return;
      s.tab = t.key;
      saveTab(t.key);
      loadList();
    });
    tabButtons.set(t.key, { btn, count, counted: t.counted });
    tabs.append(btn);
  }
  const search = h("input", { class: "input", type: "search", placeholder: "Search subject, recipient, agent…", "aria-label": "Search requests", autocomplete: "off" });
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { s.q = search.value.trim(); loadList(); }, 250);
  });
  search.addEventListener("keydown", (e) => { if (e.key === "Escape") { search.value = ""; s.q = ""; loadList(); search.blur(); } });

  const list = h("div", { class: "list", role: "list" });
  const listFoot = h("div", { class: "list-foot" });
  const listScroll = h("div", { class: "pane-scroll" }, list, listFoot);
  const listPane = h(
    "section",
    { class: "pane", "aria-label": "Requests" },
    h(
      "div",
      { class: "pane-head" },
      h("div", { class: "title-row" }, h("h1", {}, "Approvals"), refreshBtn),
      subline,
      h("div", { class: "input-icon" }, icon("search"), search),
      tabs,
    ),
    listScroll,
  );

  // ------------------------------------------------------------------ reader pane
  const readerScroll = h("div", { class: "reader-scroll" });
  const actionSlot = h("div");
  const reader = h("section", { class: "pane reader", "aria-label": "Selected request" }, readerScroll, actionSlot);
  const split = h("div", { class: "split" }, listPane, reader);

  // ------------------------------------------------------------------ data
  async function loadList({ silent = false, more = false } = {}) {
    const token = ++listToken;
    if (!silent && !more) {
      s.loading = true;
      renderList();
    }
    try {
      const params = new URLSearchParams({ status: s.tab, limit: String(PAGE), offset: String(more ? s.items.length : 0) });
      if (s.q) params.set("q", s.q);
      const res = await api.get(`api/admin/requests?${params}`);
      if (token !== listToken) return;
      s.items = more ? [...s.items, ...res.items] : res.items;
      s.total = res.total;
      s.error = null;
    } catch (err) {
      if (token !== listToken) return;
      s.error = err;
    }
    s.loading = false;
    renderList();
    if (silent && s.detail && !s.rejectOpen) {
      const row = s.items.find((i) => i.id === s.detail.id);
      if (row && row.updated_at !== s.detail.updated_at) loadDetail({ silent: true });
    }
  }

  async function loadDetail({ silent = false } = {}) {
    const id = s.selectedId;
    split.classList.toggle("show-detail", Boolean(id));
    if (!silent) {
      s.rejectOpen = false;
      s.rejectDraft = "";
      s.remote = false;
      s.bodyTab = "html";
    }
    if (!id) {
      s.detail = null;
      s.detailError = null;
      renderReader();
      return;
    }
    const token = ++detailToken;
    if (!silent) {
      s.detailLoading = true;
      s.detailError = null;
      renderReader();
    }
    try {
      const d = await api.get(`api/admin/requests/${id}`);
      if (token !== detailToken) return;
      s.detail = d;
      s.detailError = null;
    } catch (err) {
      if (token !== detailToken) return;
      s.detail = null;
      s.detailError = err;
    }
    s.detailLoading = false;
    renderReader();
    if (!silent) readerScroll.scrollTop = 0;
  }

  // ------------------------------------------------------------------ list rendering
  function sublineText() {
    const n = s.total;
    if (s.loading && !s.items.length) return "Loading…";
    if (s.q) return `${plural(n, "request")} matching “${s.q}”`;
    switch (s.tab) {
      case "pending": return n ? `${plural(n, "request")} waiting for review` : "Nothing waiting for review";
      case "sent": return `${plural(n, "e-mail")} sent`;
      case "rejected": return `${plural(n, "request")} rejected`;
      case "failed": return n ? `${plural(n, "delivery", "deliveries")} failed: retry or reject` : "No failed deliveries";
      default: return plural(n, "request");
    }
  }

  function updateTabs() {
    const counts = app.state.stats?.counts || {};
    for (const [key, { btn, count, counted }] of tabButtons) {
      btn.setAttribute("aria-selected", String(key === s.tab));
      count.textContent = counted && counts[key] ? String(counts[key]) : "";
    }
  }

  function rowEl(item) {
    const first = item.to[0];
    const others = Math.max(0, item.to.length - 1) + item.cc_count + item.bcc_count;
    const recipient = first ? first.name || first.email : "Bcc only";
    const selected = item.id === s.selectedId;
    return h(
      "a",
      {
        class: `row${item.status === "pending" ? " unread" : ""}${selected ? " selected" : ""}`,
        href: `#/inbox/${item.id}`,
        role: "listitem",
        "aria-current": selected ? "true" : null,
        dataset: { id: item.id },
      },
      h(
        "span",
        { class: "row-line" },
        h("span", { class: "row-title" }, item.subject),
        h("time", { class: "row-time", datetime: item.created_at, title: fullTime(item.created_at) }, relTime(item.created_at)),
      ),
      h(
        "span",
        { class: "row-line" },
        h("span", { class: "row-meta" }, h("strong", {}, item.profile_name), " to ", recipient, others ? ` +${others}` : ""),
        item.attachment_count ? h("span", { title: plural(item.attachment_count, "attachment") }, icon("paperclip", "sm")) : null,
        s.tab === "all" ? statusPill(item.status) : null,
      ),
    );
  }

  function renderList() {
    subline.textContent = sublineText();
    updateTabs();
    if (s.loading && !s.items.length) {
      clear(
        list,
        Array.from({ length: 7 }, () =>
          h("div", { class: "row-skeleton", "aria-hidden": "true" }, skeleton("72%", 14), skeleton("44%", 12)),
        ),
      );
      clear(listFoot);
      return;
    }
    if (s.error) {
      clear(list, h("div", { class: "inline-error" }, icon("alert"), h("span", {}, "Couldn't load requests. "), h("button", { class: "text-btn", type: "button", onClick: () => loadList() }, "Try again")));
      clear(listFoot);
      return;
    }
    if (!s.items.length) {
      clear(list, h("p", { class: "empty-line" }, s.q ? `No requests match “${s.q}”.` : EMPTY_TEXT[s.tab]));
      clear(listFoot);
      return;
    }
    clear(list, s.items.map(rowEl));
    const foot = [h("span", { class: "num" }, `1–${s.items.length} of ${s.total.toLocaleString()}`)];
    if (s.items.length < s.total) {
      const more = h("button", { class: "btn sm", type: "button" }, "Load more");
      more.addEventListener("click", () => withBusy(more, () => loadList({ more: true })));
      foot.push(more);
    }
    clear(listFoot, foot);
  }

  function markSelection() {
    for (const row of list.querySelectorAll(".row")) {
      const selected = row.dataset.id === s.selectedId;
      row.classList.toggle("selected", selected);
      if (selected) {
        row.setAttribute("aria-current", "true");
        row.scrollIntoView({ block: "nearest" });
      } else {
        row.removeAttribute("aria-current");
      }
    }
  }

  // ------------------------------------------------------------------ reader rendering
  function renderReader() {
    clear(actionSlot);
    if (!s.selectedId) {
      clear(
        readerScroll,
        h(
          "div",
          { class: "reader-empty" },
          h("p", {}, s.items.length ? "Select a request to read it and decide." : "When an agent submits an e-mail, it appears here for review."),
          h("p", { class: "hint" }, kbd("J"), kbd("K"), " move through the list · ", kbd("A"), " approve · ", kbd("R"), " reject · ", kbd("/"), " search"),
        ),
      );
      return;
    }
    if (s.detailLoading && (!s.detail || s.detail.id !== s.selectedId)) {
      clear(
        readerScroll,
        h(
          "div",
          { class: "reader-inner", "aria-busy": "true" },
          h("div", { class: "reader-top" }, skeleton(64, 20), skeleton(80, 14)),
          skeleton("60%", 24),
          h("div", { class: "kv" }, ...[1, 2, 3, 4].flatMap(() => [skeleton(56, 12), skeleton("40%", 12)])),
          h("div", { class: "note" }, skeleton(180, 12), skeleton("90%", 14), skeleton("70%", 14)),
          skeleton("100%", 220),
        ),
      );
      return;
    }
    if (s.detailError) {
      const notFound = s.detailError.status === 404;
      clear(
        readerScroll,
        h(
          "div",
          { class: "reader-inner" },
          backButton(),
          errorBlock(notFound ? "This request does not exist." : "Couldn't load this request.", notFound ? "It may have been removed." : s.detailError.message, notFound ? null : () => loadDetail()),
        ),
      );
      return;
    }
    const d = s.detail;
    if (!d) return;
    clear(readerScroll, h("article", { class: "reader-inner" }, ...readerContent(d)));
    const bar = actionBar(d);
    if (bar) actionSlot.append(bar);
  }

  function backButton() {
    return h("a", { class: "btn ghost sm icon-only back-btn", href: "#/inbox", "aria-label": "Back to the list" }, icon("back"));
  }

  function addrList(list) {
    return h("div", { class: "addr-list" }, list.map(addrNode));
  }

  function readerContent(d) {
    const nodes = [];
    const copyId = h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Copy request ID", title: "Copy request ID" }, icon("copy", "sm"));
    copyId.addEventListener("click", () => copyText(d.id, "Request ID copied"));
    nodes.push(
      h(
        "div",
        { class: "reader-top" },
        backButton(),
        statusPill(d.status),
        h("span", { class: "id-chip", title: d.id }, d.id.slice(0, 8), copyId),
        h("span", { class: "spacer" }),
        h("a", { class: "btn ghost sm", href: `api/admin/requests/${d.id}/eml`, download: `request-${d.id.slice(0, 8)}.eml`, title: "Download the message as it will be sent" }, icon("download"), "Download .eml"),
      ),
    );
    nodes.push(h("h2", { class: "subject" }, d.subject));

    const kv = h("dl", { class: "kv" });
    const row = (label, value) => kv.append(h("dt", {}, label), h("dd", {}, value));
    row("From", addrNode(d.from));
    if (d.to.length) row("To", addrList(d.to));
    if (d.cc.length) row("Cc", addrList(d.cc));
    if (d.bcc.length) row("Bcc", addrList(d.bcc));
    if (d.reply_to.length) row("Reply-To", addrList(d.reply_to));
    row("Agent", [d.profile_name, d.api_key ? h("span", { class: "muted" }, ` · key “${d.api_key.label || d.api_key.prefix}”`) : null]);
    row("Requested", h("time", { datetime: d.created_at }, fullTime(d.created_at)));
    nodes.push(kv);

    if (d.supersedes) {
      nodes.push(
        h("p", { class: "note-label" }, icon("reply", "sm"), "Revision of ", h("a", { href: `#/inbox/${d.supersedes.id}` }, `“${d.supersedes.subject}”`), statusPill(d.supersedes.status)),
      );
    }
    for (const newer of d.superseded_by) {
      nodes.push(h("p", { class: "note-label" }, icon("reply", "sm"), "Replaced by ", h("a", { href: `#/inbox/${newer.id}` }, `“${newer.subject}”`), statusPill(newer.status)));
    }

    nodes.push(
      h("section", { class: "note", "aria-label": "Agent's note" }, h("div", { class: "note-label" }, "Why the agent wants to send this"), h("div", { class: "note-body" }, d.comment)),
    );

    const decision = decisionBlock(d);
    if (decision) nodes.push(decision);

    if (d.attachments.length) {
      nodes.push(
        h(
          "section",
          { class: "section" },
          h("div", { class: "section-head" }, h("h3", {}, "Attachments"), h("span", { class: "muted small num" }, `${d.attachments.length} · ${bytes(d.attachments.reduce((n, a) => n + a.size, 0))}`)),
          h(
            "ul",
            { class: "files" },
            d.attachments.map((a) =>
              h(
                "li",
                { class: "file" },
                icon(a.content_type.startsWith("image/") ? "image" : "file"),
                h("span", { class: "file-name", title: `${a.filename} · ${a.content_type}` }, a.filename),
                a.inline ? h("span", { class: "pill" }, "Inline") : null,
                h("span", { class: "file-size" }, bytes(a.size)),
                h("a", { class: "btn ghost sm icon-only", href: `api/admin/requests/${d.id}/attachments/${a.id}`, download: a.filename, "aria-label": `Download ${a.filename}`, title: "Download" }, icon("download")),
              ),
            ),
          ),
        ),
      );
    }

    nodes.push(bodySection(d));
    nodes.push(detailsSection(d));
    nodes.push(historySection(d));
    return nodes;
  }

  function decisionBlock(d) {
    const by = d.decided_by ? ` by ${d.decided_by}` : "";
    if (d.status === "failed") {
      return h(
        "div",
        { class: "section" },
        errorBlock("Delivery failed", `${d.error || "Unknown error"}${d.send_attempts > 1 ? ` (attempt ${d.send_attempts})` : ""}`),
        d.decided_by ? h("p", { class: "muted small" }, `Approved${by} · ${fullTime(d.decided_at)}`) : null,
      );
    }
    if (d.status === "rejected") {
      return h(
        "section",
        { class: "decision", "aria-label": "Decision" },
        h("div", { class: "note-label" }, `Rejected${by} · ${fullTime(d.decided_at)}`),
        d.decision_comment ? h("div", { class: "note-body" }, d.decision_comment) : h("div", { class: "muted" }, "No reason was given."),
      );
    }
    if (d.status === "sent" || d.status === "sending") {
      return h(
        "section",
        { class: "decision", "aria-label": "Decision" },
        h("div", { class: "note-label" }, d.status === "sent" ? `Approved${by} · sent ${fullTime(d.sent_at)}` : `Approved${by} · delivery in progress`),
        d.decision_comment ? h("div", { class: "note-body" }, d.decision_comment) : null,
      );
    }
    if (d.status === "cancelled") {
      const last = [...d.events].reverse().find((e) => e.type === "cancelled");
      return h(
        "section",
        { class: "decision", "aria-label": "Decision" },
        h("div", { class: "note-label" }, `Cancelled · ${fullTime(last?.created_at || d.updated_at)}`),
        h("div", { class: "muted" }, last?.message || "Withdrawn by the agent."),
      );
    }
    return null;
  }

  function bodySection(d) {
    const section = h("section", { class: "section", "aria-label": "Message" });
    const head = h("div", { class: "section-head" }, h("h3", {}, "Message"));
    const content = h("div");
    if (d.has_html) {
      const tabsEl = h("div", { class: "tabs", role: "tablist", "aria-label": "Message format" });
      for (const [key, label] of [["html", "Formatted"], ["text", "Plain text"]]) {
        const btn = h("button", { class: "tab", type: "button", role: "tab", "aria-selected": String(s.bodyTab === key) }, label);
        btn.addEventListener("click", () => {
          s.bodyTab = key;
          section.replaceWith(bodySection(d));
        });
        tabsEl.append(btn);
      }
      section.append(head, tabsEl);
    } else {
      section.append(head);
    }
    if (d.has_html && s.bodyTab === "html") {
      if (d.has_remote_content && !s.remote) {
        const load = h("button", { class: "text-btn", type: "button" }, "Load them");
        load.addEventListener("click", () => {
          s.remote = true;
          section.replaceWith(bodySection(d));
        });
        content.append(h("div", { class: "notice" }, icon("image"), h("span", {}, "Remote images are blocked so the sender can't track this review."), load));
        content.classList.add("section");
      }
      const frame = h("iframe", {
        title: "Message body",
        sandbox: "allow-same-origin allow-popups allow-popups-to-escape-sandbox",
        referrerpolicy: "no-referrer",
        src: `api/admin/requests/${d.id}/body.html${s.remote ? "?remote=1" : ""}`,
      });
      frame.addEventListener("load", () => fitFrame(frame));
      content.append(h("div", { class: "body-frame" }, frame));
    } else {
      content.append(h("pre", { class: "body-text" }, d.text || ""));
      if (d.text_generated) content.append(h("p", { class: "muted small" }, "Generated from the HTML body. Mail clients that don't show HTML display this version."));
      content.classList.add("section");
    }
    section.append(content);
    return section;
  }

  function fitFrame(frame) {
    const fit = () => {
      try {
        const doc = frame.contentDocument;
        if (!doc) return;
        const height = Math.max(doc.documentElement.scrollHeight, doc.body ? doc.body.scrollHeight : 0);
        frame.style.height = `${Math.min(Math.max(height, 120), 30000)}px`;
      } catch { /* cross-origin: keep the default height */ }
    };
    fit();
    try {
      frame.contentDocument?.querySelectorAll("img").forEach((img) => img.addEventListener("load", fit));
    } catch { /* ignore */ }
    setTimeout(fit, 250);
  }

  function detailsSection(d) {
    const kv = h("dl", { class: "kv" });
    const row = (label, value) => kv.append(h("dt", {}, label), h("dd", {}, value));
    row("Request ID", h("span", { class: "mono" }, d.id));
    row("Message-ID", h("span", { class: "mono" }, d.message_id));
    row("Signature", d.signature || h("span", { class: "muted" }, "None"));
    if (d.idempotency_key) row("Idempotency", h("span", { class: "mono" }, d.idempotency_key));
    for (const [name, value] of Object.entries(d.headers || {})) row(name, h("span", { class: "mono" }, value));
    if (d.metadata && Object.keys(d.metadata).length) row("Metadata", h("pre", { class: "code-block" }, JSON.stringify(d.metadata, null, 2)));
    return h("details", { class: "more" }, h("summary", {}, "Technical details"), kv);
  }

  function historySection(d) {
    return h(
      "section",
      { class: "section" },
      h("div", { class: "section-head" }, h("h3", {}, "History")),
      h(
        "ol",
        { class: "timeline" },
        d.events.map((e) => {
          const [kind, name] = (e.actor || "").split(":");
          const who = kind === "operator" || kind === "agent" ? ` by ${name}` : "";
          return h(
            "li",
            {},
            h("time", { datetime: e.created_at }, fullTime(e.created_at)),
            h("div", {}, h("div", {}, `${EVENT_TEXT[e.type] || e.type}${who}`), e.message ? h("div", { class: "tl-msg" }, e.message) : null),
          );
        }),
      ),
    );
  }

  // ------------------------------------------------------------------ decisions
  function actionBar(d) {
    if (d.status !== "pending" && d.status !== "failed") return null;
    const bar = h("div", { class: "action-bar", role: "region", "aria-label": "Decision" });
    if (s.rejectOpen) {
      const area = h("textarea", { class: "textarea", id: "reject-reason", maxlength: "10000", placeholder: "What should the agent change? It receives this note with the rejection." });
      area.value = s.rejectDraft;
      area.addEventListener("input", () => { s.rejectDraft = area.value; });
      const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
      cancel.addEventListener("click", closeReject);
      const confirm = h("button", { class: "btn danger", type: "button" }, "Reject request");
      confirm.addEventListener("click", () => reject(confirm));
      area.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); reject(confirm); }
        if (e.key === "Escape") { e.preventDefault(); closeReject(); }
      });
      bar.append(
        h("div", { class: "reject-form" }, h("label", { for: "reject-reason", class: "label-text" }, "Reason for the agent"), area),
        h("div", { class: "action-row" }, h("span", { class: "summary" }, "The agent reads this reason and can submit a revised request. Ctrl+Enter to reject."), cancel, confirm),
      );
      queueMicrotask(() => area.focus());
      return bar;
    }
    const rejectBtn = h("button", { class: "btn", type: "button" }, "Reject", kbd("R"));
    rejectBtn.addEventListener("click", openReject);
    let summary;
    let primary;
    if (d.status === "pending") {
      const recipients = [...d.to, ...d.cc, ...d.bcc];
      const first = recipients[0];
      summary = h("span", { class: "summary" }, "Sends to ", h("strong", {}, first ? first.email : "nobody"), recipients.length > 1 ? ` and ${recipients.length - 1} more` : "", " from ", h("strong", {}, d.from.email));
      primary = h("button", { class: "btn primary", type: "button" }, icon("send"), "Approve and send", kbd("A"));
      primary.addEventListener("click", approve);
    } else {
      summary = h("span", { class: "summary" }, `Delivery failed after ${plural(d.send_attempts, "attempt")}. Fix the profile's delivery settings, then retry.`);
      primary = h("button", { class: "btn primary", type: "button" }, icon("refresh"), "Retry delivery");
      primary.addEventListener("click", () => retry(primary));
    }
    bar.append(h("div", { class: "action-row" }, summary, rejectBtn, primary));
    return bar;
  }

  function openReject() {
    const d = s.detail;
    if (!d || (d.status !== "pending" && d.status !== "failed")) return;
    s.rejectOpen = true;
    renderActionBarOnly();
  }

  function closeReject() {
    s.rejectOpen = false;
    renderActionBarOnly();
  }

  function renderActionBarOnly() {
    clear(actionSlot);
    const bar = s.detail ? actionBar(s.detail) : null;
    if (bar) actionSlot.append(bar);
  }

  async function approve() {
    const d = s.detail;
    if (!d || d.status !== "pending" || document.querySelector("dialog[open]")) return;
    const recipients = [...d.to, ...d.cc, ...d.bcc];
    const who = recipients.length === 1 ? addrText(recipients[0]) : `${addrText(recipients[0])} and ${recipients.length - 1} more`;
    const note = h("textarea", { class: "textarea", rows: "3", maxlength: "10000", placeholder: "Optional. The agent sees this with the result." });
    let result = null;
    const choice = await openDialog({
      title: "Send this e-mail now?",
      body: h("div", { class: "section" }, h("p", {}, `It goes to ${who}, from ${addrText(d.from)}. Once sent it can't be recalled.`), field({ label: "Note for the agent", control: note })),
      actions: [
        { label: "Cancel", value: null },
        { label: "Send now", value: "send", kind: "primary", autofocus: true },
      ],
      onAction: async () => {
        try {
          result = await api.post(`api/admin/requests/${d.id}/approve`, { comment: note.value.trim() || null });
          return true;
        } catch (err) {
          toast(err.message, "error");
          if (err.status === 409) return true;
          return false;
        }
      },
    });
    if (choice !== "send") return;
    if (!result) {
      await loadDetail({ silent: true });
      await Promise.all([loadList({ silent: true }), app.refreshStats()]);
      return;
    }
    if (result.status === "sent") toast(`Sent to ${plural(recipients.length, "recipient")}.`);
    else toast(`Delivery failed: ${result.error || "unknown error"}`, "error");
    await afterDecision(d.id, result);
  }

  async function reject(button) {
    const d = s.detail;
    if (!d) return;
    await withBusy(button, async () => {
      try {
        const result = await api.post(`api/admin/requests/${d.id}/reject`, { comment: s.rejectDraft.trim() || null });
        s.rejectOpen = false;
        s.rejectDraft = "";
        toast(result.decision_comment ? "Rejected. The agent can read your reason." : "Request rejected.");
        await afterDecision(d.id, result);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  async function retry(button) {
    const d = s.detail;
    if (!d) return;
    await withBusy(button, async () => {
      try {
        const result = await api.post(`api/admin/requests/${d.id}/retry`);
        if (result.status === "sent") toast("Delivered.");
        else toast(`Delivery failed again: ${result.error || "unknown error"}`, "error");
        await afterDecision(d.id, result);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  async function afterDecision(id, result) {
    // In a queue view, move on to the next request once this one has left the queue.
    const leftQueue = (s.tab === "pending" || s.tab === "failed") && result.status !== s.tab;
    const index = s.items.findIndex((i) => i.id === id);
    const next = leftQueue && result.status !== "failed" ? s.items[index + 1] || s.items[index - 1] : null;
    s.detail = result;
    app.refreshStats();
    await loadList({ silent: true });
    if (next && s.items.some((i) => i.id === next.id)) {
      app.navigate(`#/inbox/${next.id}`, { replace: true });
    } else {
      renderReader();
    }
  }

  // ------------------------------------------------------------------ keyboard
  function move(step) {
    if (!s.items.length) return;
    const index = s.items.findIndex((i) => i.id === s.selectedId);
    const nextIndex = index < 0 ? 0 : Math.min(s.items.length - 1, Math.max(0, index + step));
    const target = s.items[nextIndex];
    if (target && target.id !== s.selectedId) app.navigate(`#/inbox/${target.id}`);
  }

  function onKey(e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTyping(e) || document.querySelector("dialog[open]")) return;
    const key = e.key.toLowerCase();
    if (key === "j") { e.preventDefault(); move(1); }
    else if (key === "k") { e.preventDefault(); move(-1); }
    else if (key === "a" && s.detail?.status === "pending") { e.preventDefault(); approve(); }
    else if (key === "r" && s.detail && ["pending", "failed"].includes(s.detail.status)) { e.preventDefault(); openReject(); }
    else if (key === "/") { e.preventDefault(); search.focus(); }
  }
  document.addEventListener("keydown", onKey);

  const unsubscribe = app.onStats((stats, previous) => {
    updateTabs();
    const changed = !previous || JSON.stringify(stats.counts) !== JSON.stringify(previous.counts);
    if (changed) loadList({ silent: true });
  });

  app.setTitle("Approvals");
  loadList();
  loadDetail();

  return {
    el: split,
    update(r) {
      if (r.id === s.selectedId) return;
      s.selectedId = r.id;
      markSelection();
      loadDetail();
    },
    destroy() {
      document.removeEventListener("keydown", onKey);
      unsubscribe();
      clearTimeout(searchTimer);
    },
  };
}
