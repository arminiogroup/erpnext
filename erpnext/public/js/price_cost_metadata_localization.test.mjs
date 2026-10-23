import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const require = createRequire(import.meta.url);
const { parse } = require("@babel/parser");
const { JSDOM } = require("jsdom");
const { transformSync } = require("esbuild");
const erp = fileURLToPath(new URL("../../../", import.meta.url));
const framework = fileURLToPath(
  new URL("../../../../frappe/", import.meta.url)
);
const messages = {
  Rate: "Prezzo unitario",
  "Rate:BOM Item": "Costo unitario",
  "Rate:Pricing Rule": "Prezzo",
  "Basic Rate:BOM Item": "Costo unitario",
  "Basic Rate:Sales Order Item": "Prezzo unitario",
  "Basic Rate (Company Currency)": "Prezzo unitario (valuta aziendale)",
  "Basic Rate (Company Currency):BOM Item": "Costo unitario (valuta aziendale)",
  "Quantity and Rate": "Quantità e prezzo",
  "Quantity and Rate:BOM Item": "Quantità e costi",
  "Quantity and Rate:Serial and Batch Bundle": "Quantità e valorizzazione",
  Rates: "Aliquote",
  "Rates:Stock Entry Detail": "Costi",
  "Advance Paid": "Acconti ricevuti",
  "Advance Paid:Purchase Order": "Acconti versati",
  "Price List Rate": "Prezzo di listino",
};

function methods(path, names, context, binding) {
  const text = readFileSync(path, "utf8");
  const found = new Map();
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.type === "ClassMethod" && names.includes(node.key.name)) {
      assert.ok(!found.has(node.key.name), `Duplicate method ${node.key.name}`);
      found.set(node.key.name, text.slice(node.start, node.end));
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(walk);
      else if (value && typeof value === "object") walk(value);
    }
  };
  walk(parse(text, { sourceType: "module" }));
  assert.equal(found.size, names.length);
  vm.runInContext(
    `globalThis.${binding} = class { ${[...found.values()].join("\n")} };`,
    context
  );
  return context[binding].prototype;
}

function field(module, doctype, name) {
  const metadata = JSON.parse(
    readFileSync(
      `${erp}/erpnext/${module}/doctype/${doctype}/${doctype}.json`,
      "utf8"
    )
  );
  return {
    ...metadata.fields.find((df) => df.fieldname === name),
    parent: metadata.name,
  };
}

function environment() {
  const dom = new JSDOM("<body></body>");
  const $ = require("jquery")(dom.window);
  const sandbox = {
    $,
    jQuery: $,
    frappe: {
      _messages: { ...messages },
      session: { user: "translator@example.com" },
      boot: { lang_dict: {} },
      form: {},
      ui: { form: { ControlData: class {} } },
      meta: { docfield_map: {} },
      scrub: (value, separator = "_") =>
        value.toLowerCase().replaceAll(" ", separator),
    },
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(
    readFileSync(
      `${framework}/frappe/public/js/frappe/utils/datatype.js`,
      "utf8"
    ),
    context
  );
  vm.runInContext(
    readFileSync(`${framework}/frappe/public/js/frappe/translate.js`, "utf8"),
    context
  );
  const input = methods(
    `${framework}/frappe/public/js/frappe/form/controls/base_input.js`,
    ["set_label"],
    context,
    "Input"
  );
  const grid = methods(
    `${framework}/frappe/public/js/frappe/form/grid_row.js`,
    ["setup_columns"],
    context,
    "Grid"
  );
  const section = methods(
    `${framework}/frappe/public/js/frappe/form/section.js`,
    ["make_head"],
    context,
    "Section"
  );
  const tab = methods(
    `${framework}/frappe/public/js/frappe/form/tab.js`,
    ["make"],
    context,
    "Tab"
  );
  const form = methods(
    `${framework}/frappe/public/js/frappe/form/form.js`,
    ["set_currency_labels", "reset_currency_labels"],
    context,
    "Form"
  );
  const transaction = methods(
    `${erp}/erpnext/public/js/controllers/transaction.js`,
    ["get_currency_label_options", "set_currency_labels_from_options"],
    context,
    "Transaction"
  );
  return {
    context,
    sandbox,
    $,
    dom,
    input,
    grid,
    section,
    tab,
    form,
    transaction,
  };
}

test("actual input and grid headers distinguish unit cost from commercial price", () => {
  const e = environment();
  assert.equal(e.sandbox.frappe.session.user, "translator@example.com");
  for (const [module, doctype, expected] of [
    ["manufacturing", "bom_item", "Costo unitario"],
    ["selling", "sales_order_item", "Prezzo unitario"],
    ["accounts", "pricing_rule", "Prezzo"],
  ]) {
    const df = field(module, doctype, "rate");
    const snapshot = JSON.stringify(df);
    const label = e.dom.window.document.createElement("label");
    const input = { df, label_span: label, show_description_on_click() {} };
    e.input.set_label.call(input);
    assert.equal(label.textContent, expected);
    const captions = [];
    const row = {
      grid: {
        setup_visible_columns() {},
        user_defined_columns: [],
        visible_columns: [[df, 2]],
      },
      docfields: [df],
      columns: {},
      columns_list: [],
      get_column_docfield: (fields, name) =>
        fields.find((f) => f.fieldname === name),
      set_dependant_property() {},
      make_column: (value, width, text) =>
        captions.push({ fieldname: value.fieldname, width, text }),
    };
    e.grid.setup_columns.call(row);
    assert.deepEqual(captions, [
      { fieldname: "rate", width: 2, text: expected },
    ]);
    assert.equal(JSON.stringify(df), snapshot);
    df.label = "Prezzo concordato con il cliente";
    delete input._label;
    e.input.set_label.call(input);
    assert.equal(label.textContent, df.label);
  }
});

test("actual section and tab headings retain their defining child DocType", () => {
  const e = environment();
  for (const [module, doctype, name, expected] of [
    ["manufacturing", "bom_item", "quantity_and_rate", "Quantità e costi"],
    ["selling", "sales_order_item", "quantity_and_rate", "Quantità e prezzo"],
    ["stock", "stock_entry_detail", "rates_section", "Costi"],
  ]) {
    const df = field(module, doctype, name);
    const section = { df, wrapper: e.$("<div>") };
    e.section.make_head.call(section);
    assert.equal(section.head.text().trim(), expected);
    assert.equal(df.fieldname, name);
  }
  const df = field(
    "stock",
    "serial_and_batch_bundle",
    "quantity_and_rate_section"
  );
  const tab = {
    df,
    label: df.label,
    doctype: df.parent,
    tab_link_container: e.$("<ul>"),
    tabs_content: e.$("<div>"),
  };
  e.tab.make.call(tab);
  assert.equal(
    tab.tab_link.find("button").text().trim(),
    "Quantità e valorizzazione"
  );
  assert.equal(
    tab.tab_link.find("button").attr("data-fieldname"),
    df.fieldname
  );
});

test("actual currency set/reset resolves both bare and full metadata labels", () => {
  const e = environment();
  for (const [module, doctype, expected, full] of [
    [
      "manufacturing",
      "bom_item",
      "Costo unitario",
      "Costo unitario (valuta aziendale)",
    ],
    [
      "selling",
      "sales_order_item",
      "Prezzo unitario",
      "Prezzo unitario (valuta aziendale)",
    ],
  ]) {
    const df = field(module, doctype, "base_rate");
    const original = {
      fieldname: df.fieldname,
      options: df.options,
      parent: df.parent,
    };
    e.sandbox.frappe.meta.docfield_map[df.parent] = { base_rate: df };
    const calls = [];
    const form = {
      fields_dict: {
        items: {
          grid: {
            doctype: df.parent,
            update_docfield_property: (name, property, value) => {
              calls.push([name, property, value]);
              df[property] = value;
            },
          },
        },
      },
    };
    e.form.set_currency_labels.call(form, ["base_rate"], "EUR", "items");
    assert.equal(calls.at(-1)[2], `${expected} (EUR)`);
    e.form.set_currency_labels.call(form, ["base_rate"], "USD", "items");
    assert.equal(calls.at(-1)[2], `${expected} (USD)`);
    e.form.reset_currency_labels.call(form, ["base_rate"], "items");
    assert.equal(calls.at(-1)[2], full);
    assert.equal(df._original_label, "Basic Rate (Company Currency)");
    assert.deepEqual(
      { fieldname: df.fieldname, options: df.options, parent: df.parent },
      original
    );
  }
});

test("actual transaction routing uses supplier account currency for advance caption", () => {
  const e = environment();
  for (const documentCurrency of ["USD", "EUR"]) {
    const df = field("buying", "purchase_order", "advance_paid");
    const original = JSON.stringify(df);
    e.sandbox.frappe.meta.docfield_map[df.parent] = { advance_paid: df };
    e.sandbox.frappe.meta.get_docfields = () => [df];
    const labels = [];
    const form = {
      doc: {
        doctype: df.parent,
        currency: documentCurrency,
        party_account_currency: "GBP",
      },
      fields_dict: {
        advance_paid: { set_label: (value) => labels.push(value) },
      },
    };
    form.set_currency_labels = e.form.set_currency_labels.bind(form);
    form.reset_currency_labels = e.form.reset_currency_labels.bind(form);
    const transaction = { frm: form };
    const options = e.transaction.get_currency_label_options.call(
      transaction,
      "EUR"
    );
    e.transaction.set_currency_labels_from_options.call(
      transaction,
      options,
      null,
      "EUR"
    );
    assert.deepEqual(labels, ["Acconti versati (GBP)"]);
    assert.equal(df.options, "party_account_currency");
    assert.equal(
      JSON.stringify({ ...df, _original_label: undefined }),
      original
    );
    assert.equal(form.doc.currency, documentCurrency);
    assert.equal(form.doc.party_account_currency, "GBP");
  }
});

test("actual Select displays price labels while preserving canonical Rate values", () => {
  const e = environment();
  const helper = transformSync(
    readFileSync(
      `${framework}/frappe/public/js/frappe/utils/select_option_context.ts`,
      "utf8"
    ),
    { loader: "ts", format: "esm" }
  ).code.replace("export {\n  selectOptionContext\n};\n", "");
  vm.runInContext(helper, e.context);
  vm.runInContext(
    readFileSync(
      `${framework}/frappe/public/js/frappe/form/controls/select.js`,
      "utf8"
    ).replace(
      'import { selectOptionContext } from "../../utils/select_option_context";',
      ""
    ),
    e.context
  );
  for (const [module, doctype, name, expected] of [
    [
      "stock",
      "stock_settings",
      "update_price_list_based_on",
      "Prezzo unitario",
    ],
    ["accounts", "pricing_rule", "rate_or_discount", "Prezzo"],
  ]) {
    const df = field(module, doctype, name);
    const original = JSON.stringify(df);
    const control = Object.create(
      e.sandbox.frappe.ui.form.ControlSelect.prototype
    );
    Object.assign(control, { df, doctype: df.parent, $input: e.$("<select>") });
    control.set_options();
    control.$input.val("Rate");
    assert.equal(control.$input.find(":selected").text(), expected);
    assert.equal(control.$input.val(), "Rate");
    assert.equal(JSON.stringify(df), original);
    const custom = Object.create(
      e.sandbox.frappe.ui.form.ControlSelect.prototype
    );
    Object.assign(custom, {
      df: {
        parent: df.parent,
        fieldname: name,
        options: [{ label: "Prezzo concordato", value: "Rate" }],
      },
      $input: e.$("<select>"),
    });
    custom.set_options();
    assert.equal(custom.$input.find("option").text(), "Prezzo concordato");
    assert.equal(custom.$input.val(), "Rate");
  }
});
