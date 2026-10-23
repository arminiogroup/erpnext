// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SOURCE =
  "This preview shows the email subject and message. When sending, a print of this document is attached only if Send Document Print is enabled.";
const ITALIAN =
  "Questa anteprima mostra l'oggetto e il contenuto dell'email. All'invio, la stampa del documento viene allegata solo se è attiva l'opzione «Invia stampa documento».";

function environment(messages) {
  const forms = new Map();
  let dialog;
  class MemoryMarkup {
    content = "";
    append(value) {
      this.content += value;
      return this;
    }
    empty() {
      this.content = "";
      return this;
    }
  }
  const scope = {
    erpnext: { taxes_and_totals: class {} },
    frappe: {
      _messages: messages,
      provide() {},
      ui: {
        form: {
          on: (name, handlers) => forms.set(name, handlers),
          set_controller() {},
        },
        Dialog: class {
          constructor(options) {
            this.options = options;
            this.values = {};
            this.fields_dict = Object.fromEntries(
              options.fields.map((df) => [
                df.fieldname,
                { df, $wrapper: new MemoryMarkup() },
              ])
            );
            dialog = this;
          }
          get_value(name) {
            return this.values[name];
          }
          set_value(name, value) {
            this.values[name] = value;
          }
          show() {
            this.shown = true;
          }
        },
      },
    },
  };
  scope.window = scope;
  scope.$ = {};
  scope.jQuery = scope.$;
  const context = vm.createContext(scope);
  for (const [app, path] of [
    ["frappe", "frappe/public/js/frappe/format.js"],
    ["frappe", "frappe/public/js/frappe/translate.js"],
    ["erpnext", "erpnext/public/js/controllers/transaction.js"],
    ["erpnext", "erpnext/public/js/controllers/buying.js"],
    [
      "erpnext",
      "erpnext/buying/doctype/request_for_quotation/request_for_quotation.js",
    ],
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
    preview: forms.get("Request for Quotation").preview,
    dialog: () => dialog,
  };
}

for (const [language, messages, expected] of [
  ["Italian", { [SOURCE]: ITALIAN }, ITALIAN],
  ["English fallback", {}, SOURCE],
]) {
  test(`${language} preview explains conditional document print for both raw option values`, () => {
    const env = environment(messages);
    for (const send_document_print of [0, 1]) {
      const doc = {
        suppliers: [{ supplier: "SUPPLIER-RAW" }],
        send_document_print,
      };
      const before = JSON.stringify(doc);
      env.preview({ doc });
      const dialog = env.dialog();
      assert.equal(
        dialog.fields_dict.note.$wrapper.content,
        `<p class="small text-muted">${expected}</p>`
      );
      assert.equal(dialog.shown, true);
      assert.equal(JSON.stringify(doc), before);
    }
  });
}

test("actual supplier onchange preserves raw RPC, message HTML, subject and read-only form state", async () => {
  const env = environment({ [SOURCE]: "" });
  const doc = {
    suppliers: [{ supplier: "Supplier" }, { supplier: "Quotation" }],
    send_document_print: 0,
  };
  const before = JSON.stringify(doc);
  const calls = [];
  env.preview({
    doc,
    call(method, args) {
      calls.push([method, JSON.parse(JSON.stringify(args))]);
      return Promise.resolve({
        message: {
          subject: "Raw subject <&>",
          message: "<p>Raw supplier <strong>message</strong></p>",
        },
      });
    },
  });
  const dialog = env.dialog();
  assert.deepEqual(Array.from(dialog.fields_dict.supplier.df.options), [
    "Supplier",
    "Quotation",
  ]);
  assert.equal(dialog.fields_dict.subject.df.read_only, 1);
  assert.equal(dialog.fields_dict.supplier.df.reqd, 1);
  dialog.values.supplier = "Quotation";
  dialog.fields_dict.supplier.df.onchange();
  await Promise.resolve();
  assert.deepEqual(calls, [
    ["get_supplier_email_preview", { supplier: "Quotation" }],
  ]);
  assert.equal(
    dialog.fields_dict.email_preview.$wrapper.content,
    "<p>Raw supplier <strong>message</strong></p>"
  );
  assert.equal(dialog.values.subject, "Raw subject <&>");
  assert.equal(JSON.stringify(doc), before);
});
