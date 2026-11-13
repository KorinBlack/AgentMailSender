// Settings: general, notification channels, operators, account. Each area is its own view.
import { api } from "../api.js";
import {
  clear, confirmDialog, errorBlock, field, fullTime, h, icon, openDialog, plural, relTime, skeleton,
  switchControl, toast, withBusy,
} from "../ui.js";
import { configPayload, renderConfigFields, splitConfig } from "./config-fields.js";

const TITLES = { general: "General", notifications: "Notifications", operators: "Operators", account: "Account" };

function section(title, description, body) {
  return h(
    "section",
    { class: "form-section" },
    h("div", { class: "aside" }, h("h3", {}, title), description ? h("p", {}, description) : null),
    h("div", { class: "body" }, body),
  );
}

function pageShell(title, subline, ...content) {
  return h(
    "div",
    { class: "page" },
    h("div", { class: "page-inner" }, h("div", { class: "page-head" }, title, subline ? h("p", { class: "subline" }, subline) : null), ...content),
  );
}

function loadingPage(title) {
  return pageShell(h("h1", {}, title), "Loading…", h("div", { class: "section" }, skeleton("40%", 14), skeleton("60%", 14), skeleton("50%", 14)));
}

export function createSettings(app, route) {
  const root = h("div", { class: "pane" });
  root.style.height = "100%";
  let current = null;

  function show(r) {
    current?.destroy?.();
    const factory = { general: generalView, notifications: r.id ? channelEditor : channelsView, operators: operatorsView, account: accountView }[r.section];
    current = factory(app, r);
    clear(root, current.el);
    app.setTitle(TITLES[r.section]);
  }
  show(route);

  return {
    el: root,
    update(r) { show(r); },
    canLeave: () => (current?.canLeave ? current.canLeave() : true),
    destroy() { current?.destroy?.(); },
  };
}

function saveBar(statusText, onSave, label = "Save changes", { startEnabled = false } = {}) {
  const status = h("span", { class: "status", role: "status" }, statusText);
  const button = h("button", { class: "btn primary", type: "button", disabled: !startEnabled }, label);
  button.addEventListener("click", () => withBusy(button, onSave));
  return { el: h("div", { class: "save-bar" }, status, button), status, button, alwaysEnabled: startEnabled };
}

function setDirty(bar, dirty, cleanText) {
  bar.status.classList.toggle("dirty", dirty);
  clear(bar.status, dirty ? [h("span", { class: "dot" }), "Unsaved changes"] : cleanText);
  // withBusy re-enables the button when a save finishes; apply the clean state after it.
  setTimeout(() => { bar.button.disabled = !dirty && !bar.alwaysEnabled; }, 0);
}

// =========================================================================== general
function generalView() {
  const el = h("div", { class: "pane" });
  el.style.height = "100%";
  let dirty = false;
  clear(el, loadingPage("General"));

  async function load() {
    let settings;
    try {
      settings = await api.get("api/admin/settings");
    } catch (err) {
      clear(el, pageShell(h("h1", {}, "General"), null, errorBlock("Couldn't load settings.", err.message, load)));
      return;
    }
    const model = { ...settings };
    const subline = h("p", { class: "subline" });
    const updateSubline = () => {
      const url = model.dashboard_url || settings.public_url_env;
      subline.textContent = url ? `Notifications link to ${url}` : "Notifications don't include a link yet";
    };
    updateSubline();

    const url = h("input", { class: "input", id: "dashboard-url", type: "url", value: model.dashboard_url, placeholder: "https://mail-review.example.com", autocomplete: "off", spellcheck: "false", "aria-describedby": "dashboard-url-help" });
    url.addEventListener("input", () => { model.dashboard_url = url.value.trim(); mark(); updateSubline(); });
    const useHere = h("button", { class: "btn", type: "button" }, "Use this address");
    useHere.addEventListener("click", () => {
      url.value = `${location.origin}${location.pathname.replace(/\/[^/]*$/, "")}`;
      model.dashboard_url = url.value;
      mark();
      updateSubline();
    });
    const envHelp = settings.public_url_env ? ` When empty, AMS_PUBLIC_URL (${settings.public_url_env}) is used.` : "";

    const maxMb = h("input", { class: "input num", type: "number", min: "1", max: "200", value: String(model.max_attachments_mb) });
    maxMb.addEventListener("input", () => { model.max_attachments_mb = Number(maxMb.value); mark(); });
    const maxRcpt = h("input", { class: "input num", type: "number", min: "1", max: "1000", value: String(model.max_recipients) });
    maxRcpt.addEventListener("input", () => { model.max_recipients = Number(maxRcpt.value); mark(); });

    const bar = saveBar("All changes saved", async () => {
      try {
        const saved = await api.put("api/admin/settings", {
          dashboard_url: model.dashboard_url || "",
          max_attachments_mb: model.max_attachments_mb,
          max_recipients: model.max_recipients,
        });
        Object.assign(settings, saved);
        dirty = false;
        setDirty(bar, false, "All changes saved");
        toast("Settings saved.");
      } catch (err) {
        toast(err.message, "error");
      }
    });
    function mark() {
      dirty = true;
      setDirty(bar, true, "");
    }

    clear(
      el,
      h(
        "div",
        { class: "page" },
        h(
          "div",
          { class: "page-inner" },
          h("div", { class: "page-head" }, h("h1", {}, "General"), subline),
          section("Dashboard address", "The address operators open. Notifications use it to link straight to a request.", [
            h(
              "div",
              { class: "field" },
              h("label", { for: "dashboard-url" }, "Public URL"),
              h("div", { class: "input-group" }, url, useHere),
              h("div", { class: "help", id: "dashboard-url-help" }, `Include the scheme, and the path if the dashboard runs under one.${envHelp}`),
            ),
          ]),
          section("Limits", "Checked when an agent submits. Larger requests are refused with an explanation the agent can act on.", [
            h(
              "div",
              { class: "form-grid" },
              field({ label: "Attachments per request (MB)", control: maxMb }),
              field({ label: "Recipients per request", control: maxRcpt, help: "To, Cc and Bcc together." }),
            ),
          ]),
        ),
      ),
      bar.el,
    );
  }
  load();
  return { el, canLeave: () => !dirty };
}

// =========================================================================== notifications list
function channelsView(app) {
  const el = h("div", { class: "pane" });
  el.style.height = "100%";
  clear(el, loadingPage("Notifications"));
  const meta = app.state.meta;
  const eventLabels = Object.fromEntries(meta.events.map((e) => [e.type, e.label]));
  const SHORT = { "request.created": "New request", "request.sent": "Sent", "request.failed": "Failed", "request.rejected": "Rejected", "request.cancelled": "Cancelled" };

  async function load() {
    let channels;
    let profiles;
    try {
      [channels, profiles] = await Promise.all([api.get("api/admin/channels"), api.get("api/admin/profiles")]);
    } catch (err) {
      clear(el, pageShell(h("h1", {}, "Notifications"), null, errorBlock("Couldn't load channels.", err.message, load)));
      return;
    }
    const failing = channels.filter((c) => c.enabled && c.last_status === "error").length;
    const sub = channels.length ? `${plural(channels.length, "channel")}${failing ? ` · ${failing} failing` : ""}` : "No channels yet";
    const profileName = Object.fromEntries(profiles.map((p) => [p.id, p.name]));
    const add = h("a", { class: "btn sm", href: "#/settings/notifications/new" }, icon("plus"), "Add channel");
    const listEl = channels.length
      ? h(
          "div",
          { class: "channel-list" },
          channels.map((c) => {
            const scope = c.profile_ids.length ? c.profile_ids.map((id) => profileName[id] || `#${id}`).join(", ") : "All profiles";
            let state;
            if (!c.enabled) state = h("span", { class: "pill" }, "Off");
            else if (c.last_status === "error") state = h("span", { class: "state-error", title: c.last_error || "" }, h("span", { class: "dot" }), ` Failed ${relTime(c.last_sent_at)}`);
            else if (c.last_status === "ok") state = h("span", {}, h("span", { class: "dot state-ok" }), ` Delivered ${relTime(c.last_sent_at)}`);
            else state = h("span", { class: "muted" }, "Not used yet");
            return h(
              "a",
              { class: "channel-item", href: `#/settings/notifications/${c.id}` },
              h("span", { class: "name" }, c.name),
              h("span", { class: "meta" }, `${c.type_label} · ${c.events.map((e) => SHORT[e] || eventLabels[e] || e).join(", ")} · ${scope}`),
              h("span", { class: "state" }, state),
            );
          }),
        )
      : h(
          "div",
          { class: "empty-box" },
          "No channels yet. Add Discord, Slack, Telegram, ntfy or any webhook to hear about new requests as they arrive. ",
          h("a", { href: "#/settings/notifications/new" }, "Add a channel"),
        );
    clear(el, pageShell(h("div", { class: "title-row" }, h("h1", {}, "Notifications"), add), sub, listEl));
  }
  load();
  return { el };
}

// =========================================================================== channel editor
function channelEditor(app, r) {
  const meta = app.state.meta;
  const el = h("div", { class: "pane" });
  el.style.height = "100%";
  const isNew = r.id === "new";
  let dirty = false;
  clear(el, loadingPage(isNew ? "New channel" : "Channel"));

  async function load() {
    let channel = null;
    let profiles;
    let settings;
    try {
      [channel, profiles, settings] = await Promise.all([
        isNew ? Promise.resolve(null) : api.get(`api/admin/channels/${r.id}`),
        api.get("api/admin/profiles"),
        api.get("api/admin/settings"),
      ]);
    } catch (err) {
      clear(el, pageShell(h("h1", {}, "Channel"), null, errorBlock(err.status === 404 ? "This channel does not exist." : "Couldn't load the channel.", err.message, err.status === 404 ? null : load)));
      return;
    }
    const first = meta.notifiers[0];
    const split = splitConfig(channel?.config);
    const m = {
      name: channel?.name || "",
      type: channel?.type || first.type,
      enabled: channel ? channel.enabled : true,
      values: split.values,
      secretsSet: split.secretsSet,
      secretInputs: {},
      events: channel ? [...channel.events] : ["request.created"],
      profileIds: channel ? [...channel.profile_ids] : [],
    };
    const dashboard = settings.dashboard_url || settings.public_url_env || "";

    const mark = () => { dirty = true; setDirty(bar, true, ""); renderPreview(); };

    const name = h("input", { class: "input", value: m.name, maxlength: "100", placeholder: "Team channel", autocomplete: "off" });
    name.addEventListener("input", () => { m.name = name.value; mark(); });
    const typeSelect = h("select", { class: "select" }, meta.notifiers.map((n) => h("option", { value: n.type }, n.label)));
    typeSelect.value = m.type;
    const connection = h("div", { class: "section" });
    const renderConnection = () => {
      const schema = meta.notifiers.find((n) => n.type === m.type);
      clear(
        connection,
        schema?.description ? h("p", { class: "muted" }, schema.description) : null,
        renderConfigFields(schema?.fields || [], m.values, m.secretsSet, m.secretInputs, (key, value, secret) => {
          if (secret) m.secretInputs[key] = value;
          else m.values[key] = value;
          mark();
        }),
      );
    };
    typeSelect.addEventListener("change", () => {
      m.type = typeSelect.value;
      m.values = {};
      m.secretInputs = {};
      m.secretsSet = [];
      renderConnection();
      mark();
    });
    renderConnection();

    const events = h(
      "div",
      { class: "section" },
      meta.events.map((ev) => {
        const box = h("input", { type: "checkbox", checked: m.events.includes(ev.type) });
        box.addEventListener("change", () => {
          m.events = box.checked ? [...new Set([...m.events, ev.type])] : m.events.filter((e) => e !== ev.type);
          mark();
        });
        return h("label", { class: "check" }, box, h("span", {}, ev.label, h("span", { class: "check-desc mono" }, ev.type)));
      }),
    );

    const scopeWrap = h("div", { class: "section" });
    const renderScope = () => {
      const all = h("input", { type: "radio", name: "scope", checked: !m.profileIds.length });
      const some = h("input", { type: "radio", name: "scope", checked: m.profileIds.length > 0, disabled: !profiles.length });
      all.addEventListener("change", () => { m.profileIds = []; mark(); renderScope(); });
      some.addEventListener("change", () => { m.profileIds = profiles.length ? [profiles[0].id] : []; mark(); renderScope(); });
      clear(
        scopeWrap,
        h("label", { class: "check" }, all, "All profiles, including ones added later"),
        h("label", { class: "check" }, some, "Only these profiles"),
        m.profileIds.length
          ? h(
              "div",
              { class: "nav-sub" },
              profiles.map((p) => {
                const box = h("input", { type: "checkbox", checked: m.profileIds.includes(p.id) });
                box.addEventListener("change", () => {
                  m.profileIds = box.checked ? [...m.profileIds, p.id] : m.profileIds.filter((id) => id !== p.id);
                  if (!m.profileIds.length) renderScope();
                  mark();
                });
                return h("label", { class: "check" }, box, p.name);
              }),
            )
          : null,
      );
    };
    renderScope();

    const preview = h("div", { class: "preview-card", "aria-label": "Notification preview" });
    function renderPreview() {
      const sampleProfile = (m.profileIds.length ? profiles.find((p) => p.id === m.profileIds[0]) : profiles[0])?.name || "support-bot";
      const link = dashboard ? `${dashboard.replace(/\/$/, "")}/#/inbox/…` : null;
      const typeLabel = meta.notifiers.find((n) => n.type === m.type)?.label || m.type;
      clear(
        preview,
        h("div", { class: "p-head" }, `${typeLabel} · when an agent submits`),
        h("div", { class: "p-title" }, "New mail request awaiting approval"),
        h("div", {}, "Your quote for order 1042"),
        h("div", { class: "muted small" }, `Profile ${sampleProfile} · to jane@example.com`),
        h("div", { class: "muted small" }, "Agent note: Jane asked for a quote in ticket #1042 this morning…"),
        link
          ? h("div", { class: "p-link" }, link)
          : h("div", { class: "notice" }, icon("alert"), h("span", {}, "No link: set the dashboard address in "), h("a", { href: "#/settings/general" }, "General"), "."),
      );
    }
    renderPreview();

    const testResult = h("span", { class: "muted small" }, isNew ? "Save the channel to send a test." : "Sends a sample notification with the saved settings.");
    const testBtn = h("button", { class: "btn", type: "button", disabled: isNew }, icon("bell"), "Send test");
    testBtn.addEventListener("click", () =>
      withBusy(testBtn, async () => {
        if (dirty) {
          clear(testResult, h("span", { class: "result-line error" }, icon("alert"), "Save your changes first: the test uses the saved settings."));
          return;
        }
        try {
          const res = await api.post(`api/admin/channels/${r.id}/test`);
          clear(testResult, h("span", { class: `result-line ${res.ok ? "ok" : "error"}`, role: "status" }, icon(res.ok ? "check" : "alert"), res.message));
        } catch (err) {
          clear(testResult, h("span", { class: "result-line error" }, icon("alert"), err.message));
        }
      }),
    );

    const bar = saveBar(isNew ? "New channel, not saved yet" : "All changes saved", async () => {
      const schema = meta.notifiers.find((n) => n.type === m.type);
      if (!m.name.trim()) {
        toast("Give the channel a name.", "error");
        name.focus();
        return;
      }
      const payload = {
        name: m.name.trim(),
        type: m.type,
        enabled: m.enabled,
        config: configPayload(schema?.fields || [], m.values, m.secretInputs),
        events: m.events,
        profile_ids: m.profileIds,
      };
      try {
        const saved = isNew ? await api.post("api/admin/channels", payload) : await api.put(`api/admin/channels/${r.id}`, payload);
        dirty = false;
        toast(isNew ? "Channel added. Send a test to check it." : "Channel saved.");
        app.navigate(`#/settings/notifications/${saved.id}`, { replace: true });
      } catch (err) {
        toast(err.message, "error");
      }
    }, isNew ? "Add channel" : "Save changes", { startEnabled: isNew });

    const content = [
      section("Channel", null, [
        field({ label: "Name", control: name }),
        field({ label: "Service", control: typeSelect }),
        switchControl({ label: "Send notifications", checked: m.enabled, onChange: (v) => { m.enabled = v; mark(); } }),
      ]),
      section("Connection", "Credentials and URLs are stored encrypted and never shown again.", [connection]),
      section("Events", "Choose what this channel hears about. An agent's own webhook usually wants the decisions.", [events]),
      section("Profiles", "Limit the channel to some agents, for example to give each agent a callback URL of its own.", [scopeWrap]),
      section("Preview", "Roughly what arrives. Each service lays it out in its own style.", [preview, h("div", { class: "title-row" }, testBtn, testResult)]),
    ];
    if (!isNew) {
      const del = h("button", { class: "btn danger", type: "button" }, icon("trash"), "Delete channel");
      del.addEventListener("click", async () => {
        const ok = await confirmDialog({ title: `Delete “${channel.name}”?`, message: "Notifications stop going to this channel.", confirmLabel: "Delete channel", danger: true });
        if (!ok) return;
        await withBusy(del, async () => {
          try {
            await api.del(`api/admin/channels/${r.id}`);
            dirty = false;
            toast("Channel deleted.");
            app.navigate("#/settings/notifications");
          } catch (err) {
            toast(err.message, "error");
          }
        });
      });
      const zone = section("Delete channel", "Stops all notifications to this destination.", [h("div", {}, del)]);
      zone.classList.add("danger-zone");
      content.push(zone);
    }
    const lastLine = channel?.last_status === "error"
      ? `Last delivery failed ${relTime(channel.last_sent_at)}: ${channel.last_error}`
      : channel?.last_status === "ok"
        ? `Last delivered ${relTime(channel.last_sent_at)}`
        : isNew ? "Notify operators or agents when requests change" : "Not used yet";
    clear(
      el,
      pageShell(
        h("div", { class: "title-row" }, h("a", { class: "btn ghost sm icon-only", href: "#/settings/notifications", "aria-label": "Back to notifications" }, icon("back")), h("h1", {}, isNew ? "New channel" : channel.name)),
        lastLine,
        ...content,
      ),
      bar.el,
    );
    if (isNew) queueMicrotask(() => name.focus());
  }
  load();
  return { el, canLeave: () => !dirty };
}

// =========================================================================== operators
function operatorsView(app) {
  const el = h("div", { class: "pane" });
  el.style.height = "100%";
  clear(el, loadingPage("Operators"));

  async function load() {
    let operators;
    try {
      operators = await api.get("api/admin/operators");
    } catch (err) {
      clear(el, pageShell(h("h1", {}, "Operators"), null, errorBlock("Couldn't load operators.", err.message, load)));
      return;
    }
    const me = app.state.me;
    const table = h(
      "table",
      { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Username"), h("th", {}, "Last sign-in"), h("th", { class: "actions" }, h("span", { class: "sr-only" }, "Actions")))),
      h(
        "tbody",
        {},
        operators.map((op) => {
          const isMe = op.id === me.id;
          const reset = h("button", { class: "text-btn", type: "button" }, "Set password");
          reset.addEventListener("click", () => resetPassword(op));
          const del = isMe ? null : h("button", { class: "text-btn danger", type: "button" }, "Remove");
          del?.addEventListener("click", () => removeOperator(op));
          return h(
            "tr",
            {},
            h("td", {}, h("span", { class: "name" }, op.username), isMe ? h("span", { class: "muted" }, " (you)") : null, h("div", { class: "muted small" }, `Added ${fullTime(op.created_at)}`)),
            h("td", { class: "muted", title: op.last_login_at ? fullTime(op.last_login_at) : "" }, op.last_login_at ? relTime(op.last_login_at) : "Never"),
            h("td", { class: "actions" }, isMe ? null : reset, del ? " · " : null, del),
          );
        }),
      ),
    );

    const username = h("input", { class: "input", autocomplete: "off", autocapitalize: "none", spellcheck: "false", placeholder: "alice" });
    const password = h("input", { class: "input", type: "password", autocomplete: "new-password", minlength: "10" });
    const status = h("div", { class: "error-text", role: "status" });
    const add = h("button", { class: "btn", type: "submit" }, icon("plus"), "Add operator");
    const form = h(
      "form",
      { class: "section", novalidate: true },
      h("div", { class: "form-grid" }, field({ label: "Username", control: username }), field({ label: "Password", control: password, help: "At least 10 characters. Share it privately; they can change it under Account." })),
      h("div", {}, add),
      status,
    );
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      status.textContent = "";
      if (!/^[A-Za-z0-9_.@-]{2,64}$/.test(username.value.trim())) {
        status.textContent = "Usernames use 2–64 letters, digits, dots, dashes, underscores or @.";
        username.setAttribute("aria-invalid", "true");
        username.focus();
        return;
      }
      if (password.value.length < 10) {
        status.textContent = "The password needs at least 10 characters.";
        password.setAttribute("aria-invalid", "true");
        password.focus();
        return;
      }
      withBusy(add, async () => {
        try {
          await api.post("api/admin/operators", { username: username.value.trim(), password: password.value });
          toast(`Operator ${username.value.trim()} added.`);
          load();
        } catch (err) {
          status.textContent = err.message;
        }
      });
    });

    clear(
      el,
      pageShell(
        h("h1", {}, "Operators"),
        `${plural(operators.length, "person", "people")} can approve requests`,
        section("Team", "Everyone here can read, approve and reject every agent's mail, and change settings.", [table]),
        section("Add operator", null, [form]),
      ),
    );
  }

  async function resetPassword(op) {
    const input = h("input", { class: "input", type: "password", autocomplete: "new-password", minlength: "10" });
    const err = h("div", { class: "error-text", role: "status" });
    await openDialog({
      title: `New password for ${op.username}`,
      body: h("div", { class: "section" }, field({ label: "Password", control: input, help: "At least 10 characters. Their open sessions are signed out." }), err),
      actions: [{ label: "Cancel", value: null }, { label: "Set password", value: true, kind: "primary" }],
      onAction: async () => {
        if (input.value.length < 10) {
          err.textContent = "The password needs at least 10 characters.";
          input.focus();
          return false;
        }
        try {
          await api.post(`api/admin/operators/${op.id}/password`, { password: input.value });
          toast("Password updated.");
          return true;
        } catch (e) {
          err.textContent = e.message;
          return false;
        }
      },
    });
  }

  async function removeOperator(op) {
    const ok = await confirmDialog({ title: `Remove ${op.username}?`, message: "They are signed out and can no longer review requests.", confirmLabel: "Remove operator", danger: true });
    if (!ok) return;
    try {
      await api.del(`api/admin/operators/${op.id}`);
      toast(`${op.username} removed.`);
      load();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  load();
  return { el };
}

// =========================================================================== account
function accountView(app) {
  const el = h("div", { class: "pane" });
  el.style.height = "100%";
  const current = h("input", { class: "input", type: "password", autocomplete: "current-password" });
  const next = h("input", { class: "input", type: "password", autocomplete: "new-password", minlength: "10" });
  const repeat = h("input", { class: "input", type: "password", autocomplete: "new-password" });
  const status = h("div", { class: "error-text", role: "status" });
  const submit = h("button", { class: "btn primary", type: "submit" }, "Change password");
  const form = h(
    "form",
    { class: "section", novalidate: true },
    field({ label: "Current password", control: current }),
    h("div", { class: "form-grid" }, field({ label: "New password", control: next, help: "At least 10 characters." }), field({ label: "Repeat new password", control: repeat })),
    h("div", {}, submit),
    status,
  );
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    status.textContent = "";
    for (const input of [current, next, repeat]) input.removeAttribute("aria-invalid");
    const fail = (input, message) => { status.textContent = message; input.setAttribute("aria-invalid", "true"); input.focus(); };
    if (!current.value) return fail(current, "Enter your current password.");
    if (next.value.length < 10) return fail(next, "The new password needs at least 10 characters.");
    if (next.value !== repeat.value) return fail(repeat, "The two new passwords don't match.");
    withBusy(submit, async () => {
      try {
        await api.post("api/auth/password", { current_password: current.value, new_password: next.value });
        form.reset();
        toast("Password changed. Other sessions were signed out.");
      } catch (err) {
        fail(err.data?.field === "current_password" ? current : next, err.message);
      }
    });
  });
  clear(
    el,
    pageShell(
      h("h1", {}, "Account"),
      `Signed in as ${app.state.me.username}`,
      section("Password", "Changing it signs you out everywhere else.", [form]),
    ),
  );
  return { el };
}
