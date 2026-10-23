const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { test } = require("node:test");
const vm = require("node:vm");

const root =
  process.env.ERPNEXT_SOURCE_ROOT ||
  path.resolve(__dirname, "../../../../../..");
const dependencies = createRequire(
  path.join(
    process.env.BANKING_DEPENDENCY_ROOT || path.join(root, "banking"),
    "package.json"
  )
);
const ts = dependencies("typescript");
const React = dependencies("react");
const { renderToStaticMarkup } = dependencies("react-dom/server");
const FrappeContext = React.createContext(null);
const requests = [];
const deleted = [];
const buttons = [];
let mutations = 0;
const records = [
  {
    name: "selected-day",
    bank_account: "Example Bank",
    date: "2026-10-20",
    balance: 120,
  },
  {
    name: "previous-day",
    bank_account: "Example Bank",
    date: "2026-10-19",
    balance: 100,
  },
  {
    name: "future-day",
    bank_account: "Example Bank",
    date: "2026-10-21",
    balance: 999,
  },
  {
    name: "other-account",
    bank_account: "Other Bank",
    date: "2026-10-20",
    balance: 999,
  },
];
const bankAccount = {
  name: "Example Bank",
  company: "Example Company",
  account_currency: "EUR",
};
const window = {
  frappe: {
    boot: { lang: "it", user: { defaults: { date_format: "dd/mm/yyyy" } } },
    _messages: {
      "Balances as per bank statement on or before {0}":
        "Saldi dell’estratto conto fino al {0} incluso",
      "Balances as per bank statement before {0}":
        "Saldi dell’estratto conto precedenti al {0}",
    },
  },
};

function load(relative, requireModule, extra = "") {
  const filename = path.join(root, relative);
  const source = fs
    .readFileSync(filename, "utf8")
    .replace(/import\.meta\.glob(?:<[^>]+>)?\([\s\S]*?\);/, "{};");
  const result = ts.transpileModule(source + extra, {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2020,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  });
  assert.deepEqual(result.diagnostics, []);
  const exports = {};
  vm.runInNewContext(
    result.outputText,
    { exports, require: requireModule, window },
    { filename }
  );
  return exports;
}
const translate = load("banking/src/lib/translate.ts", dependencies).default;
const dates = load("banking/src/lib/date.ts", (name) =>
  name === "@/lib/translate"
    ? { __esModule: true, default: translate }
    : dependencies(name)
);
const tags = {
  Table: "table",
  TableHeader: "thead",
  TableRow: "tr",
  TableHead: "th",
  TableBody: "tbody",
  TableCell: "td",
  Button: "button",
  Trash2: "span",
  Separator: "hr",
};
const presentation = Object.fromEntries(
  Object.entries(tags).map(([name, tag]) => [
    name,
    ({ children, onClick, title, type }) => {
      if (name === "Button") buttons.push({ onClick, title });
      return React.createElement(tag, { onClick, title, type }, children);
    },
  ])
);
function useFrappeGetDocList(doctype, options) {
  requests.push({ doctype, options });
  const data = records
    .filter((record) =>
      options.filters.every(([field, operator, value]) => {
        assert.ok(["=", "<="].includes(operator));
        return operator === "="
          ? record[field] === value
          : record[field] <= value;
      })
    )
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, options.limit);
  return {
    data,
    mutate: () => {
      mutations++;
    },
  };
}
const component = load(
  "banking/src/components/features/BankReconciliation/BankBalance.tsx",
  (name) => {
    if (name === "react" || name === "react/jsx-runtime")
      return dependencies(name);
    if (name === "frappe-react-sdk")
      return { FrappeContext, useFrappeGetDocList };
    if (name === "@/lib/translate")
      return { __esModule: true, default: translate };
    if (name === "@/lib/date") return dates;
    if (name === "@/lib/numbers")
      return { flt: Number, formatCurrency: (value) => `EUR ${value}` };
    if (name === "sonner") return { toast: { promise: (promise) => promise } };
    return presentation;
  },
  "\nexport { ClosingBalancesList };\n"
);
const db = {
  deleteDoc: async (doctype, name) => {
    deleted.push({ doctype, name });
  },
};
const element = () =>
  React.createElement(
    FrappeContext.Provider,
    { value: { db } },
    React.createElement(component.ClosingBalancesList, {
      bankAccount,
      date: "2026-10-20",
    })
  );

test("inclusive query and translated heading agree on the selected day", () => {
  const html = renderToStaticMarkup(element());
  const date = dates.formatDate("2026-10-20", "Do MMM YYYY");
  assert.ok(html.includes(`Saldi dell’estratto conto fino al ${date} incluso`));
  assert.ok(html.includes("EUR 120"));
  assert.ok(html.includes("EUR 100"));
  assert.ok(!html.includes("EUR 999"));
  assert.ok(html.indexOf("EUR 120") < html.indexOf("EUR 100"));
  const request = JSON.parse(JSON.stringify(requests.at(-1)));
  assert.deepEqual(request, {
    doctype: "Bank Account Balance",
    options: {
      filters: [
        ["bank_account", "=", "Example Bank"],
        ["date", "<=", "2026-10-20"],
      ],
      orderBy: { field: "date", order: "desc" },
      fields: ["date", "balance", "name"],
      limit: 10,
    },
  });
});

test("the existing row deletion callback keeps its canonical document identity", async () => {
  buttons.length = 0;
  renderToStaticMarkup(element());
  const button = buttons.find((item) => item.title === "Delete");
  assert.ok(button);
  button.onClick();
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(deleted, [
    { doctype: "Bank Account Balance", name: "selected-day" },
  ]);
  assert.equal(mutations, 1);
});
