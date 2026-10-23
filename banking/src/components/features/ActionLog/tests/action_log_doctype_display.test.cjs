const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");
const banking = path.resolve(__dirname, "../../../../..");
const messages = {
  "Payment Entry": "Registrazione di pagamento",
  "Journal Entry": "Registrazione contabile",
  "Sales Invoice": "Fattura di vendita",
  "Undo {}?": "Annullare il documento di tipo {}?",
  "Unmatch Transaction?": "Annullare l'abbinamento della transazione?",
  "Are you sure you want to unmatch the voucher from this transaction?":
    "Vuoi rimuovere l’abbinamento tra il documento e questa transazione?",
  "Are you sure you want to cancel this {} {}?":
    "Vuoi annullare il documento di tipo {} con riferimento {}?",
};
function environment(dictionary = messages) {
  const calls = [],
    amounts = [];
  const context = vm.createContext({
    window: { frappe: { _messages: dictionary } },
  });
  let translator;
  const UI = new Proxy({}, { get: (_, name) => String(name) });
  function leaf(name) {
    if (name === "react/jsx-runtime")
      return {
        jsx: (type, props) => ({ type, props }),
        jsxs: (type, props) => ({ type, props }),
      };
    if (name === "react")
      return { useState: (value) => [value, () => {}], useMemo: (fn) => fn() };
    if (name === "@/lib/translate") return { default: translator };
    if (name === "frappe-react-sdk")
      return {
        useFrappePostCall: (method) => ({
          call: (args) => {
            calls.push([method, args]);
            throw Error("No undo action allowed");
          },
        }),
        useSWRConfig: () => ({
          mutate: () => {
            throw Error("No cache mutation allowed");
          },
        }),
      };
    if (name === "jotai")
      return {
        useSetAtom: () => () => {
          throw Error("No atom mutation allowed");
        },
        useAtomValue: () => ({ fromDate: "2026-10-01", toDate: "2026-10-21" }),
      };
    if (name === "../BankReconciliation/bankRecAtoms")
      return {
        bankRecActionLog: "ACTION",
        bankRecDateAtom: "DATE",
        bankRecMatchFilters: "FILTERS",
        selectedBankAccountAtom: "BANK",
      };
    if (name === "../BankReconciliation/utils")
      return { useGetBankAccounts: () => ({ banks: [] }) };
    if (name === "@/lib/company") return { getCompanyCurrency: () => "EUR" };
    if (name === "@/lib/numbers")
      return {
        formatCurrency: (...args) => {
          amounts.push(args);
          return args.join(" ");
        },
      };
    if (name === "@/lib/date") return { formatDate: (value) => value };
    if (name === "@/lib/frappe")
      return {
        slug: (value) => value.toLowerCase().replaceAll(" ", "-"),
        getErrorMessage: (value) => value,
      };
    if (name === "@/lib/utils")
      return { cn: (...values) => values.filter(Boolean).join(" ") };
    return UI;
  }
  function load(relative, extra = "") {
    const code = ts.transpileModule(
      fs.readFileSync(path.join(banking, relative), "utf8"),
      {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          jsx: ts.JsxEmit.ReactJSX,
          target: ts.ScriptTarget.ES2022,
        },
      }
    ).outputText;
    const exports = {};
    vm.runInContext(
      `(function(require,exports){${code}\n${extra}\n})`,
      context,
      { filename: relative }
    )(leaf, exports);
    return exports;
  }
  translator = load("src/lib/translate.ts").default;
  const components = load(
    "src/components/features/ActionLog/ActionLogDialogBody.tsx",
    "exports.Row=Row;exports.PaymentEntryDetails=PaymentEntryDetails;exports.CancelActionLogItem=CancelActionLogItem;"
  );
  return { components, calls, amounts };
}
function nodes(node, type) {
  if (Array.isArray(node)) return node.flatMap((child) => nodes(child, type));
  if (!node || typeof node !== "object") return [];
  return [
    ...(node.type === type ? [node] : []),
    ...nodes(node.props?.children, type),
  ];
}
function text(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(text).join("");
  return typeof node === "object" ? text(node.props?.children) : String(node);
}
function fixture(doctype = "Payment Entry") {
  return {
    bankTransaction: {
      name: "BT-RAW",
      company: "COMPANY-RAW",
      deposit: 24,
      date: "2026-10-21",
      bank_account: "Payment Entry",
    },
    voucher: {
      reference_doctype: doctype,
      reference_name: "Payment Entry",
      posting_date: "2026-10-21",
    },
  };
}
for (const [type, doctype, display] of [
  ["payment", "Payment Entry", "Registrazione di pagamento"],
  ["bank_entry", "Journal Entry", "Registrazione contabile"],
  ["transfer", "Payment Entry", "Registrazione di pagamento"],
]) {
  test(`${type} undo heading localizes only the canonical type`, () => {
    const env = environment(),
      item = fixture(doctype),
      original = JSON.stringify(item);
    const tree = env.components.CancelActionLogItem({
      item,
      type,
      timestamp: 1,
    });
    assert.equal(
      text(nodes(tree, "AlertDialogTitle")[0]),
      `Annullare il documento di tipo ${display}?`
    );
    assert.equal(
      text(nodes(tree, "AlertDialogDescription")[0]),
      `Vuoi annullare il documento di tipo ${display} con riferimento Payment Entry?`
    );
    assert.equal(
      nodes(tree, "a")[0].props.href,
      `/desk/${doctype.toLowerCase().replaceAll(" ", "-")}/Payment Entry`
    );
    const action = nodes(tree, "Button").find((button) => button.props.onClick);
    assert.equal(typeof action.props.onClick, "function");
    assert.match(
      action.props.onClick.toString(),
      /voucher_type: item.voucher.reference_doctype/
    );
    assert.match(
      action.props.onClick.toString(),
      /voucher_id: item.voucher.reference_name/
    );
    assert.equal(JSON.stringify(item), original);
    assert.deepEqual(env.calls, []);
  });
}
test("match-specific heading and description remain unchanged without an undo invocation", () => {
  const env = environment(),
    item = fixture();
  const tree = env.components.CancelActionLogItem({
    item,
    type: "match",
    timestamp: 1,
  });
  assert.equal(
    text(nodes(tree, "AlertDialogTitle")[0]),
    messages["Unmatch Transaction?"]
  );
  assert.equal(
    text(nodes(tree, "AlertDialogDescription")[0]),
    messages[
      "Are you sure you want to unmatch the voucher from this transaction?"
    ]
  );
  assert.deepEqual(env.calls, []);
});
test("row prefix translates the type but preserves business identity, amount and canonical link", () => {
  const env = environment(),
    item = fixture("Sales Invoice"),
    original = JSON.stringify(item);
  const tree = env.components.Row({
    item,
    index: 0,
    isLast: true,
    action: { type: "match", timestamp: 1 },
  });
  const anchor = nodes(tree, "a")[0];
  assert.equal(text(anchor), "Fattura di vendita : Payment Entry");
  assert.equal(anchor.props.href, "/desk/sales-invoice/Payment Entry");
  assert.deepEqual(env.amounts, [[24, "EUR"]]);
  assert.equal(JSON.stringify(item), original);
  assert.deepEqual(env.calls, []);
});
test("payment references translate only the invoice type, retaining all dates and amounts", () => {
  const env = environment(),
    item = fixture();
  item.voucher.doc = {
    payment_type: "Receive",
    party_name: "Company",
    paid_from_account_currency: "EUR",
    references: [
      {
        reference_doctype: "Sales Invoice",
        reference_name: 'Company & "quoted"',
        due_date: "2026-11-01",
        bill_no: "Company",
        total_amount: 24,
        allocated_amount: 12,
      },
    ],
  };
  const original = JSON.stringify(item);
  const tree = env.components.PaymentEntryDetails({ item });
  assert.equal(
    text(nodes(tree, "a")[0]),
    'Fattura di vendita: Company & "quoted"'
  );
  assert.equal(
    nodes(tree, "a")[0].props.href,
    '/desk/sales-invoice/Company & "quoted"'
  );
  assert.deepEqual(env.amounts, [
    [24, "EUR"],
    [12, "EUR"],
  ]);
  assert.equal(JSON.stringify(item), original);
});
test("suppressed standard row prefixes and internal-transfer detail branch remain unchanged", () => {
  for (const doctype of ["Payment Entry", "Journal Entry"]) {
    const env = environment(),
      item = fixture(doctype);
    assert.equal(
      text(
        nodes(
          env.components.Row({
            item,
            index: 0,
            isLast: true,
            action: { type: "payment", timestamp: 1 },
          }),
          "a"
        )[0]
      ),
      " Payment Entry"
    );
  }
  const env = environment(),
    item = fixture();
  item.voucher.doc = { payment_type: "Internal Transfer" };
  assert.equal(
    nodes(env.components.PaymentEntryDetails({ item }), "a").length,
    0
  );
});
test("unknown, empty and hostile translations remain text values with canonical route identity", () => {
  for (const dictionary of [
    {},
    { ...messages, "Payment Entry": "" },
    { ...messages, "Payment Entry": '<b>Tipo</b> & "quoted"' },
  ]) {
    const env = environment(dictionary),
      item = fixture();
    const title = text(
      nodes(
        env.components.CancelActionLogItem({
          item,
          type: "payment",
          timestamp: 1,
        }),
        "AlertDialogTitle"
      )[0]
    );
    const display = dictionary["Payment Entry"] || "Payment Entry";
    assert.equal(
      title,
      dictionary["Undo {}?"]
        ? `Annullare il documento di tipo ${display}?`
        : "Undo Payment Entry?"
    );
    assert.deepEqual(env.calls, []);
  }
});
