import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function load() {
  class Element {
    constructor(html = "") {
      this.htmlText = html;
      this.children = [];
      this.handlers = {};
      this.attrs = {};
      this.values = {};
      this.length = 1;
    }
    append(...items) {
      this.children.push(...items);
      return this;
    }
    appendTo(target) {
      target.append(this);
      return this;
    }
    on(name, fn) {
      this.handlers[name] = fn;
      return this;
    }
    attr(name, value) {
      if (value === undefined) return this.attrs[name];
      this.attrs[name] = value;
      return this;
    }
    text(value) {
      this.textValue = value;
      return this;
    }
    empty() {
      this.children = [];
      return this;
    }
    find(selector) {
      return (this.values[selector] ??= new Element());
    }
    addClass() {
      return this;
    }
    removeClass() {
      return this;
    }
    html(value) {
      this.htmlText = value;
      return this;
    }
    data() {
      return this;
    }
  }
  const $ = Object.assign(
    (value) => (value instanceof Element ? value : new Element(value)),
    { extend: Object.assign }
  );
  const host = {
    $,
    jQuery: $,
    console,
    erpnext: {},
    frappe: {
      pages: { "production-plan-visualizer": {} },
      ui: {},
      router: { slug: (s) => s.toLowerCase().replaceAll(" ", "-") },
      utils: {
        escape_html: (s) =>
          String(s)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll('"', "&quot;"),
        icon: () => "",
      },
      _messages: {
        None: "Nessuna",
        "None:Production Plan Documents": "Nessun documento",
        Open: "Aperto",
        "Open:Access": "Apri",
      },
      db: {},
    },
  };
  host.number_systems = {};
  host.deep_equal = () => {};
  host.window = host;
  host.frappe.provide = (path) => {
    let item = host;
    for (const key of path.split(".")) item = item[key] ??= {};
  };
  vm.createContext(host);
  const frappeRoot = new URL(
    "../../../../../frappe/frappe/public/js/frappe/",
    import.meta.url
  );
  for (const p of [
    "format.js",
    "translate.js",
    "ui/components/utils.js",
    "utils/utils.js",
    "ui/components/badge.js",
    "ui/components/button.js",
  ]) {
    const code = readFileSync(new URL(p, frappeRoot), "utf8")
      .replace(/^import .+;\n/gm, "")
      .replace(/^export default .+;\n/gm, "")
      .replace(/^export /gm, "");
    vm.runInContext(
      p.includes("badge.js") || p.includes("button.js")
        ? "(function(){" + code + "})()"
        : code,
      host,
      { filename: p }
    );
  }
  vm.runInContext(
    readFileSync(
      new URL("production_plan_visualizer.js", import.meta.url),
      "utf8"
    ),
    host
  );
  const view = Object.create(host.erpnext.ProductionPlanVisualizer.prototype);
  view.drawer = new Element();
  view.backdrop = new Element();
  host.frappe.ui.skeleton = { html: () => "<loading>" };
  const routes = [];
  host.frappe.set_route = (...args) => routes.push(args);
  return { view, host, Element, routes };
}

test("empty document lists use document context while populated names and encoded URLs stay raw", () => {
  const { view, host, Element } = load();
  for (const docs of [undefined, []]) {
    const target = new Element();
    view.append_document_chips(target, docs);
    assert.match(target.children[0], />Nessun documento</);
  }
  const target = new Element();
  const doc = {
    doctype: "Purchase Order",
    name: "None / caffè <x>",
    status: "Open",
  };
  view.append_document_chips(target, [doc]);
  const chip = target.children[0];
  assert(
    chip.htmlText.includes(
      "/app/purchase-order/None%20%2F%20caff%C3%A8%20%3Cx%3E"
    )
  );
  assert(chip.children[0].htmlText.includes("None / caffè &lt;x&gt;"));
  assert.equal(doc.name, "None / caffè <x>");
  const opened = [];
  view.show_document = (...args) => opened.push(args);
  for (const modifier of [
    { ctrlKey: true },
    { metaKey: true },
    { shiftKey: true },
    { which: 2 },
  ])
    chip.handlers.click({
      ...modifier,
      preventDefault: () => assert.fail("modified click must follow href"),
    });
  assert.equal(opened.length, 0);
  let prevented = false;
  chip.handlers.click({ preventDefault: () => (prevented = true) });
  assert(prevented);
  assert.deepEqual(opened, [["Purchase Order", doc.name]]);
  assert.equal(host.__("None"), "Nessuna");
});

test("drawer action localizes Open and preserves exact Form route and close", () => {
  const { view, host, routes } = load();
  let closes = 0;
  view.close_document = () => closes++;
  const [open, close] = view.drawer_actions("Work Order", "WO / caffè");
  assert.match(open.htmlText, />Apri</);
  open.handlers.click({});
  close.handlers.click({});
  assert.deepEqual(routes, [["Form", "Work Order", "WO / caffè"]]);
  assert.equal(closes, 1);
  assert.equal(host.__("Open"), "Aperto");
});

test("drawer ignores stale reads and preserves current identity", async () => {
  const { view, host } = load();
  const pending = [];
  const rendered = [];
  host.frappe.db.get_doc = (doctype, name) =>
    new Promise((resolve) => pending.push({ doctype, name, resolve }));
  view.render_document = (doc, doctype) => rendered.push([doc.name, doctype]);
  view.show_document("Work Order", "WO-A");
  view.show_document("Work Order", "WO-B");
  pending[0].resolve({ name: "WO-A" });
  await Promise.resolve();
  assert.equal(rendered.length, 0);
  pending[1].resolve({ name: "WO-B" });
  await Promise.resolve();
  assert.deepEqual(rendered, [["WO-B", "Work Order"]]);
});
