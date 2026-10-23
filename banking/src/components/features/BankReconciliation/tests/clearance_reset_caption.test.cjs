// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { createRequire } = require("node:module");
const app = path.resolve(__dirname, "../../../../../..") + path.sep;
const ts = createRequire(path.join(app, "banking/package.json"))("typescript");
const SOURCE = "Clearance date reset requested.";
const ITALIAN = "Richiesta di rimozione della data di regolamento inviata.";

function banking(which, result, messages, reject = false) {
  const calls = [],
    toasts = [],
    mutations = [];
  const bank = {
    account: "ACCOUNT-RAW",
    name: "BANK-RAW",
    account_currency: "EUR",
  };
  const dates = { fromDate: "2026-10-01", toDate: "2026-10-21" };
  const scope = { window: { frappe: { _messages: messages } } };
  const context = vm.createContext(scope);
  let translator;
  function load(path) {
    const source = fs.readFileSync(app + path, "utf8");
    let code = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText;
    if (path.endsWith("IncorrectlyClearedEntries.tsx"))
      code += "\nexports.auditView = IncorrectlyClearedEntriesView;";
    if (path.endsWith("BankClearanceSummary.tsx"))
      code += "\nexports.auditView = BankClearanceSummaryView;";
    const exports = {};
    function external(name) {
      if (name === "react/jsx-runtime")
        return {
          jsx: (type, props) => ({ type, props }),
          jsxs: (type, props) => ({ type, props }),
        };
      if (name === "react")
        return {
          useMemo: (fn) => fn(),
          useCallback: (fn) => fn,
          useState: (value) => [value, () => {}],
        };
      if (name === "jotai")
        return { useAtomValue: (atom) => (atom === "BANK" ? bank : dates) };
      if (name === "./bankRecAtoms")
        return { selectedBankAccountAtom: "BANK", bankRecDateAtom: "DATES" };
      if (name === "@/hooks/useCurrentCompany")
        return { useCurrentCompany: () => "COMPANY-RAW" };
      if (name === "@/lib/translate") return { default: translator };
      if (name === "sonner")
        return {
          toast: {
            success: (...args) => toasts.push(["success", ...args]),
            error: (...args) => toasts.push(["error", ...args]),
          },
        };
      if (name === "frappe-react-sdk")
        return {
          useFrappeGetCall: () => ({
            data: { message: { result: [result] } },
            mutate: () => mutations.push("mutate"),
          }),
          useFrappePostCall: (method) => ({
            call: (args) => {
              calls.push([method, args]);
              return reject
                ? Promise.reject(new Error("MEMORY-REJECTION"))
                : Promise.resolve(undefined);
            },
          }),
        };
      if (name === "@/lib/date") return { formatDate: (value) => value };
      if (name === "@/lib/numbers")
        return { formatCurrency: (value, currency) => `${currency}:${value}` };
      if (name === "@/lib/company") return { getCompanyCurrency: () => "EUR" };
      if (name === "@/lib/frappe")
        return {
          getErrorMessage: (error) => error.message,
          slug: (value) => value.toLowerCase().replaceAll(" ", "-"),
        };
      if (name === "usehooks-ts")
        return { useCopyToClipboard: () => [null, () => Promise.resolve()] };
      return new Proxy({}, { get: (_, key) => String(key) });
    }
    vm.runInContext(`(function(require,exports){${code}\n})`, context, {
      filename: path,
    })(external, exports);
    return exports;
  }
  translator = load("banking/src/lib/translate.ts").default;
  const module = load(
    "banking/src/components/features/BankReconciliation/" + which + ".tsx"
  );
  const tree = module.auditView();
  function find(node, type) {
    if (!node) return;
    if (Array.isArray(node)) {
      for (const child of node) {
        const found = find(child, type);
        if (found) return found;
      }
    } else if (typeof node === "object") {
      if (node.type === type) return node;
      return find(node.props?.children, type);
    }
  }
  const list = find(tree, "ListView");
  assert.ok(list);
  return { calls, toasts, mutations, columns: list.props.columns, translator };
}

async function click(env, row) {
  env.columns
    .find((column) => column.id === "actions")
    .cell({ row: { original: row } })
    .props.onClick();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

for (const [name, messages, expected] of [
  ["Italian", { [SOURCE]: ITALIAN, Cleared: "Regolato" }, ITALIAN],
  ["English fallback", {}, SOURCE],
  ["empty target fallback", { [SOURCE]: "", Cleared: "Regolato" }, SOURCE],
]) {
  test(`${name} fulfilled reset request reports the request and keeps canonical RPC/mutate order`, async () => {
    const row = { payment_document: "Payment Entry", payment_entry: "PAY-RAW" };
    const before = JSON.stringify(row);
    const env = banking("IncorrectlyClearedEntries", row, messages);
    await click(env, row);
    assert.deepEqual(JSON.parse(JSON.stringify(env.calls)), [
      [
        "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.clear_clearing_date",
        { voucher_type: "Payment Entry", voucher_name: "PAY-RAW" },
      ],
    ]);
    assert.equal(env.toasts.length, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(env.toasts[0])), [
      "success",
      expected,
      { duration: 1000 },
    ]);
    assert.deepEqual(env.mutations, ["mutate"]);
    assert.equal(JSON.stringify(row), before);
  });
}

test("a rejected reset keeps the existing error and does not refresh results", async () => {
  const row = {
    payment_document: "Journal Entry",
    payment_entry: "JOURNAL-RAW",
  };
  const source = "There was an error while performing the action.";
  const env = banking(
    "IncorrectlyClearedEntries",
    row,
    { [SOURCE]: ITALIAN, [source]: "Errore durante l’operazione." },
    true
  );
  await click(env, row);
  assert.equal(env.toasts[0][0], "error");
  assert.equal(env.toasts[0][1], "Errore durante l’operazione.");
  assert.deepEqual(JSON.parse(JSON.stringify(env.toasts[0][2])), {
    description: "MEMORY-REJECTION",
    duration: 5000,
  });
  assert.deepEqual(env.mutations, []);
});

test("the independent positive and negative badges retain their existing keys and date truthiness", () => {
  for (const date of ["2026-10-20", null, ""]) {
    const row = {
      clearance_date: date,
      payment_document_type: "Payment Entry",
      payment_entry: "PAY-RAW",
    };
    const before = JSON.stringify(row);
    const env = banking("BankClearanceSummary", row, {
      Cleared: "Regolato",
      "Not Cleared": "Non regolato",
      [SOURCE]: ITALIAN,
    });
    const cell = env.columns
      .find((column) => column.id === "status")
      .cell({ row: { original: row } });
    assert.ok(
      JSON.stringify(cell).includes(date ? "Regolato" : "Non regolato")
    );
    assert.equal(JSON.stringify(row), before);
    assert.deepEqual(env.calls, []);
  }
});
