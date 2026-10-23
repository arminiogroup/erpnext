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
const SOURCE = "erpnext/accounts/doctype/payment_entry/payment_entry.js";
const FRAME = "{0}: {1} must be less than or equal to {2}";
const MANDATORY = "Error: {0} is a mandatory field";
const TARGETS = {
  [FRAME]:
    "{0}: il valore di {1} deve essere minore o uguale al valore di {2}.",
  [MANDATORY]: "Errore: {0} è un campo obbligatorio",
  "Posting Date": "Data di registrazione",
  "From Date": "Dalla data",
  "To Date": "Alla data",
  Filters: "Filtri",
  "Get Outstanding Invoices": "Recupera fatture da saldare",
  "Get Outstanding Orders": "Recupera ordini da saldare",
};

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

test("actual posting errors use prompt captions and truthful inclusive wording", () => {
  const env = environment(),
    handler = env.forms["Payment Entry"];
  for (const [filters, expected] of [
    [
      { from_posting_date: "2026-02-01", to_posting_date: "2026-01-01" },
      "Data di registrazione: il valore di Dalla data deve essere minore o uguale al valore di Alla data.",
    ],
    [
      { from_posting_date: "2026-01-01" },
      "Errore: Alla data è un campo obbligatorio",
    ],
  ]) {
    const before = plain(filters);
    assert.throws(
      () => handler.validate_filters_data({}, filters),
      (error) => error.message === expected
    );
    assert.deepEqual(filters, before);
  }
});

test("all current map/open-bound/equality/zero decisions remain unchanged", () => {
  const env = environment(),
    handler = env.forms["Payment Entry"];
  for (const filters of [
    {},
    { from_posting_date: "2026-01-01", to_posting_date: "2026-01-01" },
    { to_posting_date: "2026-01-01" },
    {
      from_posting_date: "2026-01-01",
      to_posting_date: "2026-02-01",
      from_due_date: "2026-03-01",
      to_due_date: "2026-02-01",
    },
    { from_due_date: "2026-01-01" },
    { to_due_date: "2026-01-01" },
    { outstanding_amt_greater_than: 20, outstanding_amt_less_than: 10 },
    { outstanding_amt_greater_than: 0, outstanding_amt_less_than: -1 },
    { outstanding_amt_greater_than: 10 },
    { outstanding_amt_less_than: 10 },
  ]) {
    const before = plain(filters);
    handler.validate_filters_data({}, filters);
    assert.deepEqual(filters, before);
  }
});

for (const invoices of [true, false]) {
  test(`actual ${
    invoices ? "invoice" : "order"
  } prompt and callback preserve canonical delivery`, () => {
    const env = environment(),
      handler = env.forms["Payment Entry"],
      delivered = [];
    const frm = {
      doc: { name: "PE-RAW", cost_center: "BEFORE-RAW" },
      events: {
        ...handler,
        get_outstanding_documents(...args) {
          delivered.push(args);
        },
      },
    };
    handler.get_outstanding_invoices_or_orders(frm, invoices, !invoices);
    const [fields, callback, title, button] = env.prompts[0];
    const map = Object.fromEntries(
      fields
        .filter((field) => field.fieldname)
        .map((field) => [field.fieldname, field])
    );
    for (const name of [
      "from_posting_date",
      "to_posting_date",
      "from_due_date",
      "to_due_date",
      "outstanding_amt_greater_than",
      "outstanding_amt_less_than",
    ])
      assert(map[name]);
    assert.equal(map.from_posting_date.label, "Dalla data");
    assert.equal(map.to_posting_date.label, "Alla data");
    assert.equal(map.from_due_date.fieldtype, "Date");
    assert.equal(map.outstanding_amt_greater_than.fieldtype, "Float");
    assert(!map.advance_amount);
    assert.equal(title, "Filtri");
    assert.equal(
      button,
      TARGETS[invoices ? "Get Outstanding Invoices" : "Get Outstanding Orders"]
    );
    const filters = {
        from_posting_date: "2026-01-01",
        to_posting_date: "2026-02-01",
        from_due_date: "2026-03-01",
        to_due_date: "2026-02-01",
        cost_center: "CC-RAW",
        allocate_payment_amount: 0,
      },
      before = plain(filters);
    callback(filters);
    assert.deepEqual(filters, before);
    assert.equal(frm.doc.cost_center, "CC-RAW");
    assert.equal(env.w.frappe.flags.allocate_payment_amount, true);
    assert.equal(delivered[0][0], frm);
    assert.equal(delivered[0][1], filters);
    assert.deepEqual(delivered[0].slice(2), [invoices, !invoices]);
  });
}

test("empty dictionary and hostile fieldname translations use actual captions", () => {
  for (const messages of [
    {},
    { from_posting_date: "WRONG", to_posting_date: "WRONG" },
  ]) {
    const env = environment(messages);
    assert.throws(
      () =>
        env.forms["Payment Entry"].validate_filters_data(
          {},
          { from_posting_date: "2026-02-01", to_posting_date: "2026-01-01" }
        ),
      (error) =>
        error.message ===
        "Posting Date: From Date must be less than or equal to To Date"
    );
  }
});
