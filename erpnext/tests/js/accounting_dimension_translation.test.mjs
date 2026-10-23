import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../.."
);
const FRAPPE_ROOT =
  process.env.FRAPPE_SOURCE_ROOT || path.resolve(ROOT, "../../frappe/frappe");

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function load({ is_new = false, disabled = 0 } = {}) {
  const handlers = {};
  const buttons = [];
  const routes = [];
  const queries = [];
  const calls = [];
  const values = [];
  const alerts = [];
  const db_reads = [];
  const properties = [];
  let saves = 0;
  const frappe = {
    _messages: { "Show {0}": "Mostra {0}", Project: "Progetto" },
    model: { core_doctypes_list: ["DocType", "User"] },
    ui: {
      form: {
        on: (doctype, events) => {
          handlers[doctype] = events;
        },
      },
    },
    set_route: (...args) => routes.push(args),
    call: (args) => calls.push(args),
    show_alert: (args) => alerts.push(args),
    db: {
      get_value: (...args) => {
        db_reads.push(args);
        args[3]({ document_type: "Project" });
      },
    },
  };
  const jquery = {};
  const locals = {
    "Accounting Dimension Detail": { "ROW-RAW": { company: "Company" } },
  };
  const context = vm.createContext({
    frappe,
    window: {},
    jQuery: jquery,
    $: jquery,
    locals,
  });
  for (const name of ["format.js", "translate.js"]) {
    vm.runInContext(
      fs.readFileSync(path.join(FRAPPE_ROOT, "public/js/frappe", name), "utf8"),
      context
    );
  }
  context.__ = context.window.__;
  vm.runInContext(
    fs.readFileSync(
      path.join(
        ROOT,
        "accounts/doctype/accounting_dimension/accounting_dimension.js"
      ),
      "utf8"
    ),
    context
  );
  const frm = {
    doc: { document_type: "Project", disabled, label: "Custom Label-Axis" },
    is_new: () => is_new,
    set_query: (...args) => queries.push(args),
    add_custom_button: (label, callback) => buttons.push({ label, callback }),
    set_value: (field, value) => {
      values.push([field, value]);
      frm.doc[field] = value;
    },
    save: () => {
      saves++;
    },
    set_df_property: (...args) => properties.push(args),
  };
  return {
    handlers,
    buttons,
    routes,
    queries,
    calls,
    values,
    alerts,
    db_reads,
    properties,
    locals,
    frm,
    get saves() {
      return saves;
    },
  };
}

describe("Accounting dimension labels with the real Frappe translator", () => {
  it("translates the button's DocType while preserving the canonical List route", () => {
    const state = load();
    state.handlers["Accounting Dimension"].refresh(state.frm);
    assert.equal(state.buttons[0].label, "Mostra Progetto");
    state.buttons[0].callback();
    assert.deepEqual(plain(state.routes), [["List", "Project"]]);
    assert.equal(state.frm.doc.document_type, "Project");
  });

  it("keeps canonical query filters and omits buttons for unsaved dimensions", () => {
    const state = load({ is_new: true });
    state.handlers["Accounting Dimension"].refresh(state.frm);
    assert.equal(state.buttons.length, 0);
    assert.equal(state.queries[0][0], "document_type");
    assert.deepEqual(plain(state.queries[0][1]()), {
      filters: {
        name: [
          "not in",
          [
            "DocType",
            "User",
            "Accounting Dimension",
            "Project",
            "Cost Center",
            "Accounting Dimension Detail",
            "Company",
          ],
        ],
        istable: 0,
        issingle: 0,
      },
    });
    assert.deepEqual(state.queries[1].slice(0, 2), [
      "offsetting_account",
      "dimension_defaults",
    ]);
    assert.deepEqual(
      plain(
        state.queries[1][2](
          state.frm.doc,
          "Accounting Dimension Detail",
          "ROW-RAW"
        )
      ),
      {
        filters: {
          company: "Company",
          root_type: ["in", ["Asset", "Liability"]],
          is_group: 0,
        },
      }
    );
  });

  it("preserves enable/disable requests, saved values, callback and alert", () => {
    for (const disabled of [0, 1]) {
      const state = load({ disabled });
      state.handlers["Accounting Dimension"].refresh(state.frm);
      state.buttons[1].callback();
      assert.deepEqual(state.values, [["disabled", 1 - disabled]]);
      assert.equal(
        state.calls[0].method,
        "erpnext.accounts.doctype.accounting_dimension.accounting_dimension.disable_dimension"
      );
      assert.equal(state.calls[0].args.doc, state.frm.doc);
      assert.equal(state.calls[0].freeze, true);
      state.calls[0].callback({});
      assert.equal(state.saves, 1);
      assert.deepEqual(plain(state.alerts), [
        {
          message: disabled ? "Dimension Enabled" : "Dimension Disabled",
          indicator: "green",
        },
      ]);
    }
  });

  it("preserves raw label and fieldname generation plus duplicate lookup payload", () => {
    const state = load();
    state.handlers["Accounting Dimension"].label(state.frm);
    assert.deepEqual(state.values[0], ["fieldname", "custom_label_axis"]);
    state.handlers["Accounting Dimension"].document_type(state.frm);
    assert.deepEqual(state.values[1], ["label", "Project"]);
    assert.deepEqual(plain(state.db_reads[0].slice(0, 3)), [
      "Accounting Dimension",
      { document_type: "Project" },
      "document_type",
    ]);
    assert.deepEqual(plain(state.properties), [
      [
        "document_type",
        "description",
        "Document type is already set as dimension",
      ],
    ]);
  });

  it("keeps the child reference document canonical", () => {
    const state = load();
    state.handlers["Accounting Dimension Detail"].dimension_defaults_add(
      state.frm,
      "Accounting Dimension Detail",
      "ROW-RAW"
    );
    assert.equal(
      state.locals["Accounting Dimension Detail"]["ROW-RAW"].reference_document,
      "Project"
    );
  });
});
