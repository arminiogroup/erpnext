// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const erpnext = new URL("../../../../", import.meta.url);
const frappe = new URL("../../../../../frappe/", import.meta.url);
const sourcePath = new URL(
  "erpnext/selling/doctype/quotation/quotation.js",
  erpnext
);
const metadata = JSON.parse(
  fs.readFileSync(
    new URL("erpnext/selling/doctype/quotation/quotation.json", erpnext),
    "utf8"
  )
);
const targets = {
  "Customer Address": "Indirizzo del cliente",
  "Lead Address": "Indirizzo del contatto commerciale",
  "Prospect Address": "Indirizzo dell’azienda potenziale cliente",
  "CRM Deal Address": "Indirizzo della trattativa CRM",
};
const plain = (value) => JSON.parse(JSON.stringify(value));
const extractor = `import json, sys
from io import BytesIO
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
with open(sys.argv[1], "rb") as source:
    print(json.dumps([message for _, message, _, _ in extract("frappe.gettext.extractors.javascript.extract", BytesIO(source.read()), keywords=PYTHON_KEYWORDS)]))
`;
const extracted = new Set(
  JSON.parse(
    execFileSync(
      new URL("../../env/bin/python", erpnext).pathname,
      ["-c", extractor, sourcePath.pathname],
      { encoding: "utf8" }
    )
  )
);

function environment(messages) {
  const handlers = new Map();
  const scope = {
    erpnext: {
      accounts: {
        taxes: { setup_tax_validations() {}, setup_tax_filters() {} },
      },
      pre_sales: { set_as_lost() {} },
      sales_common: { setup_selling_controller() {} },
      selling: { SellingController: class {} },
    },
    $: {},
    frappe: {
      _messages: messages,
      ui: {
        form: {
          on: (name, methods) => handlers.set(name, methods),
          set_controller() {},
        },
      },
    },
  };
  scope.window = scope;
  scope.jQuery = scope.$;
  const context = vm.createContext(scope);
  function load(base, path, module = false) {
    const file = new URL(path, base);
    const source = fs.readFileSync(file, "utf8");
    vm.runInContext(
      module ? source.replace(/^import .*;\n/gm, "") : source,
      context,
      { filename: file.pathname }
    );
  }
  load(frappe, "frappe/public/js/frappe/format.js");
  load(frappe, "frappe/public/js/frappe/translate.js");
  load(frappe, "frappe/public/js/frappe/form/controls/base_control.js");
  load(frappe, "frappe/public/js/frappe/form/controls/base_input.js", true);
  load(erpnext, "erpnext/selling/doctype/quotation/quotation.js");
  function field() {
    const value = Object.create(scope.frappe.ui.form.ControlInput.prototype);
    value.df = {
      ...metadata.fields.find((row) => row.fieldname === "customer_address"),
      parent: "Quotation",
    };
    value.label_span = { innerHTML: "" };
    return value;
  }
  function label(kind) {
    const control = field();
    const doc = {
      quotation_to: kind,
      party_name: "PARTY-RAW",
      customer_address: "ADDRESS-RAW",
    };
    const before = structuredClone(doc);
    const fixed = { ...control.df };
    delete fixed.label;
    handlers
      .get("Quotation")
      .set_label({ doc, fields_dict: { customer_address: control } });
    assert.deepEqual(doc, before);
    const after = { ...control.df };
    delete after.label;
    assert.deepEqual(after, fixed);
    assert.equal(control.df.options, "Address");
    assert.equal(control._label, control.df.label);
    return control.label_span.innerHTML;
  }
  return { label, handlers };
}

for (const kind of ["Lead", "Prospect", "CRM Deal"]) {
  test(`${kind} has an extractable complete address label rendered by the actual control`, () => {
    const messages = { "Customer Address": targets["Customer Address"] };
    for (const [source, target] of Object.entries(targets))
      if (extracted.has(source)) messages[source] = target;
    const env = environment(messages);
    assert.equal(env.label(kind), targets[`${kind} Address`]);
  });
}

test("pre-existing complete translations retain their targets for every supported party type", () => {
  const env = environment(targets);
  for (const kind of ["Customer", "Lead", "Prospect", "CRM Deal"]) {
    assert.equal(env.label(kind), targets[`${kind} Address`]);
  }
});

test("English and empty-target fallbacks retain the complete known captions", () => {
  for (const messages of [
    {},
    Object.fromEntries(Object.keys(targets).map((key) => [key, ""])),
  ]) {
    const env = environment(messages);
    for (const kind of ["Customer", "Lead", "Prospect", "CRM Deal"])
      assert.equal(env.label(kind), `${kind} Address`);
  }
});

test("custom, empty, null and prototype-like types retain the original whole-key lookup", () => {
  const env = environment({
    ...targets,
    "Custom Party Address": "Indirizzo personalizzato",
    " Address": "Indirizzo senza tipo",
    "null Address": "Indirizzo con tipo nullo",
    "undefined Address": "Indirizzo con tipo assente",
    "toString Address": "Indirizzo del tipo personalizzato toString",
    "__proto__ Address": "Indirizzo del tipo personalizzato __proto__",
  });
  for (const [kind, expected] of [
    ["Custom Party", "Indirizzo personalizzato"],
    ["", "Indirizzo senza tipo"],
    [null, "Indirizzo con tipo nullo"],
    [undefined, "Indirizzo con tipo assente"],
    ["toString", "Indirizzo del tipo personalizzato toString"],
    ["__proto__", "Indirizzo del tipo personalizzato __proto__"],
    ["Untranslated Custom Party", "Untranslated Custom Party Address"],
  ])
    assert.equal(env.label(kind), expected);
});

test("the actual setup keeps canonical party query names and Address/Dynamic Link metadata", () => {
  const env = environment(targets);
  const queries = new Map();
  const frm = {
    cscript: {},
    set_query: (name, ...args) => queries.set(name, args),
    set_df_property() {},
    set_indicator_formatter() {},
  };
  env.handlers.get("Quotation").setup(frm);
  assert.deepEqual(plain(queries.get("quotation_to")[0]()), {
    filters: { name: ["in", ["Customer", "Lead", "Prospect", "CRM Deal"]] },
  });
  assert.deepEqual(plain(frm.custom_make_buttons), {
    "Sales Order": "Sales Order",
    Quotation: "New Version",
  });
  const party = metadata.fields.find((row) => row.fieldname === "party_name");
  assert.equal(party.fieldtype, "Dynamic Link");
  assert.equal(party.options, "quotation_to");
  assert.equal(
    metadata.fields.find((row) => row.fieldname === "quotation_to").default,
    "Customer"
  );
});
