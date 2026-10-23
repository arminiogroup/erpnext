// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SOURCE =
  "To skip pricing rules for a single transaction, enable that transaction's Ignore Pricing Rule option, where available.";
const ITALIAN =
  "Per non applicare le regole di prezzo a una singola transazione, attiva l'opzione «Ignora le regole di prezzo» nella transazione, se disponibile.";
const plain = (value) => JSON.parse(JSON.stringify(value));

function environment(messages) {
  let handlers;
  const fieldOptions = [];
  const scope = {
    erpnext: { taxes_and_totals: class {} },
    $: {
      merge(target, values) {
        target.push(...values);
        return target;
      },
      each(values, callback) {
        values.forEach((value, index) => callback(index, value));
      },
    },
    cint: (value) => Number.parseInt(value, 10) || 0,
    in_list: (values, value) => values.includes(value),
    set_field_options: (...args) => fieldOptions.push(plain(args)),
    frappe: {
      _messages: messages,
      provide() {},
      ui: {
        form: {
          on(name, methods) {
            assert.equal(name, "Pricing Rule");
            handlers = methods;
          },
        },
      },
    },
  };
  scope.window = scope;
  scope.jQuery = scope.$;
  const context = vm.createContext(scope);
  for (const [app, path] of [
    ["frappe", "frappe/public/js/frappe/format.js"],
    ["frappe", "frappe/public/js/frappe/translate.js"],
    ["erpnext", "erpnext/public/js/controllers/transaction.js"],
    ["erpnext", "erpnext/accounts/doctype/pricing_rule/pricing_rule.js"],
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
  return { scope, handlers, fieldOptions };
}

for (const [language, messages, expected] of [
  ["Italian", { [SOURCE]: ITALIAN }, ITALIAN],
  ["English fallback", {}, SOURCE],
]) {
  test(`${language} actual help refresh directs one transaction to its available ignore option`, () => {
    const env = environment(messages);
    const doc = {
      selling: 0,
      buying: 1,
      applicable_for: "Supplier",
      apply_on: "Item Code",
      disable: 0,
    };
    const before = plain(doc);
    let html;
    const required = [];
    const frm = {
      doc,
      events: env.handlers,
      get_field(name) {
        assert.equal(name, "pricing_rule_help");
        return {
          html(value) {
            html = value;
          },
        };
      },
      trigger(name) {
        env.handlers[name](frm);
      },
      toggle_reqd: (...args) => required.push(args),
      set_value(name, value) {
        doc[name] = value;
      },
    };
    env.handlers.refresh(frm);
    assert.ok(
      html.includes(`<li>\n\t\t\t\t\t\t\t${expected}\n\t\t\t\t\t\t</li>`)
    );
    assert.deepEqual(doc, before);
    assert.deepEqual(env.fieldOptions, [
      ["applicable_for", "\nSupplier\nSupplier Group"],
    ]);
    assert.deepEqual(required, [
      ["items", 1],
      ["item_groups", 0],
      ["brands", 0],
    ]);
    assert.ok(
      html.includes("Pricing Rules are further filtered based on quantity.")
    );
  });
}

test("actual transaction serialization preserves raw ignore flags and both order types", () => {
  const env = environment({ [SOURCE]: "" });
  const controller = env.scope.erpnext.TransactionController.prototype;
  for (const doctype of ["Sales Order", "Purchase Order"]) {
    for (const ignore_pricing_rule of [0, 1]) {
      const doc = {
        doctype,
        name: "ORDER-RAW",
        items: [],
        ignore_pricing_rule,
      };
      const before = plain(doc);
      const args = controller._get_args.call({
        frm: { doc },
        _get_item_list: controller._get_item_list,
      });
      assert.equal(args.ignore_pricing_rule, ignore_pricing_rule);
      assert.equal(args.doctype, doctype);
      assert.equal(args.name, "ORDER-RAW");
      assert.deepEqual(doc, before);
    }
  }
});

test("actual ignore handler requests removal of existing row effects without mutating rule documents", () => {
  const env = environment({});
  for (const doctype of ["Sales Order", "Purchase Order"]) {
    const row = {
      doctype: `${doctype} Item`,
      name: "ROW-RAW",
      item_code: "ITEM-RAW",
      pricing_rules: '["RULE-RAW"]',
      parenttype: doctype,
      parent: "ORDER-RAW",
      price_list_rate: 100,
    };
    const frm = {
      doc: { doctype, name: "ORDER-RAW", ignore_pricing_rule: 1, items: [row] },
    };
    const pending = [];
    frm.call = (request) => pending.push(request);
    const before = plain(frm.doc);
    env.scope.erpnext.TransactionController.prototype.ignore_pricing_rule.call({
      frm,
    });
    assert.equal(pending.length, 1);
    assert.equal(
      pending[0].method,
      "erpnext.accounts.doctype.pricing_rule.pricing_rule.remove_pricing_rules"
    );
    assert.deepEqual(plain(pending[0].args.item_list), [row]);
    assert.equal(typeof pending[0].callback, "function");
    assert.deepEqual(frm.doc, before);
  }
});
