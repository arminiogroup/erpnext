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
const SOURCE =
  "ERPNext supports FIFO, Moving Average, LIFO and Standard Cost for stock valuation. For details on FIFO and Moving Average, see <a href='https://docs.frappe.io/erpnext/user/manual/en/calculation-of-valuation-rate-in-fifo-and-moving-average' target='_blank'>Item Valuation, FIFO and Moving Average.</a>";
const ITALIAN =
  "ERPNext prevede i metodi di valorizzazione delle scorte FIFO, media mobile, LIFO e costo standard. Per approfondire FIFO e media mobile, consulta <a href='https://docs.frappe.io/erpnext/user/manual/en/calculation-of-valuation-rate-in-fifo-and-moving-average' target='_blank'>Valorizzazione degli articoli, FIFO e media mobile.</a>";
const HREF =
  "https://docs.frappe.io/erpnext/user/manual/en/calculation-of-valuation-rate-in-fifo-and-moving-average";
const sources = {
  jquery: fs.readFileSync(frappeRequire.resolve("jquery"), "utf8"),
  driver: fs.readFileSync(frappeRequire.resolve("driver.js"), "utf8"),
  item: fs.readFileSync(
    new URL("erpnext/stock/doctype/item/item.js", erpnext),
    "utf8"
  ),
};
for (const [name, path] of Object.entries({
  format: "frappe/public/js/frappe/format.js",
  translate: "frappe/public/js/frappe/translate.js",
  tour: "frappe/public/js/frappe/form/form_tour.js",
}))
  sources[name] = fs.readFileSync(new URL(path, frappe), "utf8");
const item = JSON.parse(
  fs.readFileSync(
    new URL("erpnext/stock/doctype/item/item.json", erpnext),
    "utf8"
  )
);
const settings = JSON.parse(
  fs.readFileSync(
    new URL(
      "erpnext/stock/doctype/stock_settings/stock_settings.json",
      erpnext
    ),
    "utf8"
  )
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

async function render(messages, { name, stored } = {}) {
  const page = await browser.newPage();
  const requests = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.route("**/*", (route) => route.abort());
  try {
    await page.setContent("<!doctype html><body></body>");
    const result = await page.evaluate(
      async ({ sources, messages, name, stored }) => {
        window.eval(sources.jquery);
        const reads = [],
          routerEvents = [],
          formEvents = [];
        window.frappe = {
          ui: { form: { on() {} } },
          tour: {},
          _messages: messages,
          router: {
            on: (event) => routerEvents.push(event),
            slug: (value) => value.toLowerCase(),
          },
          utils: {
            sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
          },
          db: {
            exists: async (...args) => {
              reads.push(["exists", ...args]);
              return Boolean(stored);
            },
            get_doc: async (...args) => {
              reads.push(["get_doc", ...args]);
              return {
                steps: [
                  {
                    fieldname: "valuation_rate",
                    title: "Authored title",
                    description: stored,
                  },
                ],
              };
            },
          },
          provide(path) {
            path
              .split(".")
              .reduce((object, key) => (object[key] ||= {}), window);
          },
        };
        window.eval(sources.format);
        window.eval(sources.translate);
        window.eval(sources.driver);
        frappe.Driver = window.Driver;
        window.eval(sources.tour);
        window.eval(sources.item);
        const before = JSON.stringify(frappe.tour.Item);
        const fields = {};
        for (const step of frappe.tour.Item) {
          const wrapper = document.createElement("div");
          wrapper.className = "frappe-control";
          wrapper.dataset.fieldname = step.fieldname;
          wrapper.textContent = step.fieldname;
          wrapper.style.minHeight = "30px";
          document.body.append(wrapper);
          fields[step.fieldname] = { wrapper };
        }
        const frm = {
          doctype: "Item",
          get_field: (fieldname) => fields[fieldname],
          layout: {
            sections: [
              {
                collapse(value) {
                  formEvents.push(["collapse", value]);
                },
              },
            ],
          },
        };
        const tour = new frappe.ui.form.FormTour({ frm });
        await tour.init({ tour_name: name });
        const index = tour.driver_steps.findIndex(
          (step) => step.element === fields.valuation_rate.wrapper
        );
        tour.start(index);
        await new Promise((resolve) => setTimeout(resolve, 25));
        const description = document.querySelector(
          ".driver-popover-description"
        );
        if (!description) throw new Error("Actual Driver popover missing");
        const anchor = description.querySelector("a");
        const result = {
          html: description.innerHTML,
          text: description.textContent,
          anchor: anchor && {
            href: anchor.getAttribute("href"),
            target: anchor.getAttribute("target"),
            text: anchor.textContent,
          },
          actual_driver: tour.driver instanceof window.Driver,
          fieldname: tour.driver_steps[index].element.dataset.fieldname,
          step_names: frappe.tour.Item.map((step) => step.fieldname),
          steps: tour.driver_steps.length,
          registration_unchanged: JSON.stringify(frappe.tour.Item) === before,
          reads,
          routerEvents,
          formEvents,
        };
        tour.driver.reset();
        return result;
      },
      { sources, messages, name, stored }
    );
    assert.equal(result.actual_driver, true);
    assert.equal(result.registration_unchanged, true);
    assert.equal(result.fieldname, "valuation_rate");
    assert.deepEqual(result.routerEvents, ["change"]);
    assert.deepEqual(result.formEvents, [["collapse", false]]);
    assert.deepEqual(requests, []);
    return result;
  } finally {
    await page.close();
  }
}

function checkLink(result) {
  assert.equal(result.anchor.href, HREF);
  assert.equal(result.anchor.target, "_blank");
  assert.equal(result.steps, 8);
  assert.deepEqual(result.step_names, [
    "item_code",
    "item_group",
    "is_stock_item",
    "include_item_in_manufacturing",
    "opening_stock",
    "valuation_rate",
    "standard_rate",
    "item_defaults",
  ]);
}

for (const [name, messages, expected] of [
  ["English", {}, SOURCE],
  ["Italian", { [SOURCE]: ITALIAN }, ITALIAN],
  ["empty target", { [SOURCE]: "" }, SOURCE],
]) {
  test(`${name} renders the four-method statement and the scoped article link through the actual tour`, async () => {
    const result = await render(messages);
    checkLink(result);
    assert.equal(
      result.html,
      expected
        .replaceAll("href='", 'href="')
        .replaceAll("' target='", '" target="')
        .replaceAll("'>", '">')
    );
    assert.deepEqual(result.reads, [["exists", "Form Tour", "Item"]]);
  });
}

test("a stored same-DocType tour retains its authored description ahead of the packaged fallback", async () => {
  const stored = "Authored stored tour description";
  const result = await render({ [SOURCE]: ITALIAN }, { stored });
  assert.equal(result.html, stored);
  assert.equal(result.steps, 1);
  assert.equal(result.anchor, null);
  assert.deepEqual(result.reads, [
    ["exists", "Form Tour", "Item"],
    ["get_doc", "Form Tour", "Item"],
  ]);
});

test("an explicit named tour keeps its canonical name and skips the fallback existence read", async () => {
  const result = await render(
    { [SOURCE]: ITALIAN },
    { name: "AUTHORED-TOUR-RAW", stored: "Named tour content" }
  );
  assert.equal(result.html, "Named tour content");
  assert.deepEqual(result.reads, [
    ["get_doc", "Form Tour", "AUTHORED-TOUR-RAW"],
  ]);
});

test("Item and Stock Settings retain the four canonical options and serialized-item configuration", () => {
  const field = (metadata) =>
    metadata.fields.find((row) => row.fieldname === "valuation_method");
  assert.deepEqual(field(item).options.split("\n").filter(Boolean), [
    "FIFO",
    "Moving Average",
    "LIFO",
    "Standard Cost",
  ]);
  assert.deepEqual(field(settings).options.split("\n"), [
    "FIFO",
    "Moving Average",
    "LIFO",
    "Standard Cost",
  ]);
  assert.equal(field(item).fieldtype, "Select");
  assert.equal(field(settings).fieldtype, "Select");
  assert.equal(
    field(item).description,
    "Serialized items are valued at Moving Average once stock transactions exist and Serial No Wise Valuation is disabled."
  );
  const serial = item.fields.find(
    (row) => row.fieldname === "use_serial_no_wise_valuation"
  );
  assert.equal(serial.fieldtype, "Check");
  assert.equal(
    serial.description,
    "Value every outward movement at each Serial No's own incoming rate. If unchecked, the item is valued at Moving Average once stock transactions exist. Cannot be enabled once Serial Nos exist for this item."
  );
});
