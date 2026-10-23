import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const erp = fileURLToPath(new URL("../../", import.meta.url));
const framework = fileURLToPath(new URL("../../../frappe/", import.meta.url));
const require = createRequire(framework + "/package.json");
const { JSDOM } = require("jsdom");
const jquery = require("jquery");
const { buildSync } = require("esbuild");
const markers = JSON.parse(
  execFileSync(
    process.env.PYTHON || "python",
    [
      "-c",
      `import json,sys
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
with open(sys.argv[1], "rb") as stream:
 print(json.dumps([(message, context) for _, message, _, context in extract("frappe.gettext.extractors.python.extract", stream, keywords=PYTHON_KEYWORDS)]))`,
      erp + "/erpnext/gettext/contextual_messages.py",
    ],
    { encoding: "utf8" }
  )
);

function metadata(module, name) {
  return JSON.parse(
    readFileSync(
      `${erp}/erpnext/${module}/doctype/${name}/${name}.json`,
      "utf8"
    )
  );
}

function environment() {
  const dom = new JSDOM("<!doctype html><body></body>", {
    url: "https://example.com",
    runScripts: "outside-only",
  });
  const window = dom.window;
  const context = dom.getInternalVMContext();
  window.$ = window.jQuery = jquery(window);
  window.fetch = () => {
    throw Error("Unexpected network");
  };
  window.XMLHttpRequest = class {
    constructor() {
      throw Error("Unexpected XHR");
    }
  };
  const translations = { Qualification: "Qualificazione" };
  if (
    markers.some(
      ([source, context]) =>
        source === "Qualification" && context === "Employee Education"
    )
  ) {
    translations["Qualification:Employee Education"] = "Titolo di studio";
  }
  window.frappe = {
    ui: { form: {} },
    utils: {},
    model: {},
    session: { user: "translator@example.com" },
    boot: { lang: "it", translated_doctypes: ["Sales Stage"] },
    _messages: translations,
    provide(path) {
      let value = window;
      for (const key of path.split(".")) value = value[key] ??= {};
    },
  };
  for (const name of ["format", "translate"]) {
    vm.runInContext(
      readFileSync(`${framework}/frappe/public/js/frappe/${name}.js`, "utf8"),
      context
    );
  }
  for (const path of [
    "frappe/public/js/frappe/form/controls/base_control.js",
    "frappe/public/js/frappe/form/controls/base_input.js",
    "frappe/public/js/frappe/form/controls/data.js",
    "frappe/public/js/frappe/form/controls/link.js",
    "frappe/public/js/frappe/model/meta.js",
  ]) {
    vm.runInContext(
      buildSync({
        entryPoints: [framework + "/" + path],
        bundle: true,
        write: false,
        format: "iife",
        platform: "browser",
        logLevel: "silent",
      }).outputFiles[0].text,
      context
    );
  }
  return { dom, window };
}

function field(meta, name) {
  return {
    ...meta.fields.find((row) => row.fieldname === name),
    parent: meta.name,
  };
}

function label(window, df) {
  const control = Object.create(window.frappe.ui.form.ControlInput.prototype);
  control.df = { ...df };
  control.value = "Qualification";
  control.label_span = window.document.createElement("label");
  control.set_label();
  assert.equal(control.df.label, "Qualification");
  assert.equal(control.value, "Qualification");
  return control.label_span.textContent;
}

test("actual form and metadata labels distinguish academic from commercial qualification", () => {
  const { dom, window } = environment();
  try {
    const academic = field(
      metadata("setup", "employee_education"),
      "qualification"
    );
    const crm = field(metadata("crm", "lead"), "qualification_tab");
    const before = JSON.stringify([academic, crm]);
    window.frappe.meta.add_field(academic);
    window.frappe.meta.add_field(crm);
    assert.equal(label(window, academic), "Titolo di studio");
    assert.equal(label(window, crm), "Qualificazione");
    assert.equal(
      window.frappe.meta.get_translated_label(
        "Employee Education",
        "qualification"
      ),
      "Titolo di studio"
    );
    assert.equal(
      window.frappe.meta.get_translated_label("Lead", "qualification_tab"),
      "Qualificazione"
    );
    assert.equal(JSON.stringify([academic, crm]), before);
    window.frappe._messages = {};
    assert.equal(label(window, academic), "Qualification");
    assert.equal(label(window, crm), "Qualification");
  } finally {
    dom.window.close();
  }
});

test("actual translated Link keeps canonical sales stage values and unknown fallback", () => {
  const { dom, window } = environment();
  try {
    const stage = metadata("crm", "sales_stage");
    assert.equal(stage.translated_doctype, 1);
    assert.equal(stage.autoname, "field:stage_name");
    const link = Object.create(window.frappe.ui.form.ControlLink.prototype);
    link.df = { options: stage.name };
    link.value = "Qualification";
    const before = JSON.stringify([link.df, link.value]);
    assert.equal(link.get_translated(link.value), "Qualificazione");
    assert.equal(
      link.get_translated("Custom qualification"),
      "Custom qualification"
    );
    assert.equal(link.get_translated(null), null);
    assert.equal(JSON.stringify([link.df, link.value]), before);
    window.frappe.boot.translated_doctypes = [];
    assert.equal(link.get_translated(link.value), "Qualification");
    assert.equal(link.df.options, "Sales Stage");
    assert.equal(link.value, "Qualification");
  } finally {
    dom.window.close();
  }
});
