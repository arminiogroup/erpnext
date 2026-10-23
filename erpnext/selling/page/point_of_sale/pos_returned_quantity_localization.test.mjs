// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SOURCE = "Returned quantity: <strong>{0}</strong>";
const ITALIAN = "Quantità resa: <strong>{0}</strong>";
const plain = (value) => JSON.parse(JSON.stringify(value));

function environment(messages, qty) {
  const calls = [];
  const scope = {
    $: {},
    erpnext: { PointOfSale: {} },
    format_currency: (value, currency) => `${currency}:${value}`,
    frappe: {
      _messages: messages,
      utils: {
        escape_html: (value) =>
          String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;"),
      },
      call: async (request) => {
        calls.push(plain(request));
        return { message: { qty } };
      },
    },
  };
  scope.window = scope;
  scope.jQuery = scope.$;
  const context = vm.createContext(scope);
  for (const [app, path] of [
    ["frappe", "frappe/public/js/frappe/format.js"],
    ["frappe", "frappe/public/js/frappe/translate.js"],
    ["erpnext", "erpnext/selling/page/point_of_sale/pos_past_order_summary.js"],
  ]) {
    const base = new URL(
      app === "frappe" ? "../../../../../frappe/" : "../../../../",
      import.meta.url
    );
    const url = new URL(path, base);
    vm.runInContext(fs.readFileSync(url, "utf8"), context, {
      filename: url.pathname,
    });
  }
  return {
    method: scope.erpnext.PointOfSale.PastOrderSummary.prototype.get_item_html,
    calls,
  };
}

function inputs(doctype = "POS Invoice", extra = {}) {
  return [
    {
      doctype,
      name: "INV-RAW",
      customer: "CUSTOMER-RAW",
      currency: "EUR",
      is_return: 0,
      docstatus: 1,
      ...extra,
    },
    { name: "ROW-RAW", item_name: "Raw <name>", qty: 5, uom: "Nos", rate: 10 },
  ];
}

for (const [name, messages, expected] of [
  ["Italian", { [SOURCE]: ITALIAN, Returned: "Reso" }, ITALIAN],
  ["English fallback", {}, SOURCE],
  ["empty-target fallback", { [SOURCE]: "", Returned: "Reso" }, SOURCE],
]) {
  test(`${name} whole quantity frame handles singular, plural and fractional returned quantities`, async () => {
    for (const qty of [1, 2, 1.5]) {
      const env = environment(messages, qty);
      const [doc, item] = inputs();
      const before = plain([doc, item]);
      const html = await env.method.call({}, doc, item);
      assert.ok(html.includes(expected.replace("{0}", qty)));
      assert.equal((html.match(/item-row-refund/g) || []).length, 1);
      assert.ok(html.includes("Raw &lt;name&gt;"));
      assert.ok(html.includes('class="item-rate">EUR:10'));
      assert.deepEqual([doc, item], before);
    }
  });
}

test("raw invoice type and identifiers reach the existing RPC unchanged", async () => {
  for (const doctype of ["POS Invoice", "Sales Invoice"]) {
    const env = environment({ [SOURCE]: ITALIAN }, 2);
    const [doc, item] = inputs(doctype);
    await env.method.call({}, doc, item);
    assert.deepEqual(env.calls, [
      {
        method:
          "erpnext.controllers.sales_and_purchase_return.get_invoice_item_returned_qty",
        args: {
          doctype,
          invoice: "INV-RAW",
          customer: "CUSTOMER-RAW",
          item_row_name: "ROW-RAW",
        },
      },
    ]);
  }
});

test("zero and missing aggregates omit the caption and keep raw data untouched", async () => {
  for (const qty of [0, null, undefined]) {
    const env = environment({ [SOURCE]: ITALIAN }, qty);
    const [doc, item] = inputs();
    const before = plain([doc, item]);
    const html = await env.method.call({}, doc, item);
    assert.ok(!html.includes("item-row-refund"));
    assert.equal(env.calls.length, 1);
    assert.deepEqual([doc, item], before);
  }
});

test("return and draft documents preserve both skip guards without an RPC", async () => {
  for (const extra of [{ is_return: 1 }, { docstatus: 0 }]) {
    const env = environment({ [SOURCE]: ITALIAN }, 2);
    const [doc, item] = inputs("POS Invoice", extra);
    const before = plain([doc, item]);
    const html = await env.method.call({}, doc, item);
    assert.ok(!html.includes("item-row-refund"));
    assert.deepEqual(env.calls, []);
    assert.deepEqual([doc, item], before);
  }
});
