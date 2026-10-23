// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import test, { after, before } from "node:test";

const require = createRequire(import.meta.url);
const { chromium } = require("@playwright/test");
const erpnext = new URL("../../../../", import.meta.url);
const frappe = new URL("../../../../../frappe/", import.meta.url);
const frappeRequire = createRequire(new URL("package.json", frappe));
const sources = {};
for (const path of [
  "format.js",
  "translate.js",
  "utils/utils.js",
  "dom.js",
  "form/controls/base_control.js",
  "form/controls/base_input.js",
  "form/controls/data.js",
  "form/controls/select.js",
  "views/reports/query_report.js",
])
  sources[path] = fs.readFileSync(
    new URL(`frappe/public/js/frappe/${path}`, frappe),
    "utf8"
  );
sources.jquery = fs.readFileSync(frappeRequire.resolve("jquery"), "utf8");
sources.context = frappeRequire("esbuild").transformSync(
  fs.readFileSync(
    new URL("frappe/public/js/frappe/utils/select_option_context.ts", frappe),
    "utf8"
  ),
  { loader: "ts", format: "iife", globalName: "contextModule" }
).code;
sources.report = fs.readFileSync(
  new URL(
    "erpnext/manufacturing/report/material_requirements_planning_report/material_requirements_planning_report.js",
    erpnext
  ),
  "utf8"
);
let browser;
before(async () => {
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_EXECUTABLE_PATH,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
});
after(async () => {
  await browser?.close();
});

async function render(messages, action = false) {
  const page = await browser.newPage();
  const requests = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.route("**/*", (route) => route.abort());
  try {
    await page.setContent("<!doctype html><body><div id='field'></div></body>");
    const result = await page.evaluate(
      async ({ sources, messages, action }) => {
        window.eval(sources.jquery);
        window.cstr = (value) => (value == null ? "" : String(value));
        window.cint = (value) => Number.parseInt(value || 0, 10);
        window.is_null = (value) => value == null || value === "";
        window.frappe = {
          provide(path) {
            path
              .split(".")
              .reduce((object, key) => (object[key] ||= {}), window);
          },
          ui: { form: {} },
          utils: {},
          model: { no_value_type: [], table_fields: [] },
          views: { BaseList: class {} },
          standard_pages: {},
          _messages: messages,
          get_meta: () => ({}),
          defaults: { get_user_default: () => "" },
          datetime: {
            get_today: () => "2026-10-21",
            add_days: () => "2026-10-28",
            add_months: () => "2027-01-21",
          },
          format: (value) => value || "",
        };
        window.eval(sources["format.js"]);
        window.eval(sources["translate.js"]);
        window.eval(sources["utils/utils.js"].replace(/^import .*;\n/gm, ""));
        frappe.utils.icon = () => "<svg></svg>";
        const dom = sources["dom.js"];
        window.eval(
          dom.slice(
            dom.indexOf("frappe.run_serially ="),
            dom.indexOf("frappe.load_image =")
          )
        );
        for (const name of ["base_control", "base_input", "data"]) {
          window.eval(
            sources[`form/controls/${name}.js`].replace(/^import .*;\n/gm, "")
          );
        }
        window.eval(sources.context);
        window.selectOptionContext = window.contextModule.selectOptionContext;
        window.eval(
          sources["form/controls/select.js"].replace(/^import .*;\n/gm, "")
        );
        window.DataTable = class {};
        window.eval(
          sources["views/reports/query_report.js"].replace(
            /^import .*;\n/gm,
            ""
          )
        );
        window.eval(sources.report);
        const definition = frappe.query_reports[
          "Material Requirements Planning Report"
        ].filters.find((field) => field.fieldname === "bucket_view");
        const raw = JSON.stringify(definition);
        const document = { bucket_view: definition.default };
        const changes = [];
        const df = {
          ...definition,
          onchange() {
            changes.push(this.get_value());
          },
        };
        const field = new frappe.ui.form.ControlSelect({
          df,
          doc: document,
          parent: window.document.getElementById("field"),
          render_input: true,
        });
        const report = Object.create(frappe.views.QueryReport.prototype);
        report.filters = [field];
        const initial = report.get_filter_values();
        const translate = window.__;
        if (action) {
          field.input.value = "Release Date";
          field.input.dispatchEvent(new Event("change", { bubbles: true }));
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
        return {
          labels: Array.from(
            field.input.options,
            (option) => option.textContent
          ),
          values: Array.from(field.input.options, (option) => option.value),
          initial,
          serialized: JSON.stringify(report.get_filter_values()),
          model: document,
          value: field.get_value(),
          changes,
          definition_unchanged: JSON.stringify(definition) === raw,
          canonical: {
            options: definition.options,
            default: definition.default,
            depends_on: definition.depends_on,
          },
          global: translate("Release Date"),
          is_actual_select: field.input instanceof HTMLSelectElement,
        };
      },
      { sources, messages, action }
    );
    assert.deepEqual(requests, []);
    return result;
  } finally {
    await page.close();
  }
}

const messages = {
  "Release Date": "Data di sblocco",
  "Delivery Date": "Data di consegna",
  "Release Date:Material Requirements Planning Report": "Data di avvio",
};

test("the actual MRP Select renders start-date context and global delivery-date fallback", async () => {
  const result = await render(messages);
  assert.deepEqual(result.labels, ["Data di consegna", "Data di avvio"]);
  assert.equal(result.global, "Data di sblocco");
  assert.equal(result.is_actual_select, true);
});

test("real input change and QueryReport serialization retain raw options and model values", async () => {
  const result = await render(messages, true);
  assert.equal(result.is_actual_select, true);
  assert.deepEqual(result.values, ["Delivery Date", "Release Date"]);
  assert.deepEqual(result.initial, { bucket_view: "Delivery Date" });
  assert.equal(result.value, "Release Date");
  assert.deepEqual(result.model, { bucket_view: "Release Date" });
  assert.equal(result.serialized, '{"bucket_view":"Release Date"}');
  assert.deepEqual(result.changes, ["Release Date"]);
  assert.equal(result.definition_unchanged, true);
  assert.deepEqual(result.canonical, {
    options: "Delivery Date\nRelease Date",
    default: "Delivery Date",
    depends_on: "eval:doc.show_in_bucket_view == 1",
  });
});

test("empty and missing context targets retain the existing global fallback", async () => {
  for (const target of [undefined, ""]) {
    const dictionary = { ...messages };
    if (target === undefined)
      delete dictionary["Release Date:Material Requirements Planning Report"];
    else
      dictionary["Release Date:Material Requirements Planning Report"] = target;
    const result = await render(dictionary);
    assert.deepEqual(result.labels, ["Data di consegna", "Data di sblocco"]);
    assert.deepEqual(result.values, ["Delivery Date", "Release Date"]);
  }
});

test("English fallback renders the original captions and retains the default selection", async () => {
  const result = await render({});
  assert.deepEqual(result.labels, ["Delivery Date", "Release Date"]);
  assert.equal(result.value, "Delivery Date");
  assert.deepEqual(result.model, { bucket_view: "Delivery Date" });
});
