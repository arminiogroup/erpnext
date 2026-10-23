// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const appRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../.."
);
const framework = path.resolve(appRoot, "../frappe");
const plain = (value) => JSON.parse(JSON.stringify(value));
const SOURCE = "erpnext/accounts/doctype/pos_settings/pos_settings.js";
const FRAME =
  "The following rows are not valid fields of {0} and have to be removed: {1}";
const TARGETS = {
  [FRAME]:
    "Le seguenti righe non corrispondono a campi validi di {0} e devono essere rimosse: {1}.",
  "Sales Invoice": "Fattura di vendita",
  "POS Invoice": "Fattura POS",
  "Invalid POS Fields": "Campi POS non validi",
  MISSING_RAW_FIELD: "DO NOT TRANSLATE FIELDNAME",
};
function metadata(env, doctype) {
  const slug = doctype.toLowerCase().replaceAll(" ", "_");
  const meta = JSON.parse(
    fs.readFileSync(
      path.join(appRoot, `erpnext/accounts/doctype/${slug}/${slug}.json`),
      "utf8"
    )
  );
  env.w.locals.DocType[doctype] = meta;
  return meta;
}

function environment(messages = TARGETS) {
  const w = {};
  w.window = w;
  w.locals = { DocType: {} };
  w.$ = w.jQuery = {
    extend: Object.assign,
    isPlainObject: (value) =>
      value !== null && typeof value === "object" && !Array.isArray(value),
    each(values, callback) {
      for (const [key, value] of Object.entries(values)) {
        if (callback(Number.isNaN(+key) ? key : +key, value) === false) break;
      }
    },
  };
  const forms = {},
    events = [],
    prompts = [],
    pending = [];
  w.fetch = () => {
    throw Error("No network");
  };
  w.XMLHttpRequest = class {
    constructor() {
      throw Error("No XHR");
    }
  };
  w.erpnext = {
    accounts: { taxes: { setup_tax_validations() {}, setup_tax_filters() {} } },
  };
  w.frappe = {
    _messages: messages,
    ui: {
      form: {
        on(doctype, handlers) {
          forms[doctype] = handlers;
        },
      },
    },
    boot: { lang: "it" },
    session: { user: "translator@example.com" },
    flags: {},
    utils: {},
    provide(name) {
      name.split(".").reduce((object, key) => (object[key] ||= {}), w);
    },
    throw(message) {
      throw Error(message);
    },
    msgprint(message) {
      events.push(message);
    },
    prompt(...args) {
      prompts.push(args);
    },
    datetime: { get_today: () => "2026-10-21", add_days: () => "2026-09-21" },
  };
  const context = vm.createContext(w);
  for (const file of [
    "frappe/public/js/frappe/format.js",
    "frappe/public/js/frappe/translate.js",
    "frappe/public/js/frappe/model/model.js",
    "frappe/public/js/frappe/model/meta.js",
  ]) {
    vm.runInContext(
      fs.readFileSync(path.join(framework, file), "utf8"),
      context,
      { filename: file }
    );
  }
  vm.runInContext(
    fs.readFileSync(path.join(appRoot, SOURCE), "utf8"),
    context,
    { filename: SOURCE }
  );
  return { w, forms, events, prompts, pending };
}

for (const doctype of ["Sales Invoice", "POS Invoice"]) {
  test(`actual ${doctype} metadata and notice preserve raw fields`, () => {
    const env = environment(),
      meta = metadata(env, doctype);
    const valid = meta.fields.find(
      (field) => !env.w.frappe.model.no_value_type.includes(field.fieldtype)
    );
    const section = meta.fields.find(
      (field) =>
        env.w.frappe.model.no_value_type.includes(field.fieldtype) &&
        field.fieldtype !== "Button"
    );
    const button = meta.fields.find((field) => field.fieldtype === "Button");
    assert(valid && section);
    const fields = [
      { idx: 1, fieldname: "MISSING_RAW_FIELD" },
      { idx: 2, fieldname: section.fieldname },
      { idx: 3, fieldname: valid.fieldname },
      { idx: 4, fieldname: "" },
    ];
    if (button) fields.push({ idx: 5, fieldname: button.fieldname });
    const frm = { doc: { invoice_type: doctype, invoice_fields: fields } },
      before = plain(frm.doc);
    env.forms["POS Settings"].validate_invoice_fields(frm);
    assert.deepEqual(frm.doc, before);
    assert.deepEqual(plain(env.events), [
      {
        title: "Campi POS non validi",
        indicator: "orange",
        message: TARGETS[FRAME].replace(
          "{0}",
          `<b>${TARGETS[doctype]}</b>`
        ).replace("{1}", `#1 MISSING_RAW_FIELD, #2 ${section.fieldname}`),
      },
    ]);
    frm.doc.invoice_fields = [{ idx: 1, fieldname: valid.fieldname }];
    env.forms["POS Settings"].validate_invoice_fields(frm);
    frm.doc.invoice_fields = [];
    env.forms["POS Settings"].validate_invoice_fields(frm);
    assert.equal(env.events.length, 1);
  });
}

test("English/empty/unknown type falls back with original markup", () => {
  const env = environment({});
  env.w.locals.DocType["Custom Invoice"] = { fields: [] };
  env.forms["POS Settings"].validate_invoice_fields({
    doc: {
      invoice_type: "Custom Invoice",
      invoice_fields: [{ idx: 1, fieldname: "MISSING_RAW_FIELD" }],
    },
  });
  assert.equal(
    env.events[0].message,
    FRAME.replace("{0}", "<b>Custom Invoice</b>").replace(
      "{1}",
      "#1 MISSING_RAW_FIELD"
    )
  );
});

test("actual asynchronous metadata handler keeps type-race guard and raw options", () => {
  const env = environment();
  metadata(env, "Sales Invoice");
  metadata(env, "POS Invoice");
  env.w.frappe.model.with_doctype = (doctype, callback) =>
    env.pending.push({ doctype, callback });
  const changes = [],
    triggers = [];
  const frm = {
    doc: { invoice_type: "Sales Invoice" },
    fields_dict: {
      invoice_fields: {
        grid: {
          update_docfield_property(...args) {
            changes.push(args);
          },
        },
      },
    },
    trigger(name) {
      triggers.push(name);
    },
  };
  env.forms["POS Settings"].get_invoice_fields(frm);
  frm.doc.invoice_type = "POS Invoice";
  env.pending[0].callback();
  assert.deepEqual(changes, []);
  assert.deepEqual(triggers, []);
  env.forms["POS Settings"].get_invoice_fields(frm);
  env.pending[1].callback();
  assert.deepEqual(triggers, ["validate_invoice_fields"]);
  const options = plain(changes[0][2]);
  assert.equal(options[0], "");
  for (const option of options.slice(1)) {
    const field = env.w.locals.DocType["POS Invoice"].fields.find(
      (row) => row.fieldname === option.value
    );
    assert.equal(option.label, `${field.label} (${field.fieldtype})`);
  }
});
