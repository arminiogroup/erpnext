const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { test } = require("node:test");
const vm = require("node:vm");
const root =
  process.env.ERPNEXT_SOURCE_ROOT ||
  path.resolve(__dirname, "../../../../../../..");
const dependencies = createRequire(
  path.join(
    process.env.BANKING_DEPENDENCY_ROOT || path.join(root, "banking"),
    "package.json"
  )
);
const ts = dependencies("typescript");
const React = dependencies("react");
const { renderToStaticMarkup } = dependencies("react-dom/server");
const buttons = [];
const importCalls = [];
const selectedDates = [];
const navigations = [];
const window = {
  frappe: {
    boot: { lang: "it", user: { defaults: { date_format: "dd/mm/yyyy" } } },
    _messages: { "Example: {0}": "Esempio: {0}" },
  },
};
function load(relative, resolver) {
  const filename = path.join(root, relative);
  const source = fs
    .readFileSync(filename, "utf8")
    .replace(/import\.meta\.glob(?:<[^>]+>)?\([\s\S]*?\);/, "__localeLoaders;");
  const result = ts.transpileModule(source, {
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
    {
      exports,
      require: resolver,
      window,
      __localeLoaders: {
        "/node_modules/dayjs/locale/it.js": async () =>
          dependencies("dayjs/locale/it"),
      },
    },
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
const componentCache = new Map();
const presentation = new Proxy(
  { __esModule: true },
  {
    get(target, name) {
      if (name === "__esModule") return true;
      if (!componentCache.has(name))
        componentCache.set(name, (props) => {
          if (name === "Button") buttons.push(props);
          const tags = {
            Table: "table",
            TableBody: "tbody",
            TableRow: "tr",
            TableHead: "th",
            TableCell: "td",
            H2: "h2",
            H3: "h3",
            Paragraph: "p",
            Button: "button",
          };
          return React.createElement(
            tags[name] || "div",
            { onClick: props.onClick },
            props.children
          );
        });
      return componentCache.get(name);
    },
  }
);
const StatementDetails = load(
  "banking/src/components/features/BankStatementImporter/CSV/StatementDetails.tsx",
  (name) => {
    if (name === "react" || name === "react/jsx-runtime")
      return dependencies(name);
    if (name === "@/lib/translate")
      return { __esModule: true, default: translate };
    if (name === "@/lib/date") return dates;
    if (name === "@/lib/numbers")
      return { flt: Number, formatCurrency: (value) => String(value) };
    if (name === "@/lib/file")
      return { getFileExtension: (file) => file.split(".").at(-1) };
    if (name === "frappe-react-sdk")
      return {
        useFrappeEventListener: () => {},
        useFrappePostCall: (method) => {
          assert.equal(method, "run_doc_method");
          return {
            loading: false,
            call: async (payload) => {
              importCalls.push(payload);
              return {
                docs: [{ start_date: "2026-10-20", end_date: "2026-10-21" }],
              };
            },
          };
        },
      };
    if (name === "react-router")
      return {
        ...presentation,
        Link: presentation.Link,
        useNavigate: () => (value) => navigations.push(value),
      };
    if (name === "jotai")
      return { useSetAtom: () => (value) => selectedDates.push(value) };
    if (name === "@/components/ui/direction")
      return { useDirection: () => "ltr" };
    if (name === "../../BankReconciliation/utils")
      return { useGetBankAccounts: () => ({ banks: [] }) };
    if (name === "sonner")
      return { toast: { success: () => {}, error: () => {} } };
    return presentation;
  }
).default;
function fixture(example = "20 October 2026") {
  return {
    doc: {
      file: "/private/files/example.csv",
      status: "Not Started",
      bank_account: "Example Bank",
      start_date: "2026-10-20",
      end_date: "2026-10-21",
      end_balance: 10,
      detected_date_format: "%d %B %Y",
    },
    date_format: "DD MMMM YYYY",
    date_format_example: example,
    currency: "EUR",
    conflicting_transactions: [],
    final_transactions: [
      {
        date: "2026-10-20",
        withdrawal: 10,
        deposit: 0,
        description: "Example",
        reference: "Example reference",
      },
    ],
  };
}
function render(data) {
  buttons.length = 0;
  return renderToStaticMarkup(React.createElement(StatementDetails, { data }));
}
function dateFormatRow(html) {
  return [...html.matchAll(/<tr>[\s\S]*?<\/tr>/g)]
    .map((match) => match[0])
    .find((row) => row.includes("Detected Date Format"));
}

test("Italian display keeps the server parser example while translating its whole frame", async () => {
  await dates.initializeDateLocale();
  const html = render(fixture());
  const row = dateFormatRow(html);
  assert.ok(row.includes("DD MMMM YYYY"));
  assert.ok(row.includes("Esempio: 20 October 2026"));
  assert.ok(!row.includes("ottobre"));
  assert.ok(html.includes("ottobre"));
});

test("cached responses without an example show the unchanged pattern alone", () => {
  const data = fixture();
  delete data.date_format_example;
  const row = dateFormatRow(render(data));
  assert.ok(row.includes("DD MMMM YYYY"));
  assert.ok(
    !row.includes("Esempio:") &&
      !row.includes("e.g.") &&
      !row.includes("ottobre")
  );
});

test("supplied examples remain opaque escaped text and import callbacks remain canonical", async () => {
  const data = fixture("20 October <2026> & 13:24:56");
  const row = dateFormatRow(render(data));
  assert.ok(row.includes("Esempio: 20 October &lt;2026&gt; &amp; 13:24:56"));
  const button = buttons.find((props) => props.onClick);
  assert.ok(button);
  button.onClick();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(importCalls.length, 1);
  assert.strictEqual(importCalls[0].docs, data.doc);
  assert.equal(importCalls[0].method, "insert_transactions");
  assert.deepEqual(JSON.parse(JSON.stringify(selectedDates)), [
    { fromDate: "2026-10-20", toDate: "2026-10-21" },
  ]);
  assert.deepEqual(navigations, ["/"]);
});
