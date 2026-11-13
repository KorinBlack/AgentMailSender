// Profiles: one per agent. Sender identities, signatures, delivery transport and API keys.
import { api } from "../api.js";
import {
  clear, confirmDialog, copyText, errorBlock, field, fullTime, h, icon, nextId, openDialog, plural, relTime,
  skeleton, switchControl, toast, withBusy,
} from "../ui.js";
import { configPayload, renderConfigFields, splitConfig } from "./config-fields.js";

function blankModel(meta) {
  return {
    id: null,
    name: "",
    description: "",
    enabled: true,
    identities: [{ email: "", name: "" }],
    signatures: [],
    default_signature: null,
    reply_to: "",
    transport_type: meta.transports[0]?.type || "smtp",
    transport_values: {},
    secrets_set: [],
    secret_inputs: {},
    api_keys: [],
    request_counts: {},
  };
}

function modelFrom(profile) {
  const { values, secretsSet } = splitConfig(profile.transport_config);
  return {
    id: profile.id,
    name: profile.name,
    description: profile.description || "",
    enabled: profile.enabled,
    identities: profile.identities.length ? profile.identities.map((i) => ({ ...i })) : [{ email: "", name: "" }],
    signatures: profile.signatures.map((sig) => ({ ...sig })),
    default_signature: profile.default_signature,
    reply_to: profile.default_reply_to.join(", "),
    transport_type: profile.transport_type,
    transport_values: values,
    secrets_set: secretsSet,
    secret_inputs: {},
    api_keys: profile.api_keys,
    request_counts: profile.request_counts || {},
  };
}

export function createProfiles(app, route) {
  const meta = app.state.meta;
  const s = { profiles: [], loading: true, error: null, id: route.id, model: null, dirty: false, testResult: null };

  const subline = h("p", { class: "subline" });
  const list = h("div", { class: "list", role: "list" });
  const listPane = h(
    "section",
    { class: "pane", "aria-label": "Profiles" },
    h(
      "div",
      { class: "pane-head" },
      h("div", { class: "title-row" }, h("h1", {}, "Profiles"), h("a", { class: "btn sm", href: "#/profiles/new" }, icon("plus"), "New profile")),
      subline,
    ),
    h("div", { class: "pane-scroll" }, list),
  );
  const editor = h("section", { class: "pane", "aria-label": "Profile editor" });
  const split = h("div", { class: "split narrow-list" }, listPane, editor);

  // ------------------------------------------------------------------ data
  async function loadProfiles() {
    try {
      s.profiles = await api.get("api/admin/profiles");
      s.error = null;
    } catch (err) {
      s.error = err;
    }
    s.loading = false;
    renderList();
  }

  function selectFromRoute() {
    split.classList.toggle("show-detail", Boolean(s.id));
    s.testResult = null;
    if (s.id === "new") {
      s.model = blankModel(meta);
    } else if (s.id) {
      const profile = s.profiles.find((p) => String(p.id) === s.id);
      s.model = profile ? modelFrom(profile) : null;
    } else if (s.profiles.length && window.matchMedia("(min-width: 901px)").matches) {
      app.navigate(`#/profiles/${s.profiles[0].id}`, { replace: true });
      return;
    } else {
      s.model = null;
    }
    s.dirty = false;
    renderList();
    renderEditor();
  }

  // ------------------------------------------------------------------ list
  function renderList() {
    if (s.loading) {
      subline.textContent = "Loading…";
      clear(list, [1, 2, 3].map(() => h("div", { class: "row-skeleton" }, skeleton("50%", 14), skeleton("70%", 12))));
      return;
    }
    if (s.error) {
      subline.textContent = "";
      clear(list, h("div", { class: "inline-error" }, icon("alert"), "Couldn't load profiles. ", h("button", { class: "text-btn", type: "button", onClick: loadProfiles }, "Try again")));
      return;
    }
    const disabled = s.profiles.filter((p) => !p.enabled).length;
    subline.textContent = s.profiles.length ? `${plural(s.profiles.length, "profile")}${disabled ? ` · ${disabled} disabled` : ""}` : "No profiles yet";
    if (!s.profiles.length) {
      clear(list, h("p", { class: "empty-line" }, "Create one profile per agent. Each gets its own sender addresses, mail account and API key."));
      return;
    }
    clear(
      list,
      s.profiles.map((p) => {
        const active = p.api_keys.filter((k) => !k.revoked_at).length;
        const selected = String(p.id) === s.id;
        return h(
          "a",
          { class: `entity-row${selected ? " selected" : ""}`, href: `#/profiles/${p.id}`, role: "listitem", "aria-current": selected ? "true" : null },
          h("span", { class: "name" }, h("span", {}, p.name), p.enabled ? null : h("span", { class: "pill" }, "Disabled")),
          h("span", { class: "meta" }, `${p.identities[0]?.email || "No sender"} · ${p.transport_label} · ${plural(active, "key")}`),
        );
      }),
    );
  }

  // ------------------------------------------------------------------ editor
  function markDirty() {
    if (!s.dirty) {
      s.dirty = true;
      updateSaveBar();
    }
  }

  let saveStatus = null;
  let saveButton = null;
  function updateSaveBar() {
    if (!saveStatus) return;
    saveStatus.classList.toggle("dirty", s.dirty);
    clear(saveStatus, s.dirty ? [h("span", { class: "dot" }), "Unsaved changes"] : s.model?.id ? "All changes saved" : "New profile, not saved yet");
    if (saveButton) saveButton.disabled = !s.dirty && Boolean(s.model?.id);
  }

  function renderEditor() {
    saveStatus = null;
    saveButton = null;
    if (s.loading) {
      clear(editor, h("div", { class: "page" }, h("div", { class: "page-inner" }, skeleton("30%", 24))));
      return;
    }
    if (s.error) {
      clear(editor, h("div", { class: "page" }, h("div", { class: "page-inner" }, errorBlock("Couldn't load profiles.", s.error.message, loadProfiles))));
      return;
    }
    const m = s.model;
    if (!m) {
      if (s.id) {
        clear(editor, h("div", { class: "page" }, h("div", { class: "page-inner" }, errorBlock("This profile does not exist.", "It may have been deleted.", null))));
        return;
      }
      clear(
        editor,
        h(
          "div",
          { class: "page" },
          h(
            "div",
            { class: "page-inner" },
            h("div", { class: "page-head" }, h("h2", {}, "Set up your first agent")),
            h("p", { class: "muted" }, "A profile holds the mail account an agent sends through, the addresses it may use and the API key it signs requests with. The agent never sees the account's password."),
            h("p", {}, h("a", { class: "btn primary", href: "#/profiles/new" }, icon("plus"), "Create a profile")),
          ),
        ),
      );
      return;
    }

    const page = h("div", { class: "page" });
    const inner = h("form", { class: "page-inner", novalidate: true });
    inner.addEventListener("submit", (e) => { e.preventDefault(); save(); });
    page.append(inner);

    const counts = m.request_counts;
    const usage = ["sent", "pending", "rejected", "failed"].filter((k) => counts[k]).map((k) => `${counts[k]} ${k}`).join(" · ");
    inner.append(
      h(
        "div",
        { class: "page-head" },
        h("div", { class: "title-row" }, h("a", { class: "btn ghost sm icon-only back-btn", href: "#/profiles", "aria-label": "Back to profiles" }, icon("back")), h("h2", {}, m.id ? m.name || "Untitled profile" : "New profile")),
        h("p", { class: "subline" }, m.id ? usage || "No requests yet" : "Fill in the sender and delivery settings, then save to create API keys."),
      ),
    );

    // Details
    const name = h("input", { class: "input", value: m.name, maxlength: "64", required: true, autocomplete: "off", placeholder: "support-bot" });
    name.addEventListener("input", () => { m.name = name.value; markDirty(); });
    const description = h("textarea", { class: "textarea", rows: "2", maxlength: "2000", placeholder: "What this agent does" });
    description.value = m.description;
    description.addEventListener("input", () => { m.description = description.value; markDirty(); });
    inner.append(
      section("Profile", "Name the agent this profile belongs to. The name appears in the review queue and in notifications.", [
        field({ label: "Name", control: name }),
        field({ label: "Description", control: description }),
        switchControl({ label: "Accept requests", description: "When off, the agent's API keys are refused.", checked: m.enabled, onChange: (v) => { m.enabled = v; markDirty(); } }),
      ]),
    );

    // Identities
    const idRows = h("div", { class: "section" });
    const renderIdentities = () => {
      clear(
        idRows,
        m.identities.map((identity, index) => {
          const email = h("input", { class: "input", type: "email", value: identity.email, placeholder: "agent@yourdomain.com", "aria-label": `Sender address ${index + 1}`, autocomplete: "off" });
          email.addEventListener("input", () => { identity.email = email.value; markDirty(); });
          const display = h("input", { class: "input", value: identity.name, placeholder: "Display name", "aria-label": `Display name ${index + 1}`, autocomplete: "off" });
          display.addEventListener("input", () => { identity.name = display.value; markDirty(); });
          const remove = h("button", { class: "btn ghost icon-only", type: "button", "aria-label": `Remove ${identity.email || "address"}`, disabled: m.identities.length === 1 }, icon("trash"));
          remove.addEventListener("click", () => { m.identities.splice(index, 1); markDirty(); renderIdentities(); });
          return h("div", { class: "repeat-row" }, email, display, remove);
        }),
        h("div", {}, h("button", { class: "text-btn", type: "button", onClick: () => { m.identities.push({ email: "", name: "" }); markDirty(); renderIdentities(); } }, icon("plus", "sm"), "Add address")),
      );
    };
    renderIdentities();
    const replyTo = h("input", { class: "input", value: m.reply_to, placeholder: "Support <support@yourdomain.com>", autocomplete: "off" });
    replyTo.addEventListener("input", () => { m.reply_to = replyTo.value; markDirty(); });
    inner.append(
      section("Sender addresses", "Addresses the agent may send from. The first one is used when the agent doesn't pick one.", [
        idRows,
        field({ label: "Default Reply-To", control: replyTo, help: "Optional. Used when the agent sets no Reply-To. Separate several addresses with commas." }),
      ]),
    );

    // Signatures
    const sigWrap = h("div", { class: "section" });
    const radioName = nextId("sig-default");
    const renderSignatures = () => {
      const cards = m.signatures.map((sig, index) => {
        const key = h("input", { class: "input mono", value: sig.key, placeholder: "default", pattern: "[a-z0-9][a-z0-9_-]{0,31}", "aria-label": "Signature key", autocomplete: "off" });
        key.addEventListener("input", () => {
          const old = sig.key;
          sig.key = key.value.trim().toLowerCase();
          if (m.default_signature === old) m.default_signature = sig.key;
          markDirty();
        });
        const label = h("input", { class: "input", value: sig.label || "", placeholder: "Label (optional)", "aria-label": "Signature label", autocomplete: "off" });
        label.addEventListener("input", () => { sig.label = label.value; markDirty(); });
        const text = h("textarea", { class: "textarea", rows: "3", placeholder: "Plain-text version" });
        text.value = sig.text || "";
        text.addEventListener("input", () => { sig.text = text.value; markDirty(); });
        const markup = h("textarea", { class: "textarea code", rows: "3", placeholder: "<p>HTML version (optional)</p>", spellcheck: "false" });
        markup.value = sig.html || "";
        markup.addEventListener("input", () => { sig.html = markup.value; markDirty(); });
        const radio = h("input", { type: "radio", name: radioName, checked: m.default_signature === sig.key && Boolean(sig.key) });
        radio.addEventListener("change", () => { m.default_signature = sig.key; markDirty(); });
        const remove = h("button", { class: "btn ghost sm icon-only", type: "button", "aria-label": "Remove signature" }, icon("trash"));
        remove.addEventListener("click", () => {
          if (m.default_signature === sig.key) m.default_signature = null;
          m.signatures.splice(index, 1);
          markDirty();
          renderSignatures();
        });
        return h(
          "div",
          { class: "card" },
          h("div", { class: "card-head" }, h("div", { class: "grow form-grid" }, key, label), h("label", { class: "check" }, radio, "Default"), remove),
          field({ label: "Plain text", control: text }),
          field({ label: "HTML", control: markup, help: "When empty, the HTML version is made from the plain text." }),
        );
      });
      const noDefault = h("input", { type: "radio", name: radioName, checked: !m.default_signature });
      noDefault.addEventListener("change", () => { m.default_signature = null; markDirty(); });
      clear(
        sigWrap,
        cards.length ? cards : h("div", { class: "empty-box" }, "No signatures. Messages are sent exactly as the agent wrote them."),
        h(
          "div",
          { class: "title-row" },
          h("button", { class: "text-btn", type: "button", onClick: () => { m.signatures.push({ key: m.signatures.length ? "" : "default", label: "", text: "", html: "" }); markDirty(); renderSignatures(); } }, icon("plus", "sm"), "Add signature"),
          cards.length ? h("label", { class: "check" }, noDefault, "No default signature") : null,
        ),
      );
    };
    renderSignatures();
    inner.append(
      section("Signatures", "Added to the message when the agent submits it, so you review exactly what is sent. The agent picks one by key, or gets the default.", [sigWrap]),
    );

    // Transport
    const transportBody = h("div", { class: "section" });
    const typeSelect = h("select", { class: "select" }, meta.transports.map((t) => h("option", { value: t.type }, t.label)));
    typeSelect.value = m.transport_type;
    typeSelect.addEventListener("change", () => {
      m.transport_type = typeSelect.value;
      m.transport_values = {};
      m.secret_inputs = {};
      m.secrets_set = [];
      s.testResult = null;
      markDirty();
      renderTransport();
    });
    const renderTransport = () => {
      const schema = meta.transports.find((t) => t.type === m.transport_type);
      const testBtn = h("button", { class: "btn", type: "button", disabled: !m.id }, "Test connection");
      testBtn.addEventListener("click", () =>
        withBusy(testBtn, async () => {
          if (s.dirty) {
            s.testResult = { ok: false, message: "Save your changes first: the test uses the saved settings." };
          } else {
            try {
              s.testResult = await api.post(`api/admin/profiles/${m.id}/test`);
            } catch (err) {
              s.testResult = { ok: false, message: err.message };
            }
          }
          renderTransport();
        }),
      );
      clear(
        transportBody,
        field({ label: "Method", control: typeSelect, help: schema?.description }),
        schema
          ? renderConfigFields(schema.fields, m.transport_values, m.secrets_set, m.secret_inputs, (name, value, secret) => {
              if (secret) m.secret_inputs[name] = value;
              else m.transport_values[name] = value;
              s.testResult = null;
              markDirty();
            })
          : null,
        h(
          "div",
          { class: "title-row" },
          testBtn,
          s.testResult
            ? h("span", { class: `result-line ${s.testResult.ok ? "ok" : "error"}`, role: "status" }, icon(s.testResult.ok ? "check" : "alert"), s.testResult.message)
            : h("span", { class: "muted small" }, m.id ? "Checks the saved settings without sending anything." : "Save the profile to test it."),
        ),
      );
    };
    renderTransport();
    inner.append(section("Delivery", "How approved messages leave. These credentials stay on the server; the agent never sees them.", [transportBody]));

    // API keys
    if (m.id) inner.append(section("API keys", "The agent signs every request with one of these keys. A new key is shown once.", [keysBody(m)]));

    // Danger zone
    if (m.id) {
      const del = h("button", { class: "btn danger", type: "button" }, icon("trash"), "Delete profile");
      del.addEventListener("click", () => removeProfile(del));
      const pending = m.request_counts.pending || 0;
      const zone = section(
        "Delete profile",
        `Its API keys stop working${pending ? ` and its ${plural(pending, "pending request")} are cancelled` : ""}. Sent history is kept.`,
        [h("div", {}, del)],
      );
      zone.classList.add("danger-zone");
      inner.append(zone);
    }

    // Save bar
    saveStatus = h("span", { class: "status", role: "status" });
    saveButton = h("button", { class: "btn primary", type: "button" }, m.id ? "Save changes" : "Create profile");
    saveButton.addEventListener("click", () => save());
    const discard = h("button", { class: "btn", type: "button" }, m.id ? "Discard" : "Cancel");
    discard.addEventListener("click", () => {
      if (!m.id) {
        s.dirty = false;
        app.navigate("#/profiles");
        return;
      }
      selectFromRoute();
    });
    clear(editor, page, h("div", { class: "save-bar" }, saveStatus, discard, saveButton));
    updateSaveBar();
    if (!m.id) queueMicrotask(() => name.focus());
  }

  function section(title, description, body) {
    return h(
      "section",
      { class: "form-section" },
      h("div", { class: "aside" }, h("h3", {}, title), h("p", {}, description)),
      h("div", { class: "body" }, body),
    );
  }

  function keysBody(m) {
    const wrap = h("div", { class: "section" });
    const label = h("input", { class: "input", placeholder: "Where the key is used, e.g. production server", maxlength: "100", "aria-label": "Key label", autocomplete: "off" });
    const create = h("button", { class: "btn", type: "button" }, icon("key"), "Create key");
    create.addEventListener("click", () =>
      withBusy(create, async () => {
        try {
          const res = await api.post(`api/admin/profiles/${m.id}/keys`, { label: label.value.trim() });
          label.value = "";
          m.api_keys = [...m.api_keys, res.api_key];
          const profile = s.profiles.find((p) => p.id === m.id);
          if (profile) profile.api_keys = m.api_keys;
          renderKeys();
          renderList();
          showNewKey(res.key);
        } catch (err) {
          toast(err.message, "error");
        }
      }),
    );
    label.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); create.click(); } });
    const tableWrap = h("div");
    const renderKeys = () => {
      if (!m.api_keys.length) {
        clear(tableWrap, h("div", { class: "empty-box" }, "No keys yet. Create one and give it to the agent."));
        return;
      }
      clear(
        tableWrap,
        h(
          "table",
          { class: "table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Label"), h("th", {}, "Key"), h("th", {}, "Last used"), h("th", { class: "actions" }, h("span", { class: "sr-only" }, "Actions")))),
          h(
            "tbody",
            {},
            m.api_keys.map((k) => {
              const revoke = k.revoked_at ? h("span", { class: "pill" }, "Revoked") : h("button", { class: "text-btn danger", type: "button" }, "Revoke");
              if (!k.revoked_at) revoke.addEventListener("click", () => revokeKey(m, k, renderKeys));
              return h(
                "tr",
                {},
                h("td", {}, k.label || h("span", { class: "muted" }, "Unlabelled"), h("div", { class: "muted small" }, `Created ${fullTime(k.created_at)}`)),
                h("td", {}, h("span", { class: "mono" }, k.hint)),
                h("td", { class: "muted", title: k.last_used_at ? fullTime(k.last_used_at) : "" }, k.last_used_at ? relTime(k.last_used_at) : "Never"),
                h("td", { class: "actions" }, revoke),
              );
            }),
          ),
        ),
      );
    };
    renderKeys();
    wrap.append(tableWrap, h("div", { class: "input-group" }, label, create));
    return wrap;
  }

  function showNewKey(key) {
    const base = `${location.origin}${location.pathname.replace(/\/[^/]*$/, "")}`;
    const env = `AGENT_MAIL_URL=${base}\nAGENT_MAIL_API_KEY=${key}`;
    const copyKey = h("button", { class: "btn", type: "button" }, icon("copy"), "Copy key");
    copyKey.addEventListener("click", () => copyText(key, "API key copied"));
    const copyEnv = h("button", { class: "btn ghost", type: "button" }, "Copy as environment variables");
    copyEnv.addEventListener("click", () => copyText(env, "Environment variables copied"));
    openDialog({
      title: "Copy the new API key",
      body: h(
        "div",
        { class: "key-reveal" },
        h("p", {}, "This is the only time the key is shown. Only a hash is stored, so a lost key has to be replaced."),
        h("div", { class: "key-value" }, key),
        h("div", { class: "title-row" }, copyKey, copyEnv),
      ),
      actions: [{ label: "Done", value: true, kind: "primary" }],
    });
  }

  async function revokeKey(m, key, rerender) {
    const ok = await confirmDialog({
      title: `Revoke “${key.label || key.hint}”?`,
      message: "Requests signed with this key are refused from now on. This can't be undone.",
      confirmLabel: "Revoke key",
      danger: true,
    });
    if (!ok) return;
    try {
      const updated = await api.del(`api/admin/profiles/${m.id}/keys/${key.id}`);
      m.api_keys = m.api_keys.map((k) => (k.id === key.id ? updated : k));
      const profile = s.profiles.find((p) => p.id === m.id);
      if (profile) profile.api_keys = m.api_keys;
      rerender();
      renderList();
      toast("Key revoked.");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function removeProfile(button) {
    const m = s.model;
    const ok = await confirmDialog({
      title: `Delete “${m.name}”?`,
      message: "The agent loses access at once and its pending requests are cancelled. Sent history stays in Approvals.",
      confirmLabel: "Delete profile",
      danger: true,
    });
    if (!ok) return;
    await withBusy(button, async () => {
      try {
        await api.del(`api/admin/profiles/${m.id}`);
        s.dirty = false;
        s.profiles = s.profiles.filter((p) => p.id !== m.id);
        toast(`Profile “${m.name}” deleted.`);
        app.refreshStats();
        app.navigate("#/profiles");
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  async function save() {
    const m = s.model;
    if (!m || !saveButton) return;
    const schema = meta.transports.find((t) => t.type === m.transport_type);
    const payload = {
      name: m.name.trim(),
      description: m.description,
      enabled: m.enabled,
      identities: m.identities.filter((i) => i.email.trim()).map((i) => ({ email: i.email.trim(), name: i.name.trim() })),
      signatures: m.signatures.map((sig) => ({ key: sig.key, label: sig.label || "", text: sig.text || "", html: sig.html || "" })),
      default_signature: m.default_signature || null,
      default_reply_to: m.reply_to.split(",").map((v) => v.trim()).filter(Boolean),
      transport_type: m.transport_type,
      transport_config: configPayload(schema?.fields || [], m.transport_values, m.secret_inputs),
    };
    if (!payload.name) {
      toast("Give the profile a name.", "error");
      return;
    }
    await withBusy(saveButton, async () => {
      try {
        const saved = m.id ? await api.put(`api/admin/profiles/${m.id}`, payload) : await api.post("api/admin/profiles", payload);
        const index = s.profiles.findIndex((p) => p.id === saved.id);
        if (index >= 0) s.profiles[index] = saved;
        else s.profiles = [...s.profiles, saved].sort((a, b) => a.name.localeCompare(b.name));
        s.dirty = false;
        toast(m.id ? "Profile saved." : "Profile created. Now create an API key for the agent.");
        if (!m.id) {
          app.navigate(`#/profiles/${saved.id}`, { replace: true });
        } else {
          s.model = modelFrom(saved);
          renderList();
          renderEditor();
        }
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  app.setTitle("Profiles");
  renderList();
  renderEditor();
  loadProfiles().then(selectFromRoute);

  return {
    el: split,
    update(r) {
      s.id = r.id;
      if (!s.loading) selectFromRoute();
    },
    canLeave: () => !s.dirty,
    destroy() {},
  };
}
