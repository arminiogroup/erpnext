// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const plain = (value) => JSON.parse(JSON.stringify(value));
const contextual = {
  "1 quotation:Quotation": "1 preventivo",
  "{0} quotations:Quotation": "{0} preventivi",
  "1 quotation:Supplier Quotation": "1 offerta del fornitore",
  "{0} quotations:Supplier Quotation": "{0} offerte del fornitore",
};

function environment(messages = contextual, allowed = []) {
  const cards = [];
  const routes = [];
  const registrations = new Map();
  const permissions = new Set(allowed);
  class UiSink {
    constructor(tag) {
      this.tag = tag;
      this.children = [];
      this.classes = [];
    }
    empty() {
      this.children = [];
      return this;
    }
    append(...items) {
      this.children.push(...items);
      return this;
    }
    appendTo(parent) {
      parent.append(this);
      return this;
    }
    text(value) {
      this.content = value;
      return this;
    }
    css() {
      return this;
    }
    addClass(value) {
      this.classes.push(value);
      return this;
    }
  }
  const ui = (tag) => new UiSink(tag);
  const scope = {
    $: ui,
    jQuery: ui,
    erpnext: {},
    flt: (value) => Number(value) || 0,
    format_currency: (value, currency) => `${currency}:${value}`,
    frappe: {
      _messages: { ...messages },
      provide() {},
      scrub: (value) => value.toLowerCase().replaceAll(" ", "_"),
      model: { can_read: (doctype) => permissions.has(doctype) },
      ui: {
        form: { on: (doctype, methods) => registrations.set(doctype, methods) },
        stat_cards({ items }) {
          cards.push(items);
          return ui("stat_cards");
        },
      },
      set_route: (...args) => routes.push(args),
    },
  };
  scope.window = scope;
  const context = vm.createContext(scope);
  for (const path of [
    "../../../../frappe/frappe/public/js/frappe/format.js",
    "../../../../frappe/frappe/public/js/frappe/translate.js",
    "./party_overview.js",
    "./customer_overview.js",
    "./supplier_overview.js",
  ]) {
    const url = new URL(path, import.meta.url);
    vm.runInContext(
      `(() => {\n${fs.readFileSync(url, "utf8")}\n})();`,
      context,
      {
        filename: url.pathname,
      }
    );
  }
  function create(party) {
    const frm = { doc: Object.freeze({ name: "PARTY-RAW" }) };
    const view = new scope.erpnext[`${party}Overview`](frm);
    view.state = Object.freeze({ company: "COMPANY-RAW" });
    view.currency = "EUR";
    view.$pipeline = ui("pipeline");
    return view;
  }
  return { scope, cards, routes, registrations, permissions, create };
}

function quotationSpec(env, party, data) {
  const view = env.create(party);
  const specs = view.pipeline_specs({ quotations: data });
  return {
    view,
    spec: specs.find((item) => item.route[0].includes("Quotation")),
  };
}

test("customer counts choose the sales quotation context over colliding global wording", () => {
  const env = environment(
    {
      ...contextual,
      "1 quotation": "GLOBAL-SINGULAR",
      "{0} quotations": "GLOBAL-{0}",
    },
    ["Quotation"]
  );
  for (const [count, expected] of [
    [1, "1 preventivo"],
    [2, "2 preventivi"],
    [0, "0 preventivi"],
    ["1", "1 preventivi"],
  ]) {
    const data = Object.freeze({ count, value: 125 });
    const { spec } = quotationSpec(env, "Customer", data);
    assert.equal(spec.data, data);
    assert.equal(spec.caption(data), expected);
  }
});

test("supplier counts retain one supplier scope and the independent expiry caption", () => {
  const env = environment(
    {
      ...contextual,
      "1 quotation": "GLOBAL-SINGULAR",
      "{0} quotations": "GLOBAL-{0}",
      "{0} expiring this week": "{0} in scadenza questa settimana",
    },
    ["Supplier Quotation"]
  );
  for (const [count, expected] of [
    [1, "1 offerta del fornitore"],
    [3, "3 offerte del fornitore"],
  ]) {
    const data = Object.freeze({ count, value: 250, expiring: 2 });
    const { spec } = quotationSpec(env, "Supplier", data);
    assert.equal(
      spec.caption(data),
      `${expected} · 2 in scadenza questa settimana`
    );
  }
});

test("missing or empty context targets retain the real translator global and English fallbacks", () => {
  for (const party of ["Customer", "Supplier"]) {
    const doctype = party === "Customer" ? "Quotation" : "Supplier Quotation";
    for (const [messages, expected] of [
      [{}, ["1 quotation", "2 quotations"]],
      [
        { "1 quotation": "custom one", "{0} quotations": "custom {0}" },
        ["custom one", "custom 2"],
      ],
      [
        { [`1 quotation:${doctype}`]: "", "1 quotation": "custom one" },
        ["custom one", "2 quotations"],
      ],
    ]) {
      const env = environment(messages, [doctype]);
      for (const count of [1, 2]) {
        const data = { count, value: 50 };
        assert.equal(
          quotationSpec(env, party, data).spec.caption(data),
          expected[count - 1]
        );
      }
    }
  }
});

for (const party of ["Customer", "Supplier"]) {
  test(`${party} render_pipeline uses contextual counts while routes and raw state remain canonical`, () => {
    const doctype = party === "Customer" ? "Quotation" : "Supplier Quotation";
    const env = environment(
      {
        ...contextual,
        Quotation: "Preventivo",
        "Supplier Quotation": "Offerta del fornitore",
      },
      [doctype]
    );
    for (const count of [1, 2]) {
      const view = env.create(party);
      const payload = { quotations: { count, value: 125, expiring: 0 } };
      view.overview = { data: { pipeline: payload } };
      const before = plain({ payload, state: view.state, doc: view.frm.doc });
      view.render_pipeline();
      const items = env.cards.at(-1);
      assert.equal(items.length, 1);
      assert.equal(items[0].value, "EUR:125");
      assert.equal(
        items[0].caption,
        party === "Customer"
          ? count === 1
            ? "1 preventivo"
            : "2 preventivi"
          : count === 1
          ? "1 offerta del fornitore"
          : "2 offerte del fornitore"
      );
      items[0].onclick();
      assert.deepEqual(env.routes.at(-1), ["List", doctype]);
      assert.deepEqual(
        plain(env.scope.frappe.route_options),
        party === "Customer"
          ? {
              quotation_to: "Customer",
              party_name: "PARTY-RAW",
              company: "COMPANY-RAW",
              status: "Open",
            }
          : {
              supplier: "PARTY-RAW",
              company: "COMPANY-RAW",
              docstatus: 1,
              status: ["in", ["Submitted", "Partially Ordered"]],
            }
      );
      assert.deepEqual(
        plain({ payload, state: view.state, doc: view.frm.doc }),
        before
      );
    }
  });
}

test("zero tiles retain None open while loading, errors and read permissions keep their branches", () => {
  for (const party of ["Customer", "Supplier"]) {
    const doctype = party === "Customer" ? "Quotation" : "Supplier Quotation";
    const env = environment(
      { ...contextual, "None open": "Nessun documento aperto" },
      [doctype]
    );
    const view = env.create(party);
    view.overview = {
      data: { pipeline: { quotations: { count: 0, value: 0 } } },
    };
    view.render_pipeline();
    assert.equal(env.cards.at(-1)[0].caption, "Nessun documento aperto");
    view.overview = { loading: true };
    view.render_pipeline();
    assert.equal(env.cards.at(-1)[0].loading, true);
    assert.equal(env.cards.at(-1)[0].caption, undefined);
    for (const failed of [{ error: true }, { data: {} }]) {
      view.overview = failed;
      const count = env.cards.length;
      view.render_pipeline();
      assert.equal(env.cards.length, count);
      assert.equal(view.$pipeline.children.at(-1).content, view.overview_error);
      assert.ok(view.$pipeline.children.at(-1).classes.includes("co-error"));
    }
    env.permissions.delete(doctype);
    assert.equal(
      view.pipeline_specs({ quotations: { count: 2, value: 125 } }).length,
      0
    );
    assert.equal(view.accounts, false);
  }
});

test("constructors and other order captions keep their existing account permissions and identifiers", () => {
  const messages = {
    ...contextual,
    "1 order": "1 ordine",
    "{0} orders": "{0} ordini",
  };
  for (const [party, invoice, order] of [
    ["Customer", "Sales Invoice", "Sales Order"],
    ["Supplier", "Purchase Invoice", "Purchase Order"],
  ]) {
    const env = environment(messages, [invoice, "GL Entry", order]);
    const view = env.create(party);
    assert.equal(view.party_type, party);
    assert.equal(view.party_key, party.toLowerCase());
    assert.equal(view.invoice_doctype, invoice);
    assert.equal(view.accounts, true);
    assert.ok(env.registrations.has(party));
    const payload =
      party === "Customer"
        ? {
            delivery: { count: 1, value: 10 },
            billing: { count: 2, value: 20 },
          }
        : {
            receipt: { count: 1, value: 10 },
            billing: { count: 2, value: 20 },
            late: { count: 0, value: 0 },
          };
    const before = plain(payload);
    const specs = view.pipeline_specs(payload);
    assert.equal(specs[0].caption(specs[0].data), "1 ordine");
    assert.ok(specs[1].caption(specs[1].data).startsWith("2 ordini · "));
    assert.equal(specs[0].route[0], order);
    assert.deepEqual(plain(payload), before);
    env.permissions.delete("GL Entry");
    assert.equal(env.create(party).accounts, false);
  }
});
