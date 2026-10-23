import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const erp = fileURLToPath(new URL("../../../../", import.meta.url));
const framework = resolve(erp, "../frappe");
const plain = (value) => JSON.parse(JSON.stringify(value));
const messages = {
  "Party Type must be one of {0}.":
    "Il tipo di controparte deve essere uno dei seguenti: {0}.",
  "Please select {0} first": "Selezionare prima {0}.",
  Customer: "Cliente",
  Supplier: "Fornitore",
  Employee: "Dipendente",
  Shareholder: "Azionista",
  Company: "Azienda",
  "Party Type": "Tipo di controparte",
  Party: "Controparte",
  "Payment Type": "Tipo di pagamento",
};
const partyAccounts = {
  Customer: "Receivable",
  Supplier: "Payable",
  Employee: "Payable",
  Shareholder: "Payable",
  "Custom Counterparty": "Receivable",
};

function environment(dictionary = messages) {
  const window = {};
  window.window = window;
  const context = vm.createContext(window);
  window.$ = window.jQuery = {
    extend: Object.assign,
    isPlainObject(value) {
      return (
        value !== null && typeof value === "object" && !Array.isArray(value)
      );
    },
    each(values, callback) {
      for (const [key, value] of Object.entries(values)) {
        if (callback(Number.isNaN(+key) ? key : +key, value) === false) break;
      }
    },
  };
  window.fetch = () => {
    throw Error("Unexpected fetch");
  };
  window.XMLHttpRequest = class {
    constructor() {
      throw Error("Unexpected XHR");
    }
  };
  const forms = {};
  const events = [];
  window.locals = {
    DocType: {},
    ":Company": { "COMPANY-RAW": { default_currency: "EUR" } },
  };
  window.erpnext = {
    accounts: { taxes: { setup_tax_validations() {}, setup_tax_filters() {} } },
  };
  window.frappe = {
    _messages: { ...dictionary },
    session: { user: "translator@example.com" },
    boot: { lang: "it", party_account_types: { ...partyAccounts } },
    flags: {},
    ui: {
      form: {
        on(doctype, handlers) {
          forms[doctype] = handlers;
        },
      },
    },
    provide(name) {
      let object = window;
      for (const key of name.split(".")) object = object[key] ||= {};
    },
    throw(message) {
      throw Error(message);
    },
    msgprint(message) {
      events.push(["msgprint", message]);
    },
    call(options) {
      events.push(["rpc_boundary", options.method, plain(options.args)]);
      return "RPC-BOUNDARY";
    },
  };
  for (const relative of [
    "frappe/public/js/frappe/format.js",
    "frappe/public/js/frappe/translate.js",
    "frappe/public/js/frappe/model/model.js",
    "frappe/public/js/frappe/model/meta.js",
  ])
    vm.runInContext(
      readFileSync(resolve(framework, relative), "utf8"),
      context,
      { filename: relative }
    );
  const metadata = JSON.parse(
    readFileSync(
      resolve(erp, "erpnext/accounts/doctype/payment_entry/payment_entry.json"),
      "utf8"
    )
  );
  const fields = metadata.fields.map((field) => ({
    ...field,
    parent: "Payment Entry",
  }));
  window.frappe.meta.docfield_map["Payment Entry"] = Object.fromEntries(
    fields.map((field) => [field.fieldname, field])
  );
  window.frappe.meta.docfield_list["Payment Entry"] = fields;
  window.frappe.meta.docfield_copy["Payment Entry"] = Object.fromEntries(
    ["PE-RAW", "OTHER-PE-RAW"].map((name) => [
      name,
      Object.fromEntries(
        fields.map((field) => [field.fieldname, plain(field)])
      ),
    ])
  );
  for (const relative of [
    "erpnext/public/js/queries.js",
    "erpnext/accounts/doctype/payment_entry/payment_entry.js",
  ])
    vm.runInContext(readFileSync(resolve(erp, relative), "utf8"), context, {
      filename: relative,
    });
  function form(values = {}) {
    const frm = {
      doctype: "Payment Entry",
      doc: {
        name: "PE-RAW",
        company: "COMPANY-RAW",
        party_type: "Customer",
        party: "PARTY-RAW",
        payment_type: "Receive",
        posting_date: "2026-10-21",
        paid_from: "ACCOUNT-FROM-RAW",
        paid_to: "ACCOUNT-TO-RAW",
        references: [{ reference_name: "SI-RAW", allocated_amount: 12.5 }],
        ...values,
      },
      events: forms["Payment Entry"],
      set_value(field, value) {
        events.push(["set_value", field, value]);
        this.doc[field] = value;
      },
      set_query(field, callback) {
        events.push(["query", field, plain(callback() ?? null)]);
      },
      clear_table(field) {
        events.push(["clear_table", field]);
        this.doc[field] = [];
      },
    };
    return frm;
  }
  return { window, forms, events, form };
}

test("invalid party type displays ordered DocType labels while preserving raw boot keys", () => {
  const h = environment();
  const frm = h.form({ party_type: "Invalid Type", party: "PARTY-RAW" });
  const before = plain(frm.doc);
  const boot = plain(h.window.frappe.boot.party_account_types);
  assert.throws(() => frm.events.party_type(frm), {
    message:
      "Il tipo di controparte deve essere uno dei seguenti: Cliente, Fornitore, Dipendente, Azionista, Custom Counterparty.",
  });
  assert.deepEqual(plain(frm.doc), { ...before, party_type: "" });
  assert.deepEqual(h.events, [["set_value", "party_type", ""]]);
  assert.deepEqual(plain(h.window.frappe.boot.party_account_types), boot);
});

test("valid canonical types and blank type retain exact party queries", () => {
  for (const type of ["", ...Object.keys(partyAccounts)]) {
    const h = environment();
    const frm = h.form({ party_type: type, party: null });
    const before = plain(frm.doc);
    frm.events.party_type(frm);
    const query =
      type === "Employee"
        ? { query: "erpnext.controllers.queries.employee_query" }
        : ["Customer", "Supplier"].includes(type)
        ? {
            query: "erpnext.controllers.queries.party_query",
            filters: { disabled: 0, company: "COMPANY-RAW" },
          }
        : type === "Shareholder"
        ? { filters: { company: "COMPANY-RAW" } }
        : null;
    assert.deepEqual(h.events, [["query", "party", query]]);
    assert.deepEqual(plain(frm.doc), before);
    assert.deepEqual(
      plain(h.window.frappe.boot.party_account_types),
      partyAccounts
    );
  }
});

test("changing a valid type keeps existing reset fields and order", () => {
  const h = environment();
  const frm = h.form({
    party: "PARTY-RAW",
    paid_from_account_currency: "EUR",
    paid_to_account_currency: "USD",
    total_allocated_amount: 12.5,
  });
  const before = plain(frm.doc);
  frm.events.party_type(frm);
  const reset = [
    "party",
    "paid_from",
    "paid_to",
    "paid_from_account_currency",
    "paid_to_account_currency",
    "references",
    "total_allocated_amount",
  ];
  assert.deepEqual(
    h.events.slice(1),
    reset.map((field) => ["set_value", field, null])
  );
  assert.deepEqual(plain(frm.doc), {
    ...before,
    ...Object.fromEntries(reset.map((field) => [field, null])),
  });
});

test("mandatory fields use actual metadata labels and preserve undefined guard return", () => {
  const labels = {
    company: "Azienda",
    party_type: "Tipo di controparte",
    party: "Controparte",
    payment_type: "Tipo di pagamento",
  };
  for (const [field, label] of Object.entries(labels)) {
    const h = environment();
    const frm = h.form({ [field]: null });
    const before = plain(frm.doc);
    assert.equal(frm.events.check_mandatory_to_fetch(frm), undefined);
    assert.deepEqual(h.events, [["msgprint", `Selezionare prima ${label}.`]]);
    assert.deepEqual(plain(frm.doc), before);
  }
  const h = environment();
  const frm = h.form();
  assert.equal(frm.events.check_mandatory_to_fetch(frm), undefined);
  assert.deepEqual(h.events, []);
});

test("per-document custom label and owner context override canonical metadata only for display", () => {
  const h = environment();
  h.window.frappe.meta.docfield_copy["Payment Entry"][
    "PE-RAW"
  ].payment_type.label = "CUSTOM PAYMENT LABEL";
  h.window.frappe._messages["CUSTOM PAYMENT LABEL:Payment Entry"] =
    "Etichetta personalizzata del pagamento";
  const fields = plain(h.window.frappe.meta.docfield_map["Payment Entry"]);
  const frm = h.form({ payment_type: null });
  const before = plain(frm.doc);
  frm.events.check_mandatory_to_fetch(frm);
  assert.deepEqual(h.events, [
    ["msgprint", "Selezionare prima Etichetta personalizzata del pagamento."],
  ]);
  assert.deepEqual(plain(frm.doc), before);
  assert.deepEqual(
    plain(h.window.frappe.meta.docfield_map["Payment Entry"]),
    fields
  );
  h.events.length = 0;
  const other = h.form({ name: "OTHER-PE-RAW", payment_type: null });
  other.events.check_mandatory_to_fetch(other);
  assert.deepEqual(h.events, [
    ["msgprint", "Selezionare prima Tipo di pagamento."],
  ]);
});

test("missing metadata and English dictionary retain existing fallbacks", () => {
  const h = environment();
  delete h.window.frappe.meta.docfield_copy["Payment Entry"]["PE-RAW"]
    .payment_type;
  h.form({ payment_type: null }).events.check_mandatory_to_fetch(
    h.form({ payment_type: null })
  );
  assert.deepEqual(h.events, [["msgprint", "Selezionare prima payment_type."]]);
  const english = environment({});
  const frm = english.form({ payment_type: null });
  frm.events.check_mandatory_to_fetch(frm);
  assert.deepEqual(english.events, [
    ["msgprint", "Please select Payment Type first"],
  ]);
  const invalid = english.form({ party_type: "Invalid Type" });
  assert.throws(() => invalid.events.party_type(invalid), {
    message:
      "Party Type must be one of Customer, Supplier, Employee, Shareholder, Custom Counterparty.",
  });
});

test("actual outstanding caller retains RPC continuation and raw arguments after mandatory warning", () => {
  const h = environment();
  const frm = h.form({ payment_type: null });
  const before = plain(frm.doc);
  const filters = {
    allocate_payment_amount: false,
    from_posting_date: "2026-01-01",
    outstanding_amt_greater_than: 12.5,
  };
  assert.equal(
    frm.events.get_outstanding_documents(frm, filters, true, false),
    "RPC-BOUNDARY"
  );
  assert.deepEqual(h.events, [
    ["clear_table", "references"],
    ["msgprint", "Selezionare prima Tipo di pagamento."],
    [
      "rpc_boundary",
      "erpnext.accounts.doctype.payment_entry.payment_entry.get_outstanding_reference_documents",
      {
        args: {
          posting_date: "2026-10-21",
          company: "COMPANY-RAW",
          party_type: "Customer",
          payment_type: null,
          party: "PARTY-RAW",
          party_account: "ACCOUNT-TO-RAW",
          ...filters,
          get_outstanding_invoices: true,
        },
      },
    ],
  ]);
  assert.deepEqual(plain(frm.doc), { ...before, references: [] });
  assert.equal(h.window.frappe.flags.allocate_payment_amount, false);
});

test("missing party caller still stops before metadata warning and RPC", () => {
  const h = environment();
  const frm = h.form({ party: null, payment_type: null });
  const before = plain(frm.doc);
  assert.equal(
    frm.events.get_outstanding_documents(frm, {}, true, false),
    undefined
  );
  assert.deepEqual(h.events, [["clear_table", "references"]]);
  assert.deepEqual(plain(frm.doc), { ...before, references: [] });
});
