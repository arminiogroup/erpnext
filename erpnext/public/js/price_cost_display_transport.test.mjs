import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const { parse } = require("@babel/parser");
const { JSDOM } = require("jsdom");
const erp = fileURLToPath(new URL("../../../", import.meta.url));
const framework = fileURLToPath(
  new URL("../../../../frappe/", import.meta.url)
);

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

function host(t) {
  const dom = new JSDOM("<body></body>");
  t.after(() => dom.window.close());
  const $ = require("jquery")(dom.window);
  const dialogs = [],
    requests = [],
    routes = [],
    messages = [];
  const context = vm.createContext({
    $,
    jQuery: $,
    console,
    erpnext: { stock: {} },
  });
  const frappe = {
    session: { user: "translator@example.com" },
    _messages: {
      Rate: "Prezzo unitario",
      "Rate:BOM Item": "Costo unitario",
      "Rate:Stock Entry": "Costo unitario di valorizzazione",
    },
    provide() {},
    utils: {},
    boot: { sysdefaults: {} },
    call: (request) => requests.push(request),
    set_route: (...args) => routes.push(args),
    msgprint: (message) => messages.push(message),
    model: { sync: (doc) => [doc] },
    get_format_helper: (doc) => doc,
    ui: {
      Dialog: class {
        constructor(options) {
          this.options = options;
          this.fields = Object.fromEntries(
            options.fields.map((df) => [
              df.fieldname,
              {
                df: { ...df },
                value: undefined,
                set_input(value) {
                  this.value = value;
                },
                set_value(value) {
                  this.value = value;
                },
                refresh() {},
              },
            ])
          );
          dialogs.push(this);
        }
        show() {
          this.shown = true;
        }
        get_field(name) {
          return this.fields[name];
        }
        get_value(name) {
          return this.fields[name].value;
        }
        set_primary_action(label, action) {
          this.primary_label = label;
          this.primary = action;
        }
      },
    },
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
  const utility = readFileSync(
    `${framework}/frappe/public/js/frappe/utils/utils.js`,
    "utf8"
  );
  const property = pick(
    utility,
    (node) => node.type === "ObjectProperty" && node.key.name === "escape_html"
  );
  vm.runInContext(
    `frappe.utils.escape_html = (${property.slice(
      property.indexOf(":") + 1
    )});`,
    context
  );
  const row = readFileSync(
    `${framework}/frappe/public/js/frappe/form/grid_row.js`,
    "utf8"
  );
  const render = pick(
    row,
    (node) => node.type === "ClassMethod" && node.key.name === "render_template"
  );
  vm.runInContext(`globalThis.Row = class { ${render} };`, context);
  vm.runInContext(
    readFileSync(`${erp}/erpnext/stock/dashboard/item_dashboard.js`, "utf8"),
    context
  );
  return {
    context,
    frappe,
    $,
    dialogs,
    requests,
    routes,
    messages,
    row: context.Row.prototype,
  };
}

function gridTemplate() {
  const code = [
    "import frappe,json,sys",
    "from unittest.mock import patch",
    "from frappe.utils import get_html_format",
    'frappe.init(site="", sites_path="/home/frappe/frappe-bench/sites")',
    'frappe.set_user("translator@example.com")',
    'with patch("frappe.get_installed_apps", return_value=["erpnext","frappe"]):',
    " print(json.dumps(get_html_format(sys.argv[1])))",
  ].join("\n");
  return JSON.parse(
    execFileSync(
      resolve(framework, "../../env/bin/python"),
      ["-c", code, `${erp}/erpnext/templates/form_grid/item_grid.html`],
      {
        cwd: resolve(framework, "../.."),
        encoding: "utf8",
        env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
      }
    )
  );
}

test("actual Add/Move dialog and request callbacks preserve stock-entry data", (t) => {
  const e = host(t);
  assert.equal(e.frappe.session.user, "translator@example.com");
  for (const add of [false, true]) {
    let callerCallbacks = 0;
    e.context.erpnext.stock.move_item(
      "Rate",
      add ? null : "SOURCE",
      add ? "TARGET" : null,
      10,
      add ? 12.5 : null,
      "Nos",
      () => callerCallbacks++
    );
    const dialog = e.dialogs.at(-1);
    assert.equal(dialog.shown, true);
    assert.equal(dialog.get_field("source").df.hidden, add ? 1 : undefined);
    assert.equal(dialog.get_field("rate").df.hidden, add ? 0 : 1);
    assert.equal(dialog.get_field("target").df.read_only, add ? 1 : undefined);
    assert.equal(
      JSON.stringify(dialog.get_field("target").df.get_query()),
      JSON.stringify({ filters: { is_group: 0 } })
    );
    if (!add) dialog.get_field("target").set_value("TARGET");
    dialog.get_field("qty").set_value(2);
    dialog.primary();
    const request = e.requests.at(-1);
    assert.equal(
      request.method,
      "erpnext.stock.dashboard.item_dashboard.make_stock_entry"
    );
    assert.equal(request.freeze, true);
    assert.equal(
      JSON.stringify(request.args),
      JSON.stringify({
        item_code: "Rate",
        source_warehouse: add ? undefined : "SOURCE",
        target_warehouse: "TARGET",
        qty: 2,
        rate: add ? 12.5 : 0,
      })
    );
    const response = {
      doctype: "Stock Entry",
      name: "Rate",
      items: [{ item_code: "Rate", basic_rate: add ? 12.5 : 0 }],
    };
    request.callback({ message: response });
    assert.deepEqual(e.routes.at(-1), ["Form", "Stock Entry", "Rate"]);
    assert.equal(response.items[0].item_code, "Rate");
    assert.equal(callerCallbacks, 0);
    if (add)
      assert.equal(
        dialog.get_field("rate").df.label,
        "Costo unitario di valorizzazione"
      );
  }
});

test("actual quantity and warehouse guards still prevent invalid requests", (t) => {
  const e = host(t);
  for (const [quantity, target] of [
    [0, "TARGET"],
    [11, "TARGET"],
    [2, "SOURCE"],
  ]) {
    e.context.erpnext.stock.move_item(
      "ITEM-MEMORY",
      "SOURCE",
      null,
      10,
      null,
      "Nos"
    );
    const dialog = e.dialogs.at(-1);
    dialog.get_field("qty").set_value(quantity);
    dialog.get_field("target").set_value(target);
    dialog.primary();
  }
  assert.equal(e.requests.length, 0);
  assert.equal(e.routes.length, 0);
  assert.equal(e.messages.length, 3);
});

test("actual server-expanded item grid and GridRow renderer retain child caption context", (t) => {
  const e = host(t);
  const template = gridTemplate();
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
  for (const [module, doctype] of parents) {
    const controller = readFileSync(
      `${erp}/erpnext/${module}/doctype/${doctype}/${doctype}.py`,
      "utf8"
    );
    assert.match(
      controller,
      /form_grid_templates\s*=\s*\{"items": "templates\/form_grid\/item_grid.html"\}/
    );
    const metadata = JSON.parse(
      readFileSync(
        `${erp}/erpnext/${module}/doctype/${doctype}/${doctype}.json`,
        "utf8"
      )
    );
    const df = metadata.fields.find((field) => field.fieldname === "items");
    const frm = {
      doc: { doctype: metadata.name, docstatus: 0, per_delivered: 0 },
      perm: [],
    };
    const row = {
      frm,
      grid: { doctype: df.options, template },
      row: e.$("<div>"),
      set_row_index() {},
      row_check_html: "",
      get_visible_columns: () => [],
    };
    e.row.render_template.call(row);
    const header = row.row_display
      .find(".hidden-xs.text-right")
      .last()
      .text()
      .trim();
    const item = {
      doctype: df.options,
      idx: 1,
      item_code: '<Item "Rate">',
      item_name: '<Item "Rate">',
      qty: 2,
      rate: 12.5,
      amount: 25,
      uom: "Nos",
      warehouse: null,
      get_formatted: (name) =>
        ({ qty: "2", rate: "12,50 €", amount: "25,00 €" }[name]),
    };
    const snapshot = JSON.stringify(item);
    row.doc = item;
    e.frappe.perm = { is_visible: () => true };
    e.row.render_template.call(row);
    assert.equal(row.row_display.find("strong").text(), '<Item "Rate">');
    assert.equal(row.row_display.find("script").length, 0);
    assert.match(row.row_display.text(), /12,50 €/);
    assert.match(row.row_display.text(), /25,00 €/);
    assert.equal(JSON.stringify(item), snapshot);
    e.frappe.perm.is_visible = () => false;
    e.row.render_template.call(row);
    assert.doesNotMatch(row.row_display.text(), /12,50 €|25,00 €/);
    assert.equal(
      header,
      doctype === "bom" ? "Costo unitario" : "Prezzo unitario"
    );
  }
});

test("unknown child contexts and English fallback retain prior generic grid caption", (t) => {
  const e = host(t);
  const template = gridTemplate();
  for (const child of [undefined, "Supplied Child"]) {
    const row = {
      frm: { doc: {} },
      grid: { doctype: child, template },
      row: e.$("<div>"),
      set_row_index() {},
      row_check_html: "",
      get_visible_columns: () => [],
    };
    e.row.render_template.call(row);
    assert.equal(
      row.row_display.find(".hidden-xs.text-right").last().text().trim(),
      "Prezzo unitario"
    );
    e.frappe._messages = {};
    e.row.render_template.call(row);
    assert.equal(
      row.row_display.find(".hidden-xs.text-right").last().text().trim(),
      "Rate"
    );
    e.frappe._messages = { Rate: "Prezzo unitario" };
  }
});
