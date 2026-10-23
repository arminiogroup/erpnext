import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const source = readFileSync(
  resolve(root, "erpnext/erpnext/public/js/utils.js"),
  "utf8"
);
const italian = {
  "Mapping {0} ...": "Recupero dei dati da {0}...",
  "Please select {0}": "Seleziona {0}",
  "You have already selected items from {0} {1}":
    "Articoli già selezionati da {0} {1}",
  "Sales Order": "Ordine di vendita",
  Customer: "Cliente",
  Supplier: "Fornitore",
};

function environment(messages) {
  const events = [],
    requests = [],
    notices = [],
    dialogs = [];
  const $ = () => ({ on() {} });
  $.extend = Object.assign;
  $.isArray = Array.isArray;
  $.each = (value, callback) => {
    for (const [key, item] of Object.entries(value)) {
      if (callback(key, item) === false) break;
    }
  };
  const scope = {
    $,
    jQuery: $,
    console,
    flt: Number,
    document: {},
    erpnext: { utils: {}, stock: { utils: {} } },
    frappe: {
      _messages: messages,
      provide() {},
      form: { link_formatters: {} },
      meta: { get_docfield: () => ({ options: "Sales Invoice Item" }) },
      get_meta: () => ({
        fields: [{ fieldname: "sales_order", options: "Sales Order" }],
      }),
      ui: {
        form: {
          MultiSelectDialog: function (options) {
            Object.assign(this, options);
            this.dialog = { hide: () => events.push("hide") };
            dialogs.push(this);
          },
        },
      },
      call: (options) => {
        requests.push(options);
        return options;
      },
      msgprint: (message) => notices.push(message),
      model: {
        sync: (value) => events.push(["sync", value]),
        with_doc: (_type, _name, callback) => callback(),
        get_doc: () => ({ items: [{ item_code: "Customer", qty: 1 }] }),
      },
    },
  };
  scope.window = scope;
  vm.createContext(scope);
  for (const path of ["format.js", "translate.js"]) {
    vm.runInContext(
      readFileSync(
        resolve(root, "frappe/frappe/public/js/frappe", path),
        "utf8"
      ),
      scope,
      { filename: path }
    );
  }
  vm.runInContext(source, scope, { filename: "erpnext/public/js/utils.js" });
  const frm = {
    doctype: "Sales Invoice",
    doc: { doctype: "Sales Invoice", items: [] },
    dirty: () => events.push("dirty"),
    refresh: () => events.push("refresh"),
  };
  return { scope, frm, events, requests, notices, dialogs };
}

for (const [language, messages] of [
  ["it", italian],
  ["en", {}],
]) {
  for (const type of ["Sales Order", "Custom Document"]) {
    test(`${language} ${type}: captions translate while mapping identities and callbacks stay raw`, () => {
      const { scope, frm, events, requests, notices, dialogs } =
        environment(messages);
      const opts = {
        target: frm,
        source_doctype: type,
        method: "raw.app.mapper",
        get_query_filters: { company: "Customer" },
      };
      scope.erpnext.utils.map_current_doc(opts);
      assert.equal(dialogs[0].doctype, type);
      const query = dialogs[0].get_query();
      query.filters.company = "Supplier";
      assert.equal(dialogs[0].get_query().filters.company, "Customer");
      dialogs[0].action([]);
      assert.equal(requests.length, 0);
      const caption =
        language === "it" && type === "Sales Order"
          ? "Ordine di vendita"
          : type;
      assert.equal(
        notices.at(-1),
        language === "it" ? `Seleziona ${caption}` : `Please select ${caption}`
      );
      dialogs[0].action(["Customer", "Customer", "Supplier"], {});
      const request = requests.at(-1);
      assert.equal(
        request.freeze_message,
        language === "it"
          ? `Recupero dei dati da ${caption}...`
          : `Mapping ${caption} ...`
      );
      assert.equal(request.method, "frappe.model.mapper.map_docs");
      assert.equal(request.type, "POST");
      assert.equal(request.args.method, "raw.app.mapper");
      assert.equal(
        JSON.stringify(request.args.source_names),
        '["Customer","Supplier"]'
      );
      assert.equal(request.args.target_doc, frm.doc);
      assert.equal(opts.source_doctype, type);
      assert.equal(opts.get_query_filters.company, "Customer");
      request.callback({ message: { name: "Customer" } });
      assert.deepEqual(events, [
        "hide",
        ["sync", { name: "Customer" }],
        "dirty",
        "refresh",
      ]);
    });
  }
}

test("the existing already-selected notice keeps its translated type and raw source name", () => {
  const { scope, frm, notices } = environment(italian);
  frm.doc.items = [{ item_code: "Customer", qty: 1, sales_order: "Supplier" }];
  scope.erpnext.utils
    .map_current_doc({ target: frm, source_doctype: "Sales Order" })
    .action(["Supplier"], {});
  assert.equal(
    notices.at(-1),
    "Articoli già selezionati da Ordine di vendita Supplier"
  );
});

test("missing source type retains the direct-call contract without translating business data", () => {
  for (const type of [undefined, null, ""]) {
    const { scope, frm, requests, dialogs } = environment(italian);
    scope.erpnext.utils.map_current_doc({
      target: frm,
      source_doctype: type,
      source_name: "Customer",
      method: "raw.app.mapper",
    });
    assert.equal(dialogs.length, 0);
    assert.equal(requests.length, 1);
    assert.equal(JSON.stringify(requests[0].args.source_names), '["Customer"]');
    assert.equal(requests[0].args.target_doc, frm.doc);
  }
});

test("failed mapper response keeps the existing no-sync callback behavior", () => {
  const { scope, frm, requests, events } = environment(italian);
  scope.erpnext.utils
    .map_current_doc({
      target: frm,
      source_doctype: "Sales Order",
      method: "raw.app.mapper",
    })
    .action(["Customer"], {});
  requests[0].callback({ exc: "raw failure" });
  assert.deepEqual(events, ["hide"]);
});

test("mapping progress translates its own type independently of the empty-selection notice", () => {
  const { scope, frm, requests } = environment(italian);
  scope.erpnext.utils
    .map_current_doc({
      target: frm,
      source_doctype: "Sales Order",
      method: "raw.app.mapper",
    })
    .action(["Customer"], {});
  assert.equal(
    requests[0].freeze_message,
    "Recupero dei dati da Ordine di vendita..."
  );
  assert.equal(JSON.stringify(requests[0].args.source_names), '["Customer"]');
});
