import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const { JSDOM } = require("jsdom");
const { parse } = require("@babel/parser");
const erp = fileURLToPath(new URL("../../../", import.meta.url));
const framework = fileURLToPath(
  new URL("../../../../frappe/", import.meta.url)
);
const italian = {
  product_bundle:
    "<h3>Informazioni sul kit di prodotti</h3>\n\n<p>Raggruppa più <b>articoli</b> in un altro <b>articolo</b>. È utile quando vendi un insieme di <b>articoli</b> come kit e gestisci le giacenze dei singoli <b>articoli</b>, ma non dell’<b>articolo</b> che rappresenta il kit.</p>\n<p>Per l’<b>articolo</b> del kit, imposta <code>Gestisci le giacenze</code> su <b>No</b>. Per venderlo, imposta <code>Consenti vendita</code> su <b>Sì</b>.</p>\n<h4>Esempio:</h4>\n<p>Se vendi portatili e zaini anche separatamente e offri un prezzo speciale per l’acquisto congiunto, puoi creare un nuovo articolo per il kit Portatile + Zaino.</p>",
  terms_and_conditions:
    '<h4>Esempio di termini e condizioni standard</h4>\n\n<pre>Condizioni di consegna per l’ordine numero {{ name }}\n\n-Data dell’ordine : {{ transaction_date }} \n-Data di consegna prevista : {{ delivery_date }}\n</pre>\n\n<h4>Come trovare i nomi dei campi</h4>\n\n<p>Nel modello dei termini e condizioni puoi usare i nomi dei campi del documento a cui vengono applicate le condizioni. Apri Personalizza modulo e seleziona il tipo di documento (ad esempio, Ordine di vendita) per trovare i nomi dei campi.</p>\n\n<h4>Modelli</h4>\n\n<p>I modelli vengono compilati con il linguaggio di template Jinja. Per saperne di più su Jinja, <a class="strong" href="https://jinja.palletsprojects.com/en/stable/templates/">consulta questa documentazione.</a></p>',
};
const tokens = ["{{ name }}", "{{ transaction_date }}", "{{ delivery_date }}"];

function metadata(module, name) {
  return JSON.parse(
    readFileSync(
      `${erp}/erpnext/${module}/doctype/${name}/${name}.json`,
      "utf8"
    )
  );
}

function definition(kind) {
  const meta =
    kind === "product_bundle"
      ? metadata("selling", kind)
      : metadata("setup", kind);
  const field =
    kind === "product_bundle" ? "about" : "terms_and_conditions_help";
  return {
    ...meta.fields.find((df) => df.fieldname === field),
    parent: meta.name,
  };
}

function environment(t) {
  const dom = new JSDOM("<body></body>");
  t.after(() => dom.window.close());
  const $ = require("jquery")(dom.window);
  const highlighted = [];
  const assets = [];
  const frappe = {
    ui: { form: {} },
    utils: {},
    _messages: {},
    session: { user: "translator@example.com" },
    provide() {},
    require(asset) {
      assets.push(asset);
      return Promise.resolve();
    },
  };
  const context = vm.createContext({
    $,
    jQuery: $,
    frappe,
    console,
    name: "",
    hljs: {
      highlightElement(element) {
        highlighted.push(element.textContent);
      },
    },
  });
  context.window = context;
  for (const path of [
    "translate.js",
    "microtemplate.js",
    "form/controls/base_control.js",
    "form/controls/html.js",
  ]) {
    vm.runInContext(
      readFileSync(`${framework}/frappe/public/js/frappe/${path}`, "utf8"),
      context
    );
  }
  const source = readFileSync(
    `${framework}/frappe/public/js/frappe/utils/utils.js`,
    "utf8"
  );
  const methods = [];
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.type === "ObjectMethod" && node.key.name === "highlight_pre")
      methods.push(source.slice(node.start, node.end));
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(walk);
      else if (value && typeof value === "object") walk(value);
    }
  };
  walk(parse(source, { sourceType: "module" }));
  assert.equal(methods.length, 1);
  vm.runInContext(
    `frappe.utils.highlight_pre = ({${methods[0]}}).highlight_pre`,
    context
  );
  return { $, frappe, highlighted, assets };
}

function render(e, kind, target) {
  const df = definition(kind);
  assert.equal(df.fieldtype, "HTML");
  const doc =
    kind === "product_bundle"
      ? {
          doctype: df.parent,
          name: "KIT-MEMORY-ONLY",
          new_item_code: "SKU@example.com",
          items: [],
        }
      : {
          doctype: df.parent,
          name: "TERMS-MEMORY-ONLY",
          terms: "Saved business body {{ name }}",
          selling: 1,
        };
  const before = JSON.stringify({ df, doc });
  e.frappe._messages = target ? { [df.options]: target } : {};
  const control = new e.frappe.ui.form.ControlHTML({
    parent: e.$("body"),
    df,
    frm: { doc },
    doc,
  });
  control.refresh_input();
  assert.equal(JSON.stringify({ df, doc }), before);
  assert.equal(e.frappe.session.user, "translator@example.com");
  return { control, df, doc };
}

test("actual Product Bundle help points to the current Item settings", (t) => {
  const e = environment(t);
  const { control, df } = render(e, "product_bundle");
  const fields = metadata("stock", "item").fields;
  const expected = ["is_stock_item", "is_sales_item"].map(
    (name) => fields.find((field) => field.fieldname === name).label
  );
  assert.deepEqual(
    control.$wrapper
      .find("code")
      .map((_, element) => e.$(element).text())
      .get(),
    expected
  );
  assert.match(control.$wrapper.text(), /To sell the package/);
  assert.ok(
    df.options.includes("<b>No</b>") && df.options.includes("<b>Yes</b>")
  );
});

test("actual Terms help describes the transaction context and existing setup screen", (t) => {
  const e = environment(t);
  const { control } = render(e, "terms_and_conditions");
  const text = control.$wrapper.text();
  assert.ok(!text.includes("email template"));
  assert.ok(
    text.includes("transaction document") && text.includes("Customize Form")
  );
  assert.ok(text.includes("Sales Order"));
  const fields = metadata("selling", "sales_order").fields;
  for (const name of ["transaction_date", "delivery_date"])
    assert.ok(fields.some((field) => field.fieldname === name));
  for (const token of tokens)
    assert.ok(control.$wrapper.find("pre").text().includes(token));
});

test("actual whole-message translation preserves help HTML and business data", async (t) => {
  const e = environment(t);
  for (const kind of ["product_bundle", "terms_and_conditions"]) {
    const { control, df } = render(e, kind, italian[kind]);
    await Promise.resolve();
    const originalTags = e
      .$("<div>")
      .html(df.options)
      .find("*")
      .map((_, element) => element.tagName)
      .get();
    const translatedTags = control.$wrapper
      .find("*")
      .map((_, element) => element.tagName)
      .get();
    assert.deepEqual(translatedTags, originalTags);
    assert.equal(control.$wrapper.find("script").length, 0);
    assert.ok(
      control.$wrapper
        .text()
        .includes(
          kind === "product_bundle" ? "kit di prodotti" : "termini e condizioni"
        )
    );
    if (kind === "product_bundle") {
      assert.deepEqual(
        control.$wrapper
          .find("code")
          .map((_, element) => e.$(element).text())
          .get(),
        ["Gestisci le giacenze", "Consenti vendita"]
      );
    } else {
      for (const token of tokens)
        assert.ok(control.$wrapper.find("pre").text().includes(token));
      assert.equal(e.highlighted.at(-1), control.$wrapper.find("pre").text());
      assert.equal(e.assets.at(-1), "syntax_highlighting.bundle.js");
    }
    assert.equal(control.df.options, df.options);
  }
});

test("literal Jinja help and documentation link keep the renderer fallback contract", async (t) => {
  const e = environment(t);
  const { control, df, doc } = render(e, "terms_and_conditions");
  await Promise.resolve();
  assert.equal(control.get_content(), df.options);
  assert.equal(doc.terms, "Saved business body {{ name }}");
  assert.ok(!control.$wrapper.find("pre").text().includes(doc.name));
  for (const token of tokens)
    assert.ok(control.$wrapper.find("pre").text().includes(token));
  assert.equal(
    control.$wrapper.find("a").attr("href"),
    "https://jinja.palletsprojects.com/en/stable/templates/"
  );
  assert.equal(e.highlighted.at(-1), control.$wrapper.find("pre").text());
  assert.equal(e.assets.at(-1), "syntax_highlighting.bundle.js");
});
