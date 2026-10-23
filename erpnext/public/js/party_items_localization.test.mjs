// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const contextual = {
  "1 item:Party Specific Item Count": "1 articolo",
  "{0} items:Party Specific Item Count": "{0} articoli",
};
const generic = {
  "1 item": "1 articolo",
  "{0} items": "{0} elementi",
  "No items": "Nessun articolo",
};
const plain = (value) => JSON.parse(JSON.stringify(value));

function environment(messages = { ...generic, ...contextual }) {
  const routes = [];
  const jquery = {};
  const forbidden = () => {
    throw new Error("Unexpected business, network or UI action");
  };
  const scope = {
    jQuery: jquery,
    $: jquery,
    erpnext: {},
    frappe: {
      _messages: { ...messages },
      model: {
        can_read: () => true,
        can_create: () => false,
        can_delete: () => false,
      },
      set_route: (...args) => routes.push(args),
      require: forbidden,
      xcall: forbidden,
      db: { insert: forbidden, delete_doc: forbidden },
      ui: { Dialog: forbidden, EmbeddedList: forbidden },
    },
  };
  scope.window = scope;
  const context = vm.createContext(scope);
  for (const path of [
    "../../../../frappe/frappe/public/js/frappe/format.js",
    "../../../../frappe/frappe/public/js/frappe/translate.js",
    "./party_items.js",
  ]) {
    const url = new URL(path, import.meta.url);
    vm.runInContext(fs.readFileSync(url, "utf8"), context, {
      filename: url.pathname,
    });
  }
  function create(party = "Customer") {
    const view = new scope.erpnext.PartyItems(
      { doc: Object.freeze({ name: "PARTY-RAW" }) },
      {
        party_type: party,
        order_doctype: party === "Customer" ? "Sales Order" : "Purchase Order",
        labels: {},
      }
    );
    view.party = "PARTY-RAW";
    return view;
  }
  return { scope, routes, create };
}

test("the singular item caption owns its context over a colliding generic target", () => {
  const env = environment({
    ...generic,
    ...contextual,
    "1 item": "1 elemento",
  });
  assert.equal(env.create().count_label(1), "1 articolo");
  assert.equal(env.scope.frappe._("1 item"), "1 elemento");
});

test("zero and plural item counts use articoli while the generic plural stays elementi", () => {
  const env = environment();
  for (const count of [2, 0, 25]) {
    assert.equal(env.create().count_label(count), `${count} articoli`);
    assert.equal(env.scope.frappe._("{0} items", [count]), `${count} elementi`);
  }
});

test("the actual Covers renderer uses the same count captions and preserves its empty branches", () => {
  const env = environment();
  const view = env.create();
  const covers = view.rule_columns()[2];
  const single = Object.freeze({ restrict_based_on: "Item", item_count: 1 });
  const group = Object.freeze({
    restrict_based_on: "Item Group",
    item_count: 2,
  });
  assert.equal(covers.render({ item_count: null }), "");
  assert.equal(
    covers.render({ item_count: 0 }),
    '<span class="text-muted">Nessun articolo</span>'
  );
  assert.equal(
    covers.render(single),
    '<span class="text-muted">1 articolo</span>'
  );
  assert.equal(covers.render(group), '<a href="#">2 articoli</a>');
  assert.deepEqual(plain(single), { restrict_based_on: "Item", item_count: 1 });
  assert.deepEqual(plain(group), {
    restrict_based_on: "Item Group",
    item_count: 2,
  });
});

test("missing and empty contextual entries preserve the real translator's global and English fallbacks", () => {
  for (const messages of [
    {},
    generic,
    {
      ...generic,
      ...Object.fromEntries(Object.keys(contextual).map((key) => [key, ""])),
    },
  ]) {
    const env = environment(messages);
    for (const count of [0, 1, 2, 25]) {
      const expected =
        count === 1
          ? messages["1 item"] || "1 item"
          : `${count} ${messages["{0} items"] ? "elementi" : "items"}`;
      assert.equal(env.create().count_label(count), expected);
    }
  }
});

test("Item routes, restriction filters and party identifiers remain canonical", () => {
  const env = environment();
  for (const party of ["Customer", "Supplier"]) {
    const view = env.create(party);
    assert.equal(view.party_type, party);
    assert.equal(
      view.order_doctype,
      party === "Customer" ? "Sales Order" : "Purchase Order"
    );
    assert.deepEqual(
      plain(view.item_columns()[0].route({ name: "ITEM-RAW" })),
      ["Form", "Item", "ITEM-RAW"]
    );
    assert.deepEqual(plain(view.item_query()), {
      query:
        "erpnext.selling.doctype.party_specific_item.party_specific_item.party_item_query",
      filters: { party_type: party, party: "PARTY-RAW" },
    });
    const group = Object.freeze({
      restrict_based_on: "Item Group",
      based_on_value: "GROUP-RAW",
    });
    view.show_rule_items(group);
    assert.deepEqual(env.routes.at(-1), ["List", "Item"]);
    assert.deepEqual(plain(env.scope.frappe.route_options), {
      item_group: ["descendants of (inclusive)", "GROUP-RAW"],
      disabled: 0,
    });
    view.show_rule_items(
      Object.freeze({ restrict_based_on: "Brand", based_on_value: "BRAND-RAW" })
    );
    assert.deepEqual(env.routes.at(-1), ["List", "Item"]);
    assert.deepEqual(plain(env.scope.frappe.route_options), {
      brand: "BRAND-RAW",
      disabled: 0,
    });
    assert.deepEqual(plain(group), {
      restrict_based_on: "Item Group",
      based_on_value: "GROUP-RAW",
    });
  }
});
