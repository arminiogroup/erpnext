import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
const frappeRoot = new URL(
  "../../../../../frappe/frappe/public/js/frappe/",
  import.meta.url
);
const messages = {
  "Payment Entry": "Registrazione di pagamento",
  "Journal Entry": "Registrazione contabile",
  "Bank Transaction {0} added as {1}":
    "Transazione bancaria {0}: documento di tipo {1} creato.",
  "Cannot create {0} from a Bank Transaction":
    "Impossibile creare {0} da una transazione bancaria",
};
function environment({ dictionary = messages, reject, custom } = {}) {
  const events = [],
    scope = { erpnext: {}, jQuery: {} };
  scope.window = scope;
  scope.$ = scope.jQuery;
  scope.frappe = {
    _messages: dictionary,
    provide: (name) =>
      name.split(".").reduce((object, key) => (object[key] ||= {}), scope),
    ui: { form: { on: () => {} } },
    show_alert: (message) => events.push(["alert", message]),
    throw: (message) => {
      throw Error(message);
    },
    xcall: (method, args) => {
      events.push(["xcall", method, structuredClone(args)]);
      if (reject) return Promise.reject(reject);
      return Promise.resolve({
        doctype: method.includes("create_journal")
          ? "Journal Entry"
          : "Payment Entry",
        name: 'VOUCHER-RAW & "quoted"',
        amount: 24,
      });
    },
    model: {
      sync: (message) => {
        events.push(["sync", structuredClone(message)]);
        return [message];
      },
    },
    set_route: (...args) => events.push(["route", ...args]),
  };
  const context = vm.createContext(scope);
  for (const file of ["format.js", "translate.js"])
    vm.runInContext(readFileSync(new URL(file, frappeRoot), "utf8"), context);
  scope.frappe.provide("erpnext.accounts.bank_reconciliation.voucher_types");
  if (custom)
    scope.erpnext.accounts.bank_reconciliation.voucher_types[custom.name] =
      custom.entry;
  vm.runInContext(
    readFileSync(new URL("./dialog_manager.js", import.meta.url), "utf8"),
    context
  );
  const dm = Object.create(
    scope.erpnext.accounts.bank_reconciliation.DialogManager.prototype
  );
  dm.bank_transaction = {
    name: "Payment Entry",
    company: "COMPANY-RAW",
    amount: 24,
  };
  dm.bank_account = "BANK-RAW";
  dm.dialog = { hide: () => events.push(["hide"]) };
  dm.update_dt_cards = (message) =>
    events.push(["update", structuredClone(message)]);
  return { dm, events, scope };
}
function values(doctype) {
  return {
    document_type: doctype,
    reference_number: "Company",
    reference_date: "2026-10-20",
    party_type: "Customer",
    party: "Payment Entry",
    posting_date: "2026-10-21",
    mode_of_payment: "MODE-RAW",
    project: "PROJECT-RAW",
    cost_center: "COST-RAW",
    journal_entry_type: "Bank Entry",
    second_account: "ACCOUNT-RAW",
  };
}
for (const [doctype, display] of [
  ["Payment Entry", "Registrazione di pagamento"],
  ["Journal Entry", "Registrazione contabile"],
]) {
  for (const allowEdit of [false, true]) {
    test(`${doctype} ${
      allowEdit ? "edit" : "success"
    } keeps actual registry payload and action ordering`, async () => {
      const env = environment(),
        input = values(doctype),
        before = JSON.stringify(input);
      await env.dm.create_voucher(input, allowEdit);
      assert.equal(JSON.stringify(input), before);
      const call = env.events[0];
      assert.equal(call[0], "xcall");
      assert.equal(
        call[1],
        `erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.create_${
          doctype === "Payment Entry" ? "payment" : "journal"
        }_entry_bts`
      );
      const expected = {
        bank_transaction_name: "Payment Entry",
        reference_number: "Company",
        reference_date: "2026-10-20",
        party_type: "Customer",
        party: "Payment Entry",
        posting_date: "2026-10-21",
        mode_of_payment: "MODE-RAW",
        allow_edit: allowEdit,
      };
      if (doctype === "Payment Entry")
        Object.assign(expected, {
          project: "PROJECT-RAW",
          cost_center: "COST-RAW",
          company_bank_account: "BANK-RAW",
        });
      else
        Object.assign(expected, {
          entry_type: "Bank Entry",
          second_account: "ACCOUNT-RAW",
        });
      assert.deepEqual(JSON.parse(JSON.stringify(call[2])), expected);
      if (allowEdit) {
        assert.deepEqual(
          env.events.map((event) => event[0]),
          ["xcall", "sync", "route"]
        );
        assert.deepEqual(env.events[2], [
          "route",
          "Form",
          doctype,
          'VOUCHER-RAW & "quoted"',
        ]);
      } else {
        assert.deepEqual(
          env.events.map((event) => event[0]),
          ["xcall", "alert", "update", "hide"]
        );
        assert.equal(
          env.events[1][1],
          `Transazione bancaria Payment Entry: documento di tipo ${display} creato.`
        );
        assert.equal(env.events[2][1].amount, 24);
      }
    });
  }
}
test("custom voucher extension retains applicability, canonical registry key and supplied payload", async () => {
  const calls = [],
    entry = {
      is_applicable: (transaction) => transaction.amount === 24,
      create: (dm, input, allowEdit) => {
        calls.push({ dm, input, allowEdit });
        return Promise.resolve({ name: "RAW-CUSTOM", amount: 12 });
      },
    };
  const env = environment({
    dictionary: { ...messages, "Custom Voucher": "Documento personalizzato" },
    custom: { name: "Custom Voucher", entry },
  });
  assert.ok(env.dm.get_document_types().includes("Custom Voucher"));
  const input = values("Custom Voucher");
  await env.dm.create_voucher(input);
  assert.equal(calls[0].dm, env.dm);
  assert.equal(calls[0].input, input);
  assert.equal(calls[0].allowEdit, false);
  assert.equal(
    env.events[0][1],
    "Transazione bancaria Payment Entry: documento di tipo Documento personalizzato creato."
  );
  assert.equal(env.events[1][1].amount, 12);
  env.dm.bank_transaction.amount = 0;
  assert.ok(!env.dm.get_document_types().includes("Custom Voucher"));
});
test("unknown voucher rejection translates only the display type and never dispatches an action", () => {
  const env = environment({
    dictionary: { ...messages, "Unknown Type": "Tipo sconosciuto" },
  });
  assert.throws(() => env.dm.create_voucher(values("Unknown Type")), {
    message: "Impossibile creare Tipo sconosciuto da una transazione bancaria",
  });
  assert.deepEqual(env.events, []);
});
test("rejected external delivery preserves error identity and performs no completion UI actions", async () => {
  const error = new Error("Server-supplied detail"),
    env = environment({ reject: error });
  await assert.rejects(
    env.dm.create_voucher(values("Payment Entry")),
    (actual) => actual === error
  );
  assert.deepEqual(
    env.events.map((event) => event[0]),
    ["xcall"]
  );
});
test("English and empty translations keep fallback while hostile type captions stay out of payloads", async () => {
  for (const dictionary of [
    {},
    { ...messages, "Payment Entry": "" },
    { ...messages, "Payment Entry": '<b>Tipo</b> & "quoted"' },
  ]) {
    const env = environment({ dictionary });
    await env.dm.create_voucher(values("Payment Entry"));
    const expected = dictionary["Bank Transaction {0} added as {1}"]
      ? `Transazione bancaria Payment Entry: documento di tipo ${
          dictionary["Payment Entry"] || "Payment Entry"
        } creato.`
      : "Bank Transaction Payment Entry added as Payment Entry";
    assert.equal(env.events[1][1], expected);
    assert.equal(env.events[0][2].bank_transaction_name, "Payment Entry");
    assert.equal(env.events[0][1].endsWith("create_payment_entry_bts"), true);
  }
});
