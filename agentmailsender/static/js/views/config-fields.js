// Renders transport / notifier settings from the field schema the server publishes in /api/admin/meta.
import { h, nextId, switchControl } from "../ui.js";

/**
 * @param {Array} fields      schema fields ({name, label, type, required, default, secret, help, placeholder, options})
 * @param {object} values     non-secret values
 * @param {string[]} secretsSet names of secret fields that already have a stored value
 * @param {object} secretInputs values typed for secret fields in this session
 * @param {(name, value, secret) => void} onChange
 */
export function renderConfigFields(fields, values, secretsSet, secretInputs, onChange) {
  const grid = h("div", { class: "form-grid" });
  for (const f of fields) {
    if (f.type === "bool") {
      const checked = values[f.name] ?? f.default ?? false;
      grid.append(
        h("div", { class: "field span-2" }, switchControl({ label: f.label, checked, description: f.help, onChange: (v) => onChange(f.name, v, false) })),
      );
      continue;
    }
    const id = nextId("cfg");
    const wide = f.type === "textarea" || f.type === "json";
    let control;
    if (f.type === "select") {
      control = h("select", { class: "select", id }, (f.options || []).map((o) => h("option", { value: o.value }, o.label)));
      control.value = values[f.name] ?? f.default ?? f.options?.[0]?.value ?? "";
      control.addEventListener("change", () => onChange(f.name, control.value, false));
    } else {
      if (wide) {
        control = h("textarea", { class: `textarea${f.type === "json" ? " code" : ""}`, id, rows: f.type === "json" ? "3" : "5", spellcheck: "false" });
      } else {
        const type = f.secret ? "password" : { number: "number", url: "url", email: "email" }[f.type] || "text";
        control = h("input", { class: "input", id, type, autocomplete: f.secret ? "new-password" : "off", spellcheck: "false" });
        if (f.type === "number") {
          if (f.min != null) control.min = f.min;
          if (f.max != null) control.max = f.max;
        }
      }
      if (f.secret) {
        control.value = secretInputs[f.name] || "";
        control.placeholder = secretsSet.includes(f.name) ? "Saved. Type a new value to replace it." : f.placeholder || "";
      } else {
        let value = values[f.name];
        if (value == null) value = f.default ?? "";
        if (typeof value === "object") value = JSON.stringify(value, null, 2);
        control.value = String(value);
        control.placeholder = f.placeholder || (f.default != null && f.type !== "number" ? String(f.default) : "");
      }
      control.addEventListener("input", () => onChange(f.name, control.value, Boolean(f.secret)));
    }
    if (f.required) control.required = true;
    const label = f.required ? f.label : [f.label, h("span", { class: "muted" }, " (optional)")];
    grid.append(
      h(
        "div",
        { class: `field${wide ? " span-2" : ""}` },
        h("label", { for: id }, label),
        control,
        f.help ? h("div", { class: "help" }, f.help) : null,
      ),
    );
  }
  return grid;
}

/** Build the payload for a config object: non-secret values plus any secrets typed in this session. */
export function configPayload(fields, values, secretInputs) {
  const out = {};
  for (const f of fields) {
    if (f.secret) {
      if (secretInputs[f.name]) out[f.name] = secretInputs[f.name];
    } else if (values[f.name] !== undefined) {
      out[f.name] = values[f.name];
    }
  }
  return out;
}

/** Split a stored public config ({..., __secrets_set__: []}) into values and the list of saved secrets. */
export function splitConfig(config) {
  const { __secrets_set__: secretsSet = [], ...values } = config || {};
  return { values, secretsSet };
}
