import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, delimiter, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { test } = require("@playwright/test");
const banking = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../..");
const esbuild = createRequire(resolve(banking, "../../frappe/package.json"))("esbuild");
const TARGETS = { "Select {0}": "Seleziona {0}", "GL Account": "Conto di mastro" };

type BrowserPage = {
  on(event: "pageerror", callback: (error: Error) => void): void;
  route(pattern: string, callback: (route: { fulfill(options: { status: number; contentType: string; body: string }): Promise<unknown> }) => Promise<unknown>): Promise<unknown>;
  goto(url: string): Promise<unknown>;
  addScriptTag(options: { content: string }): Promise<unknown>;
  evaluate<T, A>(callback: (argument: A) => T, argument: A): Promise<Awaited<T>>;
};

declare global {
  interface Window {
    bankingTooltips: {
      mount(kind: string, name: string, messages: Record<string, string>, empty?: boolean): void;
      observations: { mutations: number; calls: number; subscriptions: string[] };
      input: { banks: Array<Record<string, unknown>>; data: Record<string, unknown> };
      selected(): Record<string, unknown> | null;
    };
  }
}

const bundle = (await esbuild.build({
  stdin: {
    loader: "tsx",
    resolveDir: banking,
    contents: `
import React from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { Provider, createStore } from "jotai";
import { MemoryRouter } from "react-router";
import BankPicker from "./src/components/features/BankReconciliation/BankPicker";
import StatementDetails from "./src/components/features/BankStatementImporter/CSV/StatementDetails";
import { selectedBankAccountAtom } from "./src/components/features/BankReconciliation/bankRecAtoms";
import { TooltipProvider } from "./src/components/ui/tooltip";
let root, store;
const observations = { mutations: 0, calls: 0, subscriptions: [] };
window.bankingTooltips = {
  observations, input: { banks: [], data: {} }, selected: () => store.get(selectedBankAccountAtom),
  mount(kind, name, messages, empty) {
    window.frappe._messages = messages;
    window.localStorage.clear();
    if (root) flushSync(() => root.unmount());
    store = createStore();
    const bank = { name: 'BANK-RAW', company: 'Authored Company', bank: 'Authored bank', account_name: name, account: 'GL Account', bank_account_no: 'RAW-IBAN', account_type: 'Authored type', is_default: 0 };
    const data = { doc: { name: 'IMPORT-RAW', company: 'Authored Company', bank_account: 'BANK-RAW', status: 'Completed', file: '/private/files/Authored & statement.csv', number_of_transactions: 0, total_debits: 0, total_credits: 0, total_debit_transactions: 0, total_credit_transactions: 0, closing_balance: 0 }, currency: 'EUR', date_format: 'DD MMMM YYYY', date_format_example: '20 October 2026 & <sample>', conflicting_transactions: [], final_transactions: [] };
    window.bankingTooltips.input = { banks: empty ? [] : [bank], data };
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root'));
    flushSync(() => root.render(<Provider store={store}><MemoryRouter><TooltipProvider>{kind === 'picker' ? <BankPicker /> : <StatementDetails data={data} />}</TooltipProvider></MemoryRouter></Provider>));
  }
};`,
  },
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  jsx: "automatic",
  tsconfig: resolve(banking, "tsconfig.app.json"),
  nodePaths: (process.env.NODE_PATH ?? "").split(delimiter).filter(Boolean),
  define: { "process.env.NODE_ENV": '"development"', "import.meta.glob": "globalThis.bankingLocaleChunks" },
  banner: { js: "globalThis.bankingLocaleChunks = () => ({});" },
  plugins: [{
    name: "external-memory-providers",
    setup(build: {
      onResolve(options: { filter: RegExp }, callback: () => { path: string; namespace: string }): void;
      onLoad(options: { filter: RegExp; namespace?: string }, callback: (args: { path: string }) => { contents: string; loader: string } | undefined): void;
    }) {
      build.onResolve({ filter: /^frappe-react-sdk$/ }, () => ({ path: "frappe-sdk-memory", namespace: "memory" }));
      build.onLoad({ filter: /[/\\]BankReconciliation[/\\]utils\.ts$/ }, () => ({
        contents: `export function useGetBankAccounts() { return { banks: window.bankingTooltips.input.banks, isLoading: false, error: undefined }; }
export function useGetUnreconciledTransactions() { return { mutate() { window.bankingTooltips.observations.mutations += 1; } }; }`, loader: "ts",
      }));
      build.onLoad({ filter: /.*/, namespace: "memory" }, () => ({
        contents: `export function useFrappePostCall() { return { call() { window.bankingTooltips.observations.calls += 1; throw Error('Unexpected transport action'); }, loading: false, error: undefined }; }
export function useFrappeEventListener(name) { window.bankingTooltips?.observations.subscriptions.push(name); }`, loader: "js",
      }));
      build.onLoad({ filter: /BankReconciliation[/\\]utils\.tsx$/ }, () => undefined);
      build.onLoad({ filter: /[/\\]lib[/\\]date\.ts$/ }, (args) => ({ contents: readFileSync(args.path, "utf8"), loader: "ts" }));
    },
  }],
})).outputFiles[0].text;

const errors = new WeakMap<object, Error[]>();
test.beforeEach(async ({ page }: { page: BrowserPage }) => {
  const found: Error[] = [];
  errors.set(page, found);
  page.on("pageerror", error => found.push(error));
});
test.afterEach(async ({ page }: { page: BrowserPage }) => {
  assert.deepEqual(errors.get(page), []);
  assert.equal(await page.evaluate(() => window.bankingTooltips.observations.calls, undefined), 0);
});

async function mount(page: BrowserPage, kind: string, name = 'Authored "bank" & account', messages: Record<string, string> = TARGETS, empty = false) {
  await page.route("**/*", route => route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><html><body></body></html>" }));
  await page.goto("http://banking-memory.example.test/");
  await page.addScriptTag({ content: 'window.locals = {":Currency":{EUR:{symbol:"€",number_format:"#,###.##"}}};window.frappe = {_messages:{}, boot:{lang:"it",user:{defaults:{company:"Authored Company"}},sysdefaults:{number_format:"#,###.##",float_precision:2},currencies:{EUR:{symbol:"€",fraction_units:100}}}};' });
  await page.addScriptTag({ content: bundle });
  await page.evaluate(({ kind, name, messages, empty }) => window.bankingTooltips.mount(kind, name, messages, empty), { kind, name, messages, empty });
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => requestAnimationFrame(() => done()))), undefined);
}

test("bank card title localizes the complete action while business identity stays authored", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "picker");
  const result = await page.evaluate(() => ({ title: document.querySelector('[role="button"]')?.getAttribute("title"), business: document.querySelector('[role="button"] span')?.textContent, account: document.querySelector('span[title]')?.textContent }), undefined);
  assert.equal(result.title, 'Seleziona Authored "bank" & account');
  assert.equal(result.business, 'Authored "bank" & account');
  assert.equal(result.account, "GL Account");
});

test("statement account tooltip is translated while raw account and parser example stay exact", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "statement");
  const result = await page.evaluate(() => ({ tooltip: Array.from(document.querySelectorAll("span[title]"), node => [node.getAttribute("title"), node.textContent]), text: document.body.textContent, injected: document.querySelectorAll("sample").length, back: document.querySelector("a")?.getAttribute("href"), data: window.bankingTooltips.input.data }), undefined);
  assert.deepEqual(result.tooltip, [["Conto di mastro", "GL Account"]]);
  assert.ok(result.text?.includes("20 October 2026 & <sample>"));
  assert.equal(result.injected, 0);
  assert.equal(result.back, "/statement-importer");
  assert.equal((result.data.doc as Record<string, unknown>).name, "IMPORT-RAW");
  assert.equal((result.data.doc as Record<string, unknown>).status, "Completed");
  assert.equal(result.data.date_format, "DD MMMM YYYY");
});

test("hostile account caption is escaped at both title and text boundaries", async ({ page }: { page: BrowserPage }) => {
  const hostile = '<img id="injected" src=x> & "quoted"';
  await mount(page, "picker", hostile);
  const result = await page.evaluate(() => ({ title: document.querySelector('[role="button"]')?.getAttribute("title"), text: document.querySelector('[role="button"]')?.textContent, injected: document.querySelectorAll("#injected").length }), undefined);
  assert.equal(result.title, `Seleziona ${hostile}`);
  assert.ok(result.text?.includes(hostile));
  assert.equal(result.injected, 0);
});

test("selection still passes the exact canonical bank object and one memory refresh", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "picker");
  const before = await page.evaluate(() => JSON.stringify(window.bankingTooltips.input), undefined);
  await page.evaluate(() => (document.querySelector('[role="button"]') as HTMLElement).click(), undefined);
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => done())), undefined);
  const result = await page.evaluate(() => ({ same: window.bankingTooltips.selected() === window.bankingTooltips.input.banks[0], selected: window.bankingTooltips.selected(), mutations: window.bankingTooltips.observations.mutations, input: JSON.stringify(window.bankingTooltips.input) }), undefined);
  assert.equal(result.same, true);
  assert.equal(result.selected?.name, "BANK-RAW");
  assert.equal(result.selected?.bank_account_no, "RAW-IBAN");
  assert.equal(result.mutations, 1);
  assert.equal(result.input, before);
});

test("missing dictionary retains English source and authored collision names", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "picker", "GL Account", {});
  assert.equal(await page.evaluate(() => document.querySelector('[role="button"]')?.getAttribute("title"), undefined), "Select GL Account");
  await mount(page, "statement", "GL Account", {});
  assert.equal(await page.evaluate(() => document.querySelector('span[title]')?.getAttribute("title"), undefined), "GL Account");
});

test("an empty bank result creates no false selection tooltip or action", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "picker", "Ignored", TARGETS, true);
  const result = await page.evaluate(() => ({ cards: document.querySelectorAll('[role="button"]').length, mutations: window.bankingTooltips.observations.mutations, selected: window.bankingTooltips.selected(), link: document.querySelector("a")?.getAttribute("href") }), undefined);
  assert.deepEqual(result, { cards: 0, mutations: 0, selected: null, link: "/desk/bank-account?company=Authored%20Company&is_company_account=1" });
});
