import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { chromium } = require("@playwright/test");
const frappeRoot = new URL(
  "../../../../../frappe/frappe/public/js/",
  import.meta.url
);
const messages = {
  "Remaining quantity: {0} (component returns recorded)":
    "Quantità residua: {0} (resi di componenti registrati)",
  "Remaining quantity: {0} (no component returns recorded)":
    "Quantità residua: {0} (nessun reso di componenti registrato)",
  "{0} items produced": "Quantità prodotta: {0}",
  "{0} items in progress": "Quantità in lavorazione: {0}",
  "{0} items lost during process.": "Perdita di processo: {0}",
  "{0} items disassembled": "Quantità smontata: {0}",
};
let browser;

test.before(async () => {
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
  });
});
test.after(async () => browser?.close());

async function render(values, returns = [0], language = "it") {
  const page = await browser.newPage();
  try {
    await page.setContent("<!doctype html><html><body></body></html>");
    await page.addScriptTag({
      content: readFileSync(
        new URL("lib/jquery/jquery.min.js", frappeRoot),
        "utf8"
      ),
    });
    await page.evaluate(
      ({ dictionary, language }) => {
        window.handlers = {};
        window.frappe = {
          provide(path) {
            path
              .split(".")
              .reduce((object, key) => (object[key] ||= {}), window);
          },
          ui: {
            form: {
              on(name, methods) {
                window.handlers[name] = methods;
              },
            },
          },
          utils: {},
          boot: { lang: language, sysdefaults: { number_format: "#.###,##" } },
          _messages: dictionary,
          session: { user: "translator@example.com" },
          defaults: { get_default: () => 3, is_enabled: () => false },
          tour: {},
        };
        window.erpnext = {};
        frappe.call = () => {
          throw new Error("Unexpected request");
        };
      },
      { dictionary: language === "it" ? messages : {}, language }
    );
    for (const file of [
      "format.js",
      "utils/utils.js",
      "dom.js",
      "translate.js",
      "utils/datatype.js",
      "utils/number_format.js",
      "form/dashboard.js",
    ]) {
      await page.addScriptTag({
        content: readFileSync(
          new URL(`frappe/${file}`, frappeRoot),
          "utf8"
        ).replace(/^import .*;\n/gm, ""),
      });
    }
    await page.addScriptTag({
      content: readFileSync(
        new URL("./work_order.js", import.meta.url),
        "utf8"
      ),
    });
    return await page.evaluate(
      ({ values, returns }) => {
        const doc = {
          doctype: "Work Order",
          name: "WO-EXAMPLE",
          docstatus: 1,
          status: "Closed",
          stock_uom: "Kg",
          qty: 10,
          material_transferred_for_manufacturing: 10,
          produced_qty: 5,
          process_loss_qty: 0,
          disassembled_qty: 0,
          skip_transfer: 0,
          ...values,
          required_items: returns.map((qty, i) => ({
            item_code: `ITEM-${i}`,
            stock_uom: i ? "Kg" : "Nos",
            returned_qty: qty,
          })),
        };
        const original = JSON.stringify(doc);
        const parent = $("<div></div>").appendTo(document.body);
        const area = $("<div></div>").appendTo(parent);
        area.body = area;
        const dashboard = Object.create(frappe.ui.form.Dashboard.prototype);
        Object.assign(dashboard, { parent, progress_area: area });
        const addProgress = dashboard.add_progress;
        let captured;
        dashboard.add_progress = function (...args) {
          captured = structuredClone(args);
          return addProgress.apply(this, args);
        };
        window.handlers["Work Order"].show_progress_for_items({
          doc,
          dashboard,
        });
        return {
          bars: captured[1],
          message: captured[2],
          renderedTitles: area
            .find(".progress-bar")
            .map((index, element) => $(element).attr("title"))
            .get(),
          renderedMessage: area.find(".progress-message").text(),
          rawDocumentUnchanged: JSON.stringify(doc) === original,
          identity: frappe.session.user,
        };
      },
      { values, returns }
    );
  } finally {
    await page.close();
  }
}

function unchanged(result) {
  assert.equal(result.rawDocumentUnchanged, true);
  assert.equal(result.identity, "translator@example.com");
  assert.deepEqual(
    result.renderedTitles,
    result.bars.map((bar) => bar.title)
  );
  assert.equal(result.renderedMessage, result.message);
}

for (const fixture of [
  {
    name: "small recorded component return does not label the residual as returned output",
    values: {},
    returns: [0.25],
    quantity: 5,
    recorded: true,
  },
  {
    name: "heterogeneous component units do not become a summed returned quantity",
    values: {},
    returns: [0.25, 100],
    quantity: 5,
    recorded: true,
  },
  {
    name: "absence of component returns reports the residual without asserting a physical return action",
    values: {},
    returns: [0],
    quantity: 5,
    recorded: false,
  },
  {
    name: "a residual of one uses a fixed quantity noun",
    values: { produced_qty: 9 },
    returns: [1],
    quantity: 1,
    recorded: true,
  },
  {
    name: "fractional residual preserves its exact value",
    values: { produced_qty: 8.75 },
    returns: [0.25],
    quantity: 1.25,
    recorded: true,
  },
]) {
  test(fixture.name, async () => {
    const result = await render(fixture.values, fixture.returns);
    const title = fixture.recorded
      ? `Quantità residua: ${fixture.quantity} (resi di componenti registrati)`
      : `Quantità residua: ${fixture.quantity} (nessun reso di componenti registrato)`;
    assert.equal(result.bars[1].title, title);
    assert.equal(result.bars[1].width, `${fixture.quantity * 10}%`);
    assert.equal(
      result.bars[1].progress_class,
      fixture.recorded ? "progress-bar-warning" : "progress-bar-info"
    );
    unchanged(result);
  });
}

for (const quantity of [0, -1]) {
  test(`residual ${quantity} does not create a return-progress bar`, async () => {
    const result = await render({ produced_qty: 10 - quantity });
    assert.equal(result.bars.length, 1);
    unchanged(result);
  });
}

test("skip transfer hides residual progress without changing produced output", async () => {
  const result = await render({ skip_transfer: 1 });
  assert.deepEqual(result.bars, [
    {
      title: "Quantità prodotta: 5",
      width: "50%",
      progress_class: "progress-bar-success",
    },
  ]);
  unchanged(result);
});

test("nonclosed work orders retain the reviewed in-progress quantity label", async () => {
  const result = await render({ status: "In Process" }, [0.25]);
  assert.equal(result.bars[1].title, "Quantità in lavorazione: 5");
  assert.equal(result.bars[1].progress_class, "progress-bar-warning");
  unchanged(result);
});

test("zero produced keeps the minimum visible width and matching residual width adjustment", async () => {
  const result = await render(
    { produced_qty: 0, material_transferred_for_manufacturing: 5 },
    [0.25]
  );
  assert.equal(result.bars[0].width, "0.5%");
  assert.equal(
    result.bars[1].title,
    "Quantità residua: 5 (resi di componenti registrati)"
  );
  assert.equal(result.bars[1].width, "49.5%");
  unchanged(result);
});

test("loss and disassembly retain their independent quantities and the original residual math", async () => {
  const result = await render(
    { produced_qty: 5, disassembled_qty: 1.5, process_loss_qty: 2.5 },
    [0.25]
  );
  assert.deepEqual(
    result.bars.map((bar) => bar.title),
    [
      "Quantità prodotta: 3.5",
      "Quantità residua: 2.5 (resi di componenti registrati)",
      "Perdita di processo: 2.5",
      "Quantità smontata: 1.5",
    ]
  );
  assert.deepEqual(
    result.bars.map((bar) => bar.width),
    ["35%", "25%", "25%", "15%"]
  );
  unchanged(result);
});

test("the whole English residual frame remains the fallback", async () => {
  const result = await render({}, [0.25], "en");
  assert.equal(
    result.bars[1].title,
    "Remaining quantity: 5 (component returns recorded)"
  );
  unchanged(result);
});
