import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const frappeRoot = new URL(
  "../../../../../frappe/frappe/public/js/frappe/",
  import.meta.url
);
const frame =
  "POS Closing failed while running in a background process. You can resolve the {0} and retry the process again.";
const messages = {
  [frame]:
    "La chiusura POS non è riuscita durante l’esecuzione in background. Risolvere il {0}, quindi riprovare.",
  issue: "problema",
  Retry: "Riprova",
};

function refresh(doc, dictionary = messages) {
  const headlines = [];
  const buttons = [];
  const bindings = [];
  const scrolls = [];
  const scope = { handlers: {}, window: null };
  scope.window = scope;
  scope.$ = (selector) => ({
    on: (event, callback) => bindings.push({ selector, event, callback }),
  });
  scope.$.extend = Object.assign;
  scope.jQuery = scope.$;
  scope.frappe = {
    _messages: dictionary,
    ui: { form: { on: (name, handlers) => (scope.handlers[name] = handlers) } },
    utils: { scroll_to: (...args) => scrolls.push(args) },
    call: () => assert.fail("No site calls or retries in display test"),
  };
  const context = vm.createContext(scope);
  for (const file of ["format.js", "translate.js"]) {
    vm.runInContext(
      fs.readFileSync(new URL(file, frappeRoot), "utf8"),
      context,
      { filename: file }
    );
  }
  vm.runInContext(
    fs.readFileSync(new URL("pos_closing_entry.js", import.meta.url), "utf8"),
    context
  );
  const errorWrapper = { identity: "error_message wrapper" };
  const frm = {
    doc,
    dashboard: { set_headline: (message) => headlines.push(message) },
    add_custom_button: (label, callback) => buttons.push({ label, callback }),
    get_field: (name) => {
      assert.equal(name, "error_message");
      return { $wrapper: errorWrapper };
    },
    call: () => assert.fail("Registered retry must not be invoked"),
    reload_doc: () => assert.fail("No reload in display test"),
  };
  scope.handlers["POS Closing Entry"].refresh(frm);
  return { headlines, buttons, bindings, scrolls, errorWrapper };
}

for (const [label, dictionary, expected] of [
  [
    "Italian",
    messages,
    'La chiusura POS non è riuscita durante l’esecuzione in background. Risolvere il <a id="jump_to_error" style="text-decoration: underline;">problema</a>, quindi riprovare.',
  ],
  [
    "English",
    {},
    'POS Closing failed while running in a background process. You can resolve the <a id="jump_to_error" style="text-decoration: underline;">issue</a> and retry the process again.',
  ],
  [
    "empty anchor target",
    { ...messages, issue: "" },
    'La chiusura POS non è riuscita durante l’esecuzione in background. Risolvere il <a id="jump_to_error" style="text-decoration: underline;">issue</a>, quindi riprovare.',
  ],
]) {
  test(`${label}: submitted failure retains actual issue link and retry registration`, () => {
    const doc = {
      name: "CLOSE-EXAMPLE",
      docstatus: 1,
      status: "Failed",
      error_message: '<tag>&"detail',
    };
    const before = structuredClone(doc);
    const e = refresh(doc, dictionary);
    assert.deepEqual(e.headlines, [expected]);
    assert.equal(e.buttons.length, 1);
    assert.equal(e.buttons[0].label, dictionary.Retry || "Retry");
    assert.equal(typeof e.buttons[0].callback, "function");
    assert.equal(e.bindings.length, 1);
    assert.equal(e.bindings[0].selector, "#jump_to_error");
    assert.equal(e.bindings[0].event, "click");
    const prevented = [];
    e.bindings[0].callback({ preventDefault: () => prevented.push(true) });
    assert.deepEqual(prevented, [true]);
    assert.deepEqual(e.scrolls, [[e.errorWrapper, true, 30]]);
    assert.deepEqual(doc, before);
  });
}

for (const [docstatus, status] of [
  [0, "Failed"],
  [1, "Submitted"],
  [2, "Failed"],
  [1, "Consolidated"],
]) {
  test(`nonfailure guard preserves no notice/actions: ${docstatus}/${status}`, () => {
    const doc = {
      name: "CLOSE-EXAMPLE",
      docstatus,
      status,
      error_message: "literal detail",
    };
    const before = structuredClone(doc);
    const e = refresh(doc);
    assert.deepEqual(e.headlines, []);
    assert.deepEqual(e.buttons, []);
    assert.deepEqual(e.bindings, []);
    assert.deepEqual(e.scrolls, []);
    assert.deepEqual(doc, before);
  });
}
