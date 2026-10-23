import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { dirname, delimiter, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { test } = require("@playwright/test");
const banking = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const esbuild = createRequire(resolve(banking, "../../frappe/package.json"))("esbuild");
const TARGETS = {
  Breadcrumb: "Percorso di navigazione",
  Notifications: "Notifiche",
  Suggestions: "Suggerimenti",
  Close: "Chiudi",
  "Select all rows": "Seleziona tutte le righe",
  "Select row": "Seleziona la riga",
  "Resize column": "Ridimensiona la colonna",
  "No data": "Nessun dato",
};

type BrowserPage = {
  on(event: "pageerror", callback: (error: Error) => void): void;
  setContent(html: string): Promise<unknown>;
  addScriptTag(options: { content: string }): Promise<unknown>;
  evaluate<T, A>(callback: (argument: A) => T, argument: A): Promise<Awaited<T>>;
};

declare global {
  interface Window {
    bankingDefaults: {
      mount(kind: string, props: Record<string, unknown>, messages: Record<string, string>): void;
      observations: { selections: unknown[]; sizing: unknown[]; command: string[]; dialogs: boolean[] };
    };
  }
}

const bundle = esbuild.buildSync({
  stdin: {
    resolveDir: banking,
    loader: "tsx",
    contents: `
import React, { useState, useMemo } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { Breadcrumb, BreadcrumbList, BreadcrumbItem } from "./src/components/ui/breadcrumb";
import { Toaster } from "./src/components/ui/sonner";
import { toast } from "sonner";
import { Command, CommandInput, CommandList, CommandItem } from "./src/components/ui/command";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "./src/components/ui/dialog";
import { ListView } from "./src/components/ui/list-view";
import { TooltipProvider } from "./src/components/ui/tooltip";
let root;
const observations = { selections: [], sizing: [], command: [], dialogs: [] };
function Table({ options }) {
  const [selected, setSelected] = useState({});
  const [sizes, setSizes] = useState({});
  const data = useMemo(() => options.empty ? [] : [{ name: 'RAW-1', caption: 'Select row' }, { name: 'RAW-2', caption: '<b>& Authored' }], [options.empty]);
  const columns = useMemo(() => [{ accessorKey: 'caption', header: 'Raw caption', size: 150 }], []);
  const props = { ...options };
  delete props.empty;
  if (props.emptyState === 'node') props.emptyState = <span data-authored="true">Authored &lt;empty&gt;</span>;
  return <ListView data={data} columns={columns}
    getRowId={row => row.name} rowSelection={selected} onRowSelectionChange={update => {
      setSelected(previous => { const next = typeof update === 'function' ? update(previous) : update; observations.selections.push(next); return next; });
    }} columnSizing={sizes} onColumnSizingChange={update => {
      setSizes(previous => { const next = typeof update === 'function' ? update(previous) : update; observations.sizing.push(next); return next; });
    }} {...props} />;
}
window.bankingDefaults = {
  observations,
  mount(kind, props, messages) {
    window.frappe = { _messages: messages, boot: { lang: 'it' } };
    if (root) flushSync(() => root.unmount());
    document.body.innerHTML = '<div id="root" style="width:600px;height:420px"></div>';
    root = createRoot(document.getElementById('root'));
    const component = kind === 'breadcrumb' ? <Breadcrumb {...props}><BreadcrumbList><BreadcrumbItem><a href="/desk">Authored route</a></BreadcrumbItem></BreadcrumbList></Breadcrumb>
      : kind === 'toaster' ? <Toaster {...props} />
      : kind === 'command' ? <Command><CommandInput /><CommandList {...props}><CommandItem value="ALPHA_RAW" onSelect={value => observations.command.push(value)}>Alpha</CommandItem><CommandItem value="BETA_RAW" onSelect={value => observations.command.push(value)}>Beta</CommandItem></CommandList></Command>
      : kind === 'dialog' ? <Dialog defaultOpen onOpenChange={value => observations.dialogs.push(value)}><DialogContent showCloseButton={props.showCloseButton ?? true}><DialogHeader><DialogTitle>Authored title</DialogTitle><DialogDescription>Authored description</DialogDescription></DialogHeader><DialogFooter showCloseButton={props.footerClose === true}>{props.authored ? <button>Authored close</button> : null}</DialogFooter></DialogContent></Dialog>
      : <Table options={props} />;
    flushSync(() => root.render(<TooltipProvider>{component}</TooltipProvider>));
    if (kind === 'toaster' && props.localToast) toast('Authored <toast> & title', { description: 'Authored body', duration: Infinity });
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
  define: { "process.env.NODE_ENV": '"development"' },
}).outputFiles[0].text;

async function mount(page: BrowserPage, kind: string, props: Record<string, unknown> = {}, messages: Record<string, string> = TARGETS) {
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({ content: bundle });
  await page.evaluate(({ kind, props, messages }) => {
    window.bankingDefaults.mount(kind, props, messages);
  }, { kind, props, messages });
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => requestAnimationFrame(() => done()))), undefined);
}

const errors = new WeakMap<object, Error[]>();
test.beforeEach(async ({ page }: { page: BrowserPage }) => {
  const found: Error[] = [];
  errors.set(page, found);
  page.on("pageerror", error => found.push(error));
});
test.afterEach(async ({ page }: { page: BrowserPage }) => {
  assert.deepEqual(errors.get(page), []);
});

const attr = (page: BrowserPage, selector: string, attribute: string) => page.evaluate(
  ({ selector, attribute }) => document.querySelector(selector)?.getAttribute(attribute), { selector, attribute });

test("breadcrumb has a translated accessible name and canonical route", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "breadcrumb");
  assert.equal(await attr(page, "nav", "aria-label"), "Percorso di navigazione");
  assert.equal(await attr(page, "nav", "data-slot"), "breadcrumb");
  assert.equal(await attr(page, "a", "href"), "/desk");
});

test("breadcrumb explicit authored and empty labels override the default", async ({ page }: { page: BrowserPage }) => {
  for (const label of ["Authored <nav> & name", ""]) {
    await mount(page, "breadcrumb", { "aria-label": label });
    assert.equal(await attr(page, "nav", "aria-label"), label);
    assert.equal(await page.evaluate(() => document.querySelectorAll("nav nav").length, undefined), 0);
  }
});

test("Sonner keeps the real shortcut and local toast data under a translated region name", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "toaster", { localToast: true });
  assert.equal(await attr(page, "section", "aria-label"), "Notifiche alt+T");
  const result = await page.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { altKey: true, code: "KeyT", bubbles: true }));
    return { live: document.querySelector("section")?.getAttribute("aria-live"), title: document.querySelector("[data-title]")?.textContent, body: document.querySelector("[data-description]")?.textContent, close: document.querySelectorAll("[data-close-button]").length };
  }, undefined);
  assert.deepEqual(result, { live: "polite", title: "Authored <toast> & title", body: "Authored body", close: 0 });
});

test("Sonner explicit labels and hotkeys retain caller ownership", async ({ page }: { page: BrowserPage }) => {
  for (const label of ["Authored region", ""]) {
    await mount(page, "toaster", { containerAriaLabel: label, hotkey: ["ctrlKey", "KeyK"] });
    assert.equal(await attr(page, "section", "aria-label"), `${label} ctrl+K`);
  }
});

test("cmdk Suggestions is localized without changing filtering or selection values", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "command");
  assert.equal(await attr(page, "[role=listbox]", "aria-label"), "Suggerimenti");
  const result = await page.evaluate(() => {
    const input = document.querySelector("input")!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "Beta");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    return input.value;
  }, undefined);
  assert.equal(result, "Beta");
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => done())), undefined);
  const selected = await page.evaluate(() => {
    document.querySelector("input")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    return window.bankingDefaults.observations.command;
  }, undefined);
  assert.deepEqual(selected, ["BETA_RAW"]);
});

test("cmdk supplied empty and authored suggestion labels stay exact", async ({ page }: { page: BrowserPage }) => {
  for (const label of ["Authored choices", ""]) {
    await mount(page, "command", { label });
    assert.equal(await attr(page, "[role=listbox]", "aria-label"), label);
  }
});

test("Radix dialog default close name is translated and closes only its memory dialog", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "dialog");
  assert.equal(await page.evaluate(() => document.querySelector("[role=dialog] button")?.textContent, undefined), "Chiudi");
  await page.evaluate(() => {
    (document.querySelector("[role=dialog] button") as HTMLButtonElement).click();
  }, undefined);
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => done())), undefined);
  const result = await page.evaluate(() => window.bankingDefaults.observations.dialogs, undefined);
  assert.deepEqual(result, [false]);
});

test("dormant DialogFooter close uses the same translator while explicit content stays authored", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "dialog", { showCloseButton: false, footerClose: true, authored: true });
  const result = await page.evaluate(() => Array.from(document.querySelectorAll("[role=dialog] button"), node => node.textContent), undefined);
  assert.deepEqual(result, ["Authored close", "Chiudi"]);
});

test("list defaults localize selection and resize names with canonical selection payloads", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "table", { enableRowSelection: true });
  const result = await page.evaluate(() => ({ names: Array.from(document.querySelectorAll("[role=checkbox]"), node => node.getAttribute("aria-label")), resize: document.querySelector("[role=separator]")?.getAttribute("aria-label"), raw: document.querySelector("[role=grid]")?.textContent }), undefined);
  assert.deepEqual(result.names, ["Seleziona tutte le righe", "Seleziona la riga", "Seleziona la riga"]);
  assert.equal(result.resize, "Ridimensiona la colonna");
  assert.ok(result.raw?.includes("Select row"));
  assert.ok(result.raw?.includes("<b>& Authored"));
  await page.evaluate(() => {
    (document.querySelector("[role=checkbox]") as HTMLButtonElement).click();
  }, undefined);
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => done())), undefined);
  const selections = await page.evaluate(() => window.bankingDefaults.observations.selections, undefined);
  assert.deepEqual(selections[selections.length - 1], { "RAW-1": true, "RAW-2": true });
});

test("list nullish default is translated but custom empty values and content stay exact", async ({ page }: { page: BrowserPage }) => {
  for (const emptyState of [undefined, null, "", false, 0, "Authored empty", "node"]) {
    const props: Record<string, unknown> = { empty: true };
    if (emptyState !== undefined) props.emptyState = emptyState;
    await mount(page, "table", props);
    const text = await page.evaluate(() => document.getElementById("root")?.textContent, undefined);
    const expected = emptyState == null ? "Nessun dato" : emptyState === "node" ? "Authored <empty>" : emptyState === false ? "" : String(emptyState);
    assert.equal(text, expected);
  }
});

test("table disabled features and missing translations preserve source fallback", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "table", { enableRowSelection: false, enableColumnResizing: false });
  assert.equal(await page.evaluate(() => document.querySelectorAll("[role=checkbox],[role=separator]").length, undefined), 0);
  for (const [kind, selector, expected] of [["breadcrumb", "nav", "Breadcrumb"], ["toaster", "section", "Notifications alt+T"], ["command", "[role=listbox]", "Suggestions"]]) {
    await mount(page, kind, {}, {});
    assert.equal(await attr(page, selector, "aria-label"), expected);
  }
});


test("real column resizing preserves numeric sizing and removes temporary pointer classes", async ({ page }: { page: BrowserPage }) => {
  await mount(page, "table", { enableRowSelection: false });
  const before = await page.evaluate(() => document.querySelector("[role=grid]")?.textContent, undefined);
  await page.evaluate(() => {
    const separator = document.querySelector("[role=separator]")!;
    separator.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, clientX: 100, button: 0, buttons: 1 }));
  }, undefined);
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => done())), undefined);
  await page.evaluate(() => {
    document.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 140, buttons: 1 }));
  }, undefined);
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => done())), undefined);
  await page.evaluate(() => {
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, clientX: 140, button: 0 }));
  }, undefined);
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => done())), undefined);
  const result = await page.evaluate(() => ({ sizing: window.bankingDefaults.observations.sizing, text: document.querySelector("[role=grid]")?.textContent, classes: document.body.className }), undefined);
  assert.deepEqual(result.sizing[result.sizing.length - 1], { caption: 190 });
  assert.equal(result.text, before);
  assert.equal(result.classes, "");
});
