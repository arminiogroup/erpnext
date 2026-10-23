import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const frappeRoot = new URL(
  "../../../../../frappe/frappe/public/js/frappe/",
  import.meta.url
);
const frame = "{0} will be set as the {1} in subsequently scanned items";
const messages = {
  [frame]:
    "Negli articoli scansionati successivamente, il campo {1} sarà impostato su {0}.",
  Company: "Azienda",
  Warehouse: "Magazzino",
  "Target Warehouse:Stock Entry Detail": "Magazzino di destinazione",
  "Source Warehouse:Stock Entry Detail": "Magazzino di origine",
};

function environment(dictionary = messages, options = {}) {
  const alerts = [];
  const requests = [];
  const properties = [];
  const events = [];
  const inputs = [];
  const refreshes = [];
  const sounds = [];
  const scope = {
    erpnext: { utils: {} },
    locals: {},
    refresh_field: (field) => refreshes.push(field),
  };
  scope.window = scope;
  scope.$ = { extend: Object.assign };
  scope.jQuery = scope.$;
  scope.frappe = {
    _messages: dictionary,
    provide: (path) =>
      path.split(".").reduce((object, key) => (object[key] ||= {}), scope),
    show_alert: (message, duration) => alerts.push({ ...message, duration }),
    session: { user: "translator@example.com" },
    call: (request) => {
      requests.push(JSON.parse(JSON.stringify(request)));
      return Promise.resolve({
        message: { warehouse: options.warehouse ?? "Company" },
      });
    },
  };
  const context = vm.createContext(scope);
  for (const file of [
    "utils/datatype.js",
    "format.js",
    "translate.js",
    "model/meta.js",
  ]) {
    vm.runInContext(
      fs.readFileSync(new URL(file, frappeRoot), "utf8"),
      context,
      { filename: file }
    );
  }
  const utils = fs.readFileSync(new URL("utils/utils.js", frappeRoot), "utf8");
  const imports = [
    'import deep_equal from "fast-deep-equal";',
    'import number_systems from "./number_systems";',
    'import { CHART_PALETTE } from "../ui/components/utils.js";',
  ];
  let loadedUtils = utils;
  for (const declaration of imports) {
    assert.equal(loadedUtils.split(declaration).length, 2);
    loadedUtils = loadedUtils.replace(declaration, "");
  }
  vm.runInContext(loadedUtils, context, { filename: "utils/utils.js" });
  scope.frappe.utils.play_sound = (sound) => sounds.push(sound);
  scope.frappe.meta.docfield_map["Stock Entry"] = options.withoutLastScanned
    ? {}
    : { last_scanned_warehouse: { fieldtype: "Link" } };
  scope.frappe.meta.docfield_map["Stock Entry Detail"] = {
    t_warehouse: { label: "Target Warehouse", fieldtype: "Link" },
    s_warehouse: { label: "Source Warehouse", fieldtype: "Link" },
  };
  const doc = {
    doctype: "Stock Entry",
    name: "STE-EXAMPLE",
    purpose: options.purpose ?? "Material Receipt",
    company: "COMPANY-EXAMPLE",
    set_warehouse: "DEFAULT-EXAMPLE",
    item_code: "ITEM-EXAMPLE",
    items: [{ name: "ROW-EXAMPLE", qty: 2.5, item_code: "ITEM-EXAMPLE" }],
  };
  const field = {
    value: "WAREHOUSE-CODE",
    set_value: (value) => {
      inputs.push(value);
      field.value = value;
    },
  };
  const frm = {
    doctype: doc.doctype,
    doc,
    fields_dict: {
      scan_barcode: field,
      items: { grid: { doctype: "Stock Entry Detail" } },
    },
    set_value: (key, value) => (doc[key] = value),
    set_df_property: (...args) => properties.push(args),
    $wrapper: { on: (...args) => events.push(args) },
  };
  vm.runInContext(
    fs.readFileSync(new URL("barcode_scanner.js", import.meta.url), "utf8"),
    context
  );
  const selector =
    options.selector ??
    ((row) =>
      row.purpose === "Material Receipt" ? "t_warehouse" : "s_warehouse");
  const scanner = new scope.erpnext.utils.BarcodeScanner({
    frm,
    warehouse_field: selector,
    play_success_sound: "success",
    play_fail_sound: "fail",
  });
  return {
    scope,
    doc,
    scanner,
    field,
    alerts,
    requests,
    properties,
    events,
    inputs,
    refreshes,
    sounds,
  };
}

function unchanged(e, before) {
  const expected = {
    ...before,
    last_scanned_warehouse: e.doc.last_scanned_warehouse,
  };
  assert.deepEqual(e.doc, expected);
  assert.deepEqual(e.requests, [
    {
      method: "erpnext.stock.utils.scan_barcode",
      args: {
        search_value: "WAREHOUSE-CODE",
        item_code: "ITEM-EXAMPLE",
        ctx: { set_warehouse: "DEFAULT-EXAMPLE", company: "COMPANY-EXAMPLE" },
      },
    },
  ]);
  assert.deepEqual(e.inputs, [""]);
  assert.deepEqual(e.sounds, ["success"]);
  assert.deepEqual(e.refreshes, []);
}

for (const warehouse of ["Company", "Warehouse", 'Caffè & "Deposito" <tag>']) {
  test(`actual scan preserves warehouse business identity: ${warehouse}`, async () => {
    const e = environment(messages, { warehouse });
    const before = structuredClone(e.doc);
    await e.scanner.process_scan();
    assert.equal(e.doc.last_scanned_warehouse, warehouse);
    assert.equal(
      e.alerts[0].message,
      `Negli articoli scansionati successivamente, il campo <b>Magazzino di destinazione</b> sarà impostato su ${e.scope.frappe.utils.bold(
        warehouse
      )}.`
    );
    assert.equal(e.alerts[0].indicator, "green");
    assert.equal(e.alerts[0].duration, 6);
    if (warehouse.includes("<")) {
      assert(e.alerts[0].message.includes("&lt;tag&gt;"));
      assert(e.alerts[0].message.includes("&amp;"));
      assert(!e.alerts[0].message.includes("<tag>"));
    }
    unchanged(e, before);
  });
}

for (const [provider, warehouse] of [
  [{}, "Unknown depot"],
  [{ [frame]: "" }, "Company"],
]) {
  test(`English and empty provider fallback preserves raw identity: ${warehouse}`, async () => {
    const e = environment(provider, { warehouse });
    await e.scanner.process_scan();
    assert.equal(
      e.alerts[0].message,
      `<strong>${warehouse}</strong> will be set as the <b>Target Warehouse</b> in subsequently scanned items`
    );
    assert.equal(e.doc.last_scanned_warehouse, warehouse);
  });
}

test("string and callback selectors preserve source warehouse context and payload", async () => {
  for (const selector of [
    "s_warehouse",
    (doc) => (doc.purpose === "Material Issue" ? "s_warehouse" : "t_warehouse"),
  ]) {
    const e = environment(messages, {
      selector,
      purpose: "Material Issue",
      warehouse: "Company",
    });
    const before = structuredClone(e.doc);
    await e.scanner.process_scan();
    assert(e.alerts[0].message.includes("<b>Magazzino di origine</b>"));
    assert(e.alerts[0].message.includes("<strong>Company</strong>"));
    unchanged(e, before);
  }
});

test("repeat scans initialize once and actual clear handler resets only context", async () => {
  const e = environment(messages, { warehouse: "Company" });
  await e.scanner.process_scan();
  e.field.value = "WAREHOUSE-CODE";
  await e.scanner.process_scan();
  assert.equal(e.properties.length, 3);
  assert.deepEqual(e.properties.slice(0, 2), [
    ["last_scanned_warehouse", "options", "Warehouse"],
    ["last_scanned_warehouse", "fieldtype", "Link"],
  ]);
  assert.equal(e.properties[2][0], "last_scanned_warehouse");
  assert.equal(e.properties[2][1], "formatter");
  assert.equal(typeof e.properties[2][2], "function");
  assert.equal(e.events.length, 1);
  assert.equal(e.events[0][0], "click");
  assert.equal(e.events[0][1], ".btn-clear-last-scanned-warehouse");
  const before = structuredClone(e.doc);
  const calls = [];
  e.events[0][2]({
    preventDefault: () => calls.push("prevent"),
    stopPropagation: () => calls.push("stop"),
  });
  assert.deepEqual(calls, ["prevent", "stop"]);
  assert.deepEqual(e.doc, { ...before, last_scanned_warehouse: null });
  assert.equal(e.requests.length, 2);
  assert.equal(e.alerts.at(-1).indicator, "blue");
});

test("missing last-scanned metadata rejects warehouse scan without setting context", async () => {
  const e = environment(messages, { withoutLastScanned: true });
  const before = structuredClone(e.doc);
  await assert.rejects(e.scanner.process_scan());
  assert.deepEqual(e.doc, before);
  assert.deepEqual(e.properties, []);
  assert.deepEqual(e.events, []);
  assert.deepEqual(e.inputs, ["", ""]);
  assert.deepEqual(e.refreshes, ["items"]);
  assert.deepEqual(e.sounds, ["fail"]);
  assert.equal(e.alerts[0].indicator, "red");
});
