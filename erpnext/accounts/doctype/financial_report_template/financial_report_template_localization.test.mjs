import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const frappeRoot = fileURLToPath(
  new URL("../../../../../frappe/", import.meta.url)
);

function load() {
  const dialogs = [];
  const calls = [];
  const trees = [];
  const host = {
    jQuery: null,
    frappe: {
      _messages: {
        View: "Visualizza",
        "View:Financial Report Template": "Vista",
        "Missing Accounts": "Conti mancanti",
        "Missing Accounts:Financial Report Template": "Conti non inclusi",
        "Filtered Accounts": "Conti inclusi dai filtri",
      },
      defaults: { get_user_default: () => "Azienda di prova" },
      ui: {
        form: { on() {} },
        Dialog: class {
          constructor(options) {
            this.options = options;
            this.values = Object.fromEntries(
              options.fields.map((field) => [field.fieldname, field.default])
            );
            dialogs.push(this);
          }
          show() {}
          get_value(name) {
            return this.values[name];
          }
          get_field() {
            return { $wrapper: { empty() {} } };
          }
          set_title(value) {
            this.title = value;
          }
        },
        Tree: class {
          constructor(options) {
            trees.push(options);
          }
          load_children() {}
        },
      },
      call: async (options) => {
        calls.push(options);
        return { message: ["Cassa - PRV"] };
      },
    },
  };
  host.window = host;
  const context = vm.createContext(host);
  for (const file of ["format.js", "translate.js"])
    vm.runInContext(
      readFileSync(
        resolve(frappeRoot, "frappe/public/js/frappe", file),
        "utf8"
      ),
      context
    );
  vm.runInContext(
    readFileSync(
      new URL("./financial_report_template.js", import.meta.url),
      "utf8"
    ),
    context
  );
  return { host, context, dialogs, calls, trees };
}

for (const hasSelection of [false, true]) {
  test(`account coverage localizes display while retaining ${
    hasSelection ? "selected" : "all"
  } row filtering`, async () => {
    const { host, context, dialogs, calls, trees } = load();
    const rows = [
      {
        data_source: "Account Data",
        calculation_formula: '["account_type","=","Cash"]',
      },
      { data_source: "Blank Line" },
    ];
    const snapshot = JSON.stringify(rows);
    context.rows = rows;
    context.hasSelection = hasSelection;
    vm.runInContext("show_accounts_tree(rows, hasSelection)", context);
    await new Promise((resolve) => setImmediate(resolve));
    const dialog = dialogs[0];
    const field = dialog.options.fields.find(
      (field) => field.fieldname === "view_type"
    );
    assert.equal(
      field.default,
      hasSelection ? "Filtered Accounts" : "Missing Accounts"
    );
    assert.equal(calls.length, 1);
    assert.equal(
      calls[0].method,
      "erpnext.accounts.doctype.financial_report_template.financial_report_engine.get_filtered_accounts"
    );
    assert.equal(calls[0].args.company, "Azienda di prova");
    assert.deepEqual(JSON.parse(JSON.stringify(calls[0].args.account_rows)), [
      rows[0],
    ]);
    assert.equal(trees[0].args.missed, !hasSelection);
    assert.deepEqual(Array.from(trees[0].args.filtered_accounts), [
      "Cassa - PRV",
    ]);
    assert.equal(
      trees[0].method,
      "erpnext.accounts.doctype.financial_report_template.financial_report_engine.get_children_accounts"
    );
    dialog.values.view_type = hasSelection
      ? "Missing Accounts"
      : "Filtered Accounts";
    field.onchange();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(trees[1].args.missed, hasSelection);
    assert.equal(JSON.stringify(rows), snapshot);
    assert.equal(field.label, "Vista");
    assert.deepEqual(JSON.parse(JSON.stringify(field.options)), [
      { value: "Missing Accounts", label: "Conti non inclusi" },
      { value: "Filtered Accounts", label: "Conti inclusi dai filtri" },
    ]);
    assert.equal(host.__("View"), "Visualizza");
    assert.equal(host.__("Missing Accounts"), "Conti mancanti");
  });
}
