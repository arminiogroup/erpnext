import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

function load(messages = { Set: "Imposta", "Set:UOM": "Insieme" }) {
  const routes = [];
  const sandbox = {
    frappe: {
      treeview_settings: {},
      _messages: messages,
      utils: {
        escape_html: (value) =>
          String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;"),
      },
      set_route: (...args) => routes.push(args),
    },
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  for (const relative of [
    "../../../../../frappe/frappe/public/js/frappe/translate.js",
    "./bom_tree.js",
  ]) {
    vm.runInContext(
      readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8"),
      context
    );
  }
  return { ...sandbox, context, routes };
}

test("BOM node translates its unit in UOM context without changing item or quantity data", () => {
  const env = load();
  const node = {
    is_root: false,
    data: {
      value: "BOM-RAW",
      item_code: "ITEM-RAW",
      item_name: "Custom item",
      qty: 2.5,
      stock_uom: "Set",
    },
  };
  const original = JSON.stringify(node);
  const html = env.frappe.treeview_settings.BOM.get_label(node);
  assert.equal(
    html,
    'ITEM-RAW: Custom item <span class="badge badge-pill badge-light">2.5 Insieme</span>'
  );
  assert.equal(JSON.stringify(node), original);
  env.frappe.treeview_settings.BOM.toolbar[0].click(node);
  assert.deepEqual(env.routes[0], ["Form", "BOM", "BOM-RAW"]);
});

test("BOM root uses its actual document UOM and keeps its canonical root identity", () => {
  const env = load({ Minute: "Verbale", "Minute:UOM": "Minuto" });
  const bom = {
    name: "BOM-RAW",
    item: "ITEM-RAW",
    item_name: "Custom item",
    quantity: 1,
    uom: "Minute",
  };
  env.frappe.model = {
    with_doc(doctype, name, callback) {
      assert.equal(doctype, "BOM");
      assert.equal(name, "BOM-RAW");
      callback();
    },
    get_doc: () => bom,
  };
  const node = { is_root: true, data: { value: "BOM-RAW" } };
  assert.equal(
    env.frappe.treeview_settings.BOM.get_label(node),
    'BOM-RAW: Custom item <span class="badge badge-pill badge-light">1 Minuto</span>'
  );
  assert.equal(node.data.value, "BOM-RAW");
  assert.equal(node.data.item_code, "ITEM-RAW");
  assert.equal(node.data.stock_uom, "Minute");
  assert.equal(bom.uom, "Minute");
});

test("BOM unit display keeps HTML escaping and global/source fallback", () => {
  const env = load({
    Unsafe: "Wrong",
    "Unsafe:UOM": "<Unità & insieme>",
    Global: "Globale",
  });
  const node = {
    data: {
      value: "BOM-RAW",
      item_code: "<ITEM>",
      item_name: "<Name>",
      qty: 3,
      stock_uom: "Unsafe",
    },
  };
  const html = env.frappe.treeview_settings.BOM.get_label(node);
  assert.equal(
    html,
    '&lt;ITEM&gt;: &lt;Name&gt; <span class="badge badge-pill badge-light">3 &lt;Unità &amp; insieme&gt;</span>'
  );
  for (const [unit, expected] of [
    ["Global", "Globale"],
    ["Custom unit", "Custom unit"],
  ]) {
    node.data.stock_uom = unit;
    assert.match(
      env.frappe.treeview_settings.BOM.get_label(node),
      new RegExp(`3 ${expected}</span>`)
    );
    assert.equal(node.data.stock_uom, unit);
  }
  node.data.qty = 0;
  assert.equal(env.frappe.treeview_settings.BOM.get_label(node), "<ITEM>");
});
