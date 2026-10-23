import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const { JSDOM } = require("jsdom");
const { transformSync } = require("esbuild");
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const framework = resolve(root, "../frappe");
const plain = (value) => JSON.parse(JSON.stringify(value));
const reportPath =
  "erpnext/buying/report/supplier_quotation_comparison/supplier_quotation_comparison.js";
const read = (path) => readFileSync(resolve(framework, path), "utf8");
const variants = [
  {
    name: "English",
    messages: {},
    supplier: "Categorize by Supplier",
    item: "Categorize by Item",
  },
  {
    name: "current Italian caption",
    messages: {
      "Categorize by": "Raggruppa per",
      "Categorize by Supplier": "Categorizza per fornitore",
      "Categorize by Item": "Raggruppa per articolo",
    },
    supplier: "Categorizza per fornitore",
    item: "Raggruppa per articolo",
  },
  {
    name: "reviewed Italian caption",
    messages: {
      "Categorize by": "Raggruppa per",
      "Categorize by Supplier": "Raggruppa per fornitore",
      "Categorize by Item": "Raggruppa per articolo",
    },
    supplier: "Raggruppa per fornitore",
    item: "Raggruppa per articolo",
  },
  {
    name: "custom caption",
    messages: {
      "Categorize by Supplier": "Scelta del fornitore personalizzata",
    },
    supplier: "Scelta del fornitore personalizzata",
    item: "Categorize by Item",
  },
  {
    name: "unrelated dictionary fallback",
    messages: { Unrelated: "Altro" },
    supplier: "Categorize by Supplier",
    item: "Categorize by Item",
  },
];

function environment(messages) {
  const dom = new JSDOM(
    '<!doctype html><body><div class="page-form"></div></body>',
    { url: "http://localhost/", runScripts: "outside-only" }
  );
  const context = dom.getInternalVMContext();
  const window = dom.window;
  vm.runInContext(readFileSync(require.resolve("jquery"), "utf8"), context);
  const $ = window.jQuery;
  $.fn.tooltip = function () {
    return this;
  };
  Object.assign(window, {
    moment: require("moment-timezone"),
    cint: (value) => Number.parseInt(value || 0, 10),
    cstr: (value) => String(value ?? ""),
    flt: (value) => Number(value || 0),
    is_null: (value) => value == null,
    strip_html: (value) => String(value ?? "").replace(/<[^>]*>/g, ""),
    locals: {},
    DataTable: class {},
  });
  window.frappe = {
    provide(path) {
      let target = window;
      for (const part of path.split(".")) target = target[part] ??= {};
    },
    _messages: { ...messages },
    ui: { form: {} },
    views: { BaseList: class {} },
    form: { formatters: {} },
    standard_pages: {},
    query_reports: {},
    model: {
      is_value_type: () => true,
      can_write: () => false,
      is_numeric_field: () => false,
      no_value_type: [],
      table_fields: [],
      get_value: () => undefined,
    },
    perm: { has_perm: (_doctype, level) => !level },
    boot: {
      treeviews: [],
      user: { language: "en" },
      sysdefaults: { date_format: "yyyy-mm-dd" },
      time_zone: { system: "UTC", user: "UTC" },
    },
    sys_defaults: { date_format: "yyyy-mm-dd", time_zone: "UTC" },
    get_meta: () => ({ fields: [], autoname: "hash" }),
    is_mobile: () => false,
    utils: { icon: () => "<svg></svg>" },
    meta: { docfield_map: {}, docfield_copy: {} },
    defaults: {
      is_enabled: () => false,
      get_user_default: () => "Azienda <raw>",
    },
    session: { user: "translator@example.com" },
    call() {
      throw new Error("Unexpected network");
    },
    db: new Proxy(
      {},
      {
        get() {
          throw new Error("Unexpected database access");
        },
      }
    ),
  };
  vm.runInContext(
    transformSync(
      read("frappe/public/js/frappe/utils/select_option_context.ts"),
      { loader: "ts", format: "iife", globalName: "SelectContexts" }
    ).code,
    context
  );
  window.selectOptionContext = window.SelectContexts.selectOptionContext;
  for (const path of [
    "utils/utils.js",
    "dom.js",
    "format.js",
    "translate.js",
    "utils/datetime.js",
    "form/formatters.js",
    "form/controls/base_control.js",
    "form/controls/base_input.js",
    "form/controls/data.js",
    "form/controls/select.js",
    "form/controls/control.js",
    "ui/page.js",
    "views/reports/query_report.js",
  ]) {
    vm.runInContext(
      read("frappe/public/js/frappe/" + path).replace(/^import .*;\n/gm, ""),
      context,
      { filename: path }
    );
  }
  vm.runInContext(readFileSync(resolve(root, reportPath), "utf8"), context, {
    filename: reportPath,
  });
  const settings = window.frappe.query_reports["Supplier Quotation Comparison"];
  const original = settings.filters;
  const field = original.find((row) => row.fieldname === "categorize_by");
  const page = Object.create(window.frappe.ui.Page.prototype);
  page.page_form = $(".page-form");
  page.fields_dict = {};
  const report = Object.create(window.frappe.views.QueryReport.prototype);
  Object.assign(report, { page, report_settings: { filters: [field] } });
  report.setup_filters();
  return { window, report, field, settings, close: () => window.close() };
}

for (const variant of variants) {
  test(`${variant.name}: localized option labels retain a canonical supplier default`, () => {
    const env = environment(variant.messages);
    try {
      const { report, field } = env;
      const control = report.filters[0];
      assert.equal(field.default, "Categorize by Supplier");
      assert.equal(control.$input.val(), "Categorize by Supplier");
      assert.deepEqual(plain(report.get_filter_values()), {
        categorize_by: "Categorize by Supplier",
      });
      assert.deepEqual(plain(field.options), [
        { label: variant.supplier, value: "Categorize by Supplier" },
        { label: variant.item, value: "Categorize by Item" },
      ]);
      assert.deepEqual(
        plain(
          control.$input
            .find("option")
            .map((_index, option) => option.value)
            .get()
        ),
        ["Categorize by Supplier", "Categorize by Item"]
      );
      assert.equal(control.$input.find(":selected").text(), variant.supplier);
      for (const value of ["Categorize by Item", "Categorize by Supplier"]) {
        control.set_input(value);
        assert.deepEqual(plain(report.get_filter_values()), {
          categorize_by: value,
        });
      }
      control.set_input("not an option");
      assert.deepEqual(plain(report.get_filter_values()), {});
      control.set_input(field.default);
      assert.deepEqual(plain(report.get_filter_values()), {
        categorize_by: "Categorize by Supplier",
      });
    } finally {
      env.close();
    }
  });
}

test("other report filters and backend-facing values remain unchanged", () => {
  const env = environment({});
  try {
    const filters = env.settings.filters;
    assert.deepEqual(plain(filters.map((field) => field.fieldname)), [
      "company",
      "from_date",
      "to_date",
      "item_code",
      "supplier",
      "supplier_quotation",
      "request_for_quotation",
      "categorize_by",
      "status",
      "order_status",
      "include_expired",
    ]);
    assert.equal(
      filters.find((field) => field.fieldname === "company").default,
      "Azienda <raw>"
    );
    assert.equal(
      filters.find((field) => field.fieldname === "status").default,
      "Submitted"
    );
    assert.equal(
      filters.find((field) => field.fieldname === "include_expired").default,
      0
    );
    for (const name of ["item_code", "request_for_quotation"])
      assert.equal(
        typeof filters.find((field) => field.fieldname === name).get_query,
        "function"
      );
    for (const name of ["supplier", "supplier_quotation"])
      assert.equal(
        typeof filters.find((field) => field.fieldname === name).get_data,
        "function"
      );
    assert.equal(typeof env.settings.formatter, "function");
    assert.equal(typeof env.settings.make_default_supplier_dialog, "function");
  } finally {
    env.close();
  }
});

test("actual backend preserves legacy filters, modern precedence, raw rows and grouping", () => {
  const program = String.raw`
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import frappe
frappe.init(site="", sites_path="/home/frappe/frappe-bench/sites")
frappe.local.session=frappe._dict(user="translator@example.com",data=frappe._dict())
frappe.local.lang="en"
from erpnext.buying.report.supplier_quotation_comparison import supplier_quotation_comparison as report
cases=[({"group_by":"Group by Supplier"},"Categorize by Supplier"),({"group_by":"Group by Item"},"Categorize by Item"),({"categorize_by":"Categorize by Supplier"},"Categorize by Supplier"),({"categorize_by":"Categorize by Item"},"Categorize by Item"),({"group_by":"Group by Supplier","categorize_by":"Categorize by Item"},"Categorize by Item"),({"categorize_by":"Custom raw value"},"Custom raw value"),({},None)]
rows=[frappe._dict(parent="QUOTE-RAW",item_code="ARTICOLO-RAW",supplier_name="Fornitore <raw>",qty=2,stock_qty=2,amount=20,rate=10,conversion_factor=1,base_amount=20,base_rate=10,supplier_quotation_status="Draft",currency="EUR",price_list_currency="EUR",uom="Nos",stock_uom="Nos")]
original=deepcopy(rows)
frappe.local.db=SimpleNamespace(get_default=lambda key:"2",close=lambda:None)
results=[]
try:
 with patch.object(frappe,"get_cached_value",return_value="EUR"),patch.object(frappe,"get_system_settings",return_value=None),patch.object(frappe.cache,"hget",return_value={}):
  for values,expected in cases:
   filters=frappe._dict(company="Azienda <raw>",**values)
   report.validate_filters(filters)
   assert filters.get("categorize_by")==expected
   assert all(filters.get(key)==value for key,value in values.items())
   columns=report.get_columns(filters)
   assert columns[0]["fieldname"]==("item_code" if expected=="Categorize by Item" else "supplier_name")
   out,chart=report.prepare_data(rows,filters)
   assert chart==[] and len(out)==1
   assert out[0]["item_code"]=="ARTICOLO-RAW" and out[0]["supplier_name"]=="Fornitore <raw>"
   assert out[0]["uom"]=="Nos" and out[0]["base_amount"]==20 and out[0]["base_price_per_unit"]==10
   assert bool(out[0].get("min"))==(expected!="Categorize by Supplier")
   results.append(dict(filters=filters,first_column=columns[0]["fieldname"]))
 assert rows==original
 print(json.dumps(results))
finally:
 frappe.destroy()
`;
  const result = spawnSync(
    resolve(root, "../../env/bin/python"),
    ["-c", program],
    { encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } }
  );
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).length, 7);
});
