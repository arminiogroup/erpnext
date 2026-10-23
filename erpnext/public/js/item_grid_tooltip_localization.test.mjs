import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const { parse } = require("@babel/parser");
const { JSDOM } = require("jsdom");
const erp = fileURLToPath(new URL("../../../", import.meta.url));
const framework = fileURLToPath(
  new URL("../../../../frappe/", import.meta.url)
);
const messages = {
  Warehouse: "Magazzino",
  Available: "Disponibile",
  "Not Available": "Non disponibile",
};

function pick(source, predicate) {
  const matches = [];
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (predicate(node)) matches.push(source.slice(node.start, node.end));
    for (const child of Object.values(node)) {
      if (Array.isArray(child)) child.forEach(walk);
      else if (child && typeof child === "object") walk(child);
    }
  };
  walk(parse(source, { sourceType: "module" }));
  assert.equal(matches.length, 1);
  return matches[0];
}

function expandedTemplate() {
  const script = [
    "import frappe,json,sys",
    "from unittest.mock import patch",
    "from frappe.utils import get_html_format",
    'frappe.init(site="", sites_path="sites")',
    'frappe.set_user("translator@example.com")',
    'with patch("frappe.get_installed_apps",return_value=["erpnext","frappe"]):',
    " print(json.dumps(get_html_format(sys.argv[1])))",
  ].join("\n");
  return JSON.parse(
    execFileSync(
      resolve(framework, "../../env/bin/python"),
      ["-c", script, `${erp}/erpnext/templates/form_grid/item_grid.html`],
      { encoding: "utf8", cwd: resolve(framework, "../..") }
    )
  );
}

function host(t, translations = messages) {
  const dom = new JSDOM("<body></body>");
  t.after(() => dom.window.close());
  const $ = require("jquery")(dom.window);
  const context = vm.createContext({ $, jQuery: $, console });
  const frappe = {
    session: { user: "translator@example.com" },
    _messages: translations,
    provide() {},
    utils: {},
    boot: { sysdefaults: {} },
    model: {},
    get_format_helper: (doc) => doc,
    perm: { is_visible: () => true },
  };
  Object.assign(context, { frappe, window: context });
  for (const path of [
    "utils/datatype.js",
    "format.js",
    "translate.js",
    "utils/number_format.js",
    "microtemplate.js",
  ]) {
    vm.runInContext(
      readFileSync(
        `${framework}/frappe/public/js/frappe/${path}`,
        "utf8"
      ).replace('import "./datatype";', ""),
      context
    );
  }
  const common = readFileSync(
    `${framework}/frappe/public/js/frappe/utils/common.js`,
    "utf8"
  );
  const lstrip = pick(
    common,
    (node) =>
      node.type === "AssignmentExpression" &&
      node.left.type === "MemberExpression" &&
      node.left.object.name === "window" &&
      node.left.property.name === "lstrip"
  );
  vm.runInContext(`${lstrip};`, context);
  const utility = readFileSync(
    `${framework}/frappe/public/js/frappe/utils/utils.js`,
    "utf8"
  );
  const escape = pick(
    utility,
    (node) => node.type === "ObjectProperty" && node.key.name === "escape_html"
  );
  vm.runInContext(
    `frappe.utils.escape_html = (${escape.slice(escape.indexOf(":") + 1)});`,
    context
  );
  const model = readFileSync(
    `${framework}/frappe/public/js/frappe/model/model.js`,
    "utf8"
  );
  const layout = pick(
    model,
    (node) =>
      node.type === "ObjectProperty" && node.key.name === "layout_fields"
  );
  vm.runInContext(
    `frappe.model.layout_fields = (${layout.slice(layout.indexOf(":") + 1)});`,
    context
  );
  const source = readFileSync(
    `${framework}/frappe/public/js/frappe/form/grid_row.js`,
    "utf8"
  );
  const methods = [
    "make",
    "render_template",
    "set_row_index",
    "set_data",
    "get_visible_columns",
  ].map((name) =>
    pick(
      source,
      (node) => node.type === "ClassMethod" && node.key.name === name
    )
  );
  vm.runInContext(`globalThis.Row = class { ${methods.join("\n")} };`, context);
  return { context, frappe, $, Row: context.Row, template: expandedTemplate() };
}

const parents = [
  ["buying", "purchase_order"],
  ["stock", "purchase_receipt"],
  ["buying", "supplier_quotation"],
  ["stock", "delivery_note"],
  ["selling", "sales_order"],
  ["selling", "quotation"],
  ["accounts", "sales_invoice"],
  ["accounts", "purchase_invoice"],
  ["manufacturing", "bom"],
];

function render(e, parent, values = {}, delivered = 0, options = {}) {
  const [module, directory] =
    parents.find(([, name]) => name === parent) || parents[4];
  const parentPath = `${erp}/erpnext/${module}/doctype/${directory}/${directory}`;
  assert.match(
    readFileSync(`${parentPath}.py`, "utf8"),
    /form_grid_templates\s*=\s*\{"items": "templates\/form_grid\/item_grid.html"\}/
  );
  const metadata = JSON.parse(readFileSync(`${parentPath}.json`, "utf8"));
  const parentField = metadata.fields.find(
    (field) => field.fieldname === "items"
  );
  const childDirectory = parentField.options.toLowerCase().replaceAll(" ", "_");
  const child = JSON.parse(
    readFileSync(
      `${erp}/erpnext/${module}/doctype/${childDirectory}/${childDirectory}.json`,
      "utf8"
    )
  );
  const frm = {
    doc: {
      doctype: metadata.name,
      docstatus: options.docstatus ?? 0,
      per_delivered: delivered,
    },
    perm: [],
    get_perm: (level) => !level,
  };
  const raw = {
    doctype: parentField.options,
    name: "Warehouse",
    idx: 1,
    item_code: 'In Stock <SKU "A">',
    item_name: 'In Stock <SKU "A">',
    qty: 5,
    actual_qty: 5,
    projected_qty: 5,
    rate: 12.5,
    amount: 62.5,
    warehouse: child.fields.some((field) => field.fieldname === "warehouse")
      ? 'In Stock <Nord "A"> & Filiale'
      : undefined,
    uom: "Nos",
    ...values,
    get_formatted(field) {
      return (
        { qty: String(this.qty), rate: "12,50 €", amount: "62,50 €" }[field] ??
        String(this[field] ?? "")
      );
    },
  };
  const snapshot = JSON.stringify(raw);
  const row = new e.Row();
  Object.assign(row, {
    doc: options.header ? null : raw,
    frm,
    parent: e.$("body"),
    grid: {
      doctype: parentField.options,
      frm,
      meta: { ...child, editable_grid: options.editable ? 1 : 0 },
      template: options.template ?? e.template,
    },
    docfields: child.fields,
    row_check_html: "",
    render_row() {
      this.modernRenderCalls = (this.modernRenderCalls || 0) + 1;
      return true;
    },
  });
  row.make();
  assert.equal(JSON.stringify(raw), snapshot);
  if (row.row_display && !options.header) {
    assert.equal(row.row_display.find("script").length, 0);
    if (!options.template)
      assert.equal(
        row.row_display
          .find(".col-sm-6.col-xs-8")
          .children("strong, span.indicator")
          .first()
          .text(),
        raw.item_code
      );
  }
  const indicator = row.row_display?.find(
    "span.pull-right[title] > span.indicator"
  );
  const observation = {
    parent: metadata.name,
    child: raw.doctype,
    raw_warehouse: raw.warehouse,
    actual: raw.actual_qty,
    projected: raw.projected_qty,
    quantity: raw.qty,
    delivered,
    title: indicator?.length ? indicator.parent().attr("title") : null,
    color: indicator?.length ? indicator.attr("class").split(" ").at(-1) : null,
    warehouse_text: indicator?.length ? indicator.text().trim() : null,
  };
  const clone = row.wrapper.clone();
  clone
    .find("span.pull-right[title] > span.indicator")
    .parent()
    .attr("title", "CAPTION");
  const parity = {
    form: JSON.parse(JSON.stringify(frm.doc)),
    raw: JSON.parse(JSON.stringify(raw)),
    html: clone[0].outerHTML,
  };
  if (indicator?.length)
    assert.equal(observation.warehouse_text, raw.warehouse);
  return { row, raw, observation, parity };
}

test("metadata warehouse titles use Italian without translating actual warehouse/item names", (t) => {
  const e = host(t);
  const results = parents
    .filter(([, parent]) => parent !== "bom")
    .map(([, parent]) => render(e, parent, {}, 100).observation);
  for (const value of results) assert.equal(value.color, "grey");
  assert.deepEqual(
    results.map((value) => value.title),
    results.map(() => "Magazzino")
  );
});

test("actual inclusive sufficient-stock branches use the compiled Italian availability term", (t) => {
  const e = host(t);
  const results = [
    render(e, "sales_order", { actual_qty: 0, projected_qty: 5 }).observation,
    render(e, "delivery_note", { actual_qty: 5, projected_qty: 0 }).observation,
    render(e, "sales_order", { qty: 0, actual_qty: 0, projected_qty: 0 })
      .observation,
  ];
  for (const value of results) assert.equal(value.color, "green");
  assert.deepEqual(
    results.map((value) => value.title),
    results.map(() => "Disponibile")
  );
});

test("actual insufficient-stock branches expose the complete Italian tooltip", (t) => {
  const e = host(t);
  const results = [
    render(e, "sales_order", { actual_qty: 99, projected_qty: 4 }).observation,
    render(e, "delivery_note", { actual_qty: 4.9, projected_qty: 99 })
      .observation,
    render(e, "delivery_note", { actual_qty: 0, projected_qty: 99 })
      .observation,
  ];
  for (const value of results) assert.equal(value.color, "red");
  assert.deepEqual(
    results.map((value) => value.title),
    results.map(() => "Non disponibile")
  );
});

test("existing quantity, projected/actual, null and rounded-delivery predicates remain intact", (t) => {
  const e = host(t, {});
  const cases = [
    [
      "sales_order",
      { actual_qty: 99, projected_qty: undefined },
      0,
      "Warehouse",
      "grey",
    ],
    [
      "sales_order",
      { actual_qty: 99, projected_qty: null },
      0,
      "Warehouse",
      "grey",
    ],
    [
      "delivery_note",
      { actual_qty: undefined, projected_qty: 99 },
      0,
      "Warehouse",
      "grey",
    ],
    [
      "delivery_note",
      { actual_qty: null, projected_qty: 99 },
      0,
      "Warehouse",
      "grey",
    ],
    [
      "sales_order",
      { actual_qty: 0, projected_qty: 10 },
      99.994,
      "Available",
      "green",
    ],
    [
      "sales_order",
      { actual_qty: 0, projected_qty: 10 },
      99.999,
      "Warehouse",
      "grey",
    ],
    ["delivery_note", { actual_qty: 10 }, 100, "Warehouse", "grey"],
    ["delivery_note", { actual_qty: 10 }, 101, "Warehouse", "grey"],
    [
      "sales_order",
      { actual_qty: 99, projected_qty: 4 },
      0,
      "Not Available",
      "red",
    ],
    [
      "delivery_note",
      { actual_qty: 5, projected_qty: 0 },
      0,
      "Available",
      "green",
    ],
    [
      "sales_order",
      { doctype: "Custom Item", projected_qty: 10 },
      0,
      "Warehouse",
      "grey",
    ],
  ];
  const results = cases.map(([parent, values, delivered, title, color]) => {
    const result = render(e, parent, values, delivered).observation;
    assert.equal(result.color, color);
    return result;
  });
  assert.deepEqual(
    results.map((result) => result.title),
    cases.map(([, , , title]) => title)
  );
});

test("no warehouse, header, modern grid and custom-template controls keep their own display", (t) => {
  const e = host(t);
  for (const warehouse of [undefined, null, ""])
    assert.equal(
      render(e, "sales_order", { warehouse }).observation.title,
      null
    );
  assert.equal(render(e, "bom").observation.title, null);
  assert.equal(
    render(e, "sales_order", {}, 0, { header: true }).observation.title,
    null
  );
  const modern = render(e, "sales_order", {}, 0, { editable: true }).row;
  assert.equal(modern.modernRenderCalls, 1);
  assert.equal(modern.row_display, undefined);
  const custom = render(e, "sales_order", {}, 0, {
    template:
      '<span class="custom-group" title="CUSTOM GROUP">{%= frappe.utils.escape_html(doc.item_code) %}</span>',
  }).row;
  assert.equal(
    custom.row_display.find(".custom-group").attr("title"),
    "CUSTOM GROUP"
  );
  assert.equal(
    custom.row_display.find(".custom-group").text(),
    'In Stock <SKU "A">'
  );
});

test("rate/amount visibility and submitted pending-row presentation keep their contracts", (t) => {
  const e = host(t, {});
  for (const allowed of [true, false]) {
    e.frappe.perm.is_visible = () => allowed;
    const result = render(e, "sales_order", { discount_percentage: 5 });
    const text = result.row.row_display.text();
    assert.equal(text.includes("12,50 €"), allowed);
    assert.equal(text.includes("62,50 €"), allowed);
    assert.equal(
      result.observation.raw_warehouse,
      result.observation.warehouse_text
    );
  }
  for (const delivered of [2, 5]) {
    const result = render(e, "sales_order", { delivered_qty: delivered }, 0, {
      docstatus: 1,
    });
    const item = result.row.row_display
      .find(".col-sm-6.col-xs-8")
      .children("span.indicator");
    assert.equal(item.text(), result.raw.item_code);
    assert.equal(
      item.hasClass(delivered < result.raw.qty ? "orange" : "green"),
      true
    );
  }
});

test("localized tooltip quotes and markup remain literal attribute text", (t) => {
  const labels = {
    Warehouse: 'Magazzino "A" & <B>',
    Available: 'Disponibile "A" & <B>',
    "Not Available": 'Non disponibile "A" & <B>',
  };
  const e = host(t, labels);
  for (const [label, projected, delivered] of [
    ["Warehouse", 5, 100],
    ["Available", 5, 0],
    ["Not Available", 4, 0],
  ]) {
    const result = render(
      e,
      "sales_order",
      { actual_qty: 0, projected_qty: projected },
      delivered
    );
    assert.equal(result.observation.title, labels[label]);
    assert.equal(result.row.row_display.find("B").length, 0);
  }
});

export { host, render };
