import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const frappeRoot = new URL(
  "../../../../../frappe/frappe/public/js/frappe/",
  import.meta.url
);
const messages = {
  "Try the {0} for a better experience.":
    "Prova il {0} per un’esperienza migliore.",
  "new Banking module": "nuovo modulo bancario",
};
function refresh(dictionary = messages) {
  const scope = { erpnext: {}, jQuery: {}, actions: [], events: [] };
  scope.window = scope;
  scope.$ = scope.jQuery;
  scope.frappe = {
    _messages: dictionary,
    provide: (name) =>
      name.split(".").reduce((object, key) => (object[key] ||= {}), scope),
    ui: {
      form: {
        on: (doctype, handlers) => {
          assert.equal(doctype, "Bank Reconciliation Tool");
          scope.handlers = handlers;
        },
      },
    },
    require: (asset, callback) => scope.events.push([asset, typeof callback]),
    call: () => {
      throw Error("No action request allowed");
    },
  };
  const context = vm.createContext(scope);
  for (const file of ["format.js", "translate.js"])
    vm.runInContext(readFileSync(new URL(file, frappeRoot), "utf8"), context);
  vm.runInContext(
    readFileSync(
      new URL("./bank_reconciliation_tool.js", import.meta.url),
      "utf8"
    ),
    context
  );
  let intro;
  const doc = {
    doctype: "Bank Reconciliation Tool",
    name: "BT-TOOL",
    company: "Company",
    bank_account: "BANK-RAW",
  };
  const before = JSON.stringify(doc);
  scope.handlers.refresh({
    doc,
    disable_save: () => scope.events.push("disable_save"),
    add_custom_button: (label, action) => scope.actions.push({ label, action }),
    change_custom_button_type: (...args) => scope.events.push(args),
    set_intro: (...args) => {
      intro = args;
    },
  });
  assert.equal(JSON.stringify(doc), before);
  assert.equal(scope.actions.length, 3);
  assert.ok(
    scope.actions.every((action) => typeof action.action === "function")
  );
  assert.deepEqual(scope.events[1], [
    "bank-reconciliation-tool.bundle.js",
    "function",
  ]);
  assert.equal(intro[1], "info");
  return intro[0];
}
test("actual refresh inserts the complete Italian module link inside the translated frame", () => {
  assert.equal(
    refresh(),
    "Prova il <a href='/banking'>nuovo modulo bancario</a> per un’esperienza migliore."
  );
});
test("the English dictionary fallback preserves the exact anchor and href", () => {
  assert.equal(
    refresh({}),
    "Try the <a href='/banking'>new Banking module</a> for a better experience."
  );
});
test("a translated frame can reorder the intact anchor without changing its canonical destination", () => {
  assert.equal(
    refresh({
      ...messages,
      "Try the {0} for a better experience.":
        "Per un’esperienza migliore, prova il {0}.",
    }),
    "Per un’esperienza migliore, prova il <a href='/banking'>nuovo modulo bancario</a>."
  );
});
test("empty or quoted label providers preserve gettext fallback and existing trusted HTML delivery", () => {
  assert.equal(
    refresh({ ...messages, "new Banking module": "" }),
    "Prova il <a href='/banking'>new Banking module</a> per un’esperienza migliore."
  );
  assert.equal(
    refresh({ ...messages, "new Banking module": 'modulo & "nuovo"' }),
    "Prova il <a href='/banking'>modulo & \"nuovo\"</a> per un’esperienza migliore."
  );
});
