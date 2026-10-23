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
  "Submitted Job Cards Updated Today":
    "Schede di lavorazione confermate aggiornate oggi",
  "1 submitted job card updated today":
    "1 scheda di lavorazione confermata, aggiornata oggi",
  "{0} submitted job cards updated today":
    "{0} schede di lavorazione confermate, aggiornate oggi",
};
let browser;

test.before(async () => {
  browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
  });
});
test.after(async () => browser?.close());

function card(name, quantity = 1, status = "Completed") {
  return {
    name,
    docstatus: 1,
    status,
    status_colour: "green",
    finished_good: "Example product",
    production_item: "ITEM-EXAMPLE",
    item_image: "",
    operation: "Example operation",
    total_completed_qty: quantity,
    for_quantity: quantity,
    process_loss_qty: 0,
    total_time_in_mins: 10,
    modified: "2026-10-21 01:00:00",
  };
}

async function render({ sessions = [], jobs = [], language = "it" } = {}) {
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
        window.frappe = {
          provide(path) {
            path
              .split(".")
              .reduce((object, key) => (object[key] ||= {}), window);
          },
          ui: { form: {} },
          utils: {},
          boot: { lang: language, sysdefaults: { number_format: "#.###,##" } },
          _messages: dictionary,
          session: { user: "translator@example.com" },
          templates: {},
          defaults: { get_default: () => 3, is_enabled: () => false },
        };
      },
      { dictionary: language === "it" ? messages : {}, language }
    );
    for (const file of [
      "format.js",
      "utils/utils.js",
      "dom.js",
      "translate.js",
      "microtemplate.js",
      "utils/common.js",
      "utils/datatype.js",
      "utils/number_format.js",
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
        new URL("./shop_floor.js", import.meta.url),
        "utf8"
      ),
    });
    const template = readFileSync(
      new URL("../templates/shop_floor_template.html", import.meta.url),
      "utf8"
    );
    return await page.evaluate(
      ({ template, sessions, jobs }) => {
        frappe.templates.shop_floor_template = template;
        const view = Object.create(frappe.ui.ShopFloor.prototype);
        Object.assign(view, {
          timer_intervals: {},
          materials_open: {},
          instructions_open: {},
        });
        const container = $("<div></div>").appendTo(document.body);
        const original = JSON.stringify({ sessions, jobs });
        const requests = [];
        frappe.call = (options) => {
          requests.push({
            method: options.method,
            args: structuredClone(options.args),
          });
          options.callback({
            message: {
              job_cards: structuredClone(jobs),
              today_sessions: structuredClone(sessions),
              capacity: 1,
              mode: "work_order",
              oee: null,
            },
          });
        };
        view.load_operator_data(container, {
          workstation: "WS-IGNORED",
          work_order: "WO-EXAMPLE",
        });
        const section = container.find(".mes-list-card").last();
        return {
          title: section.find(".mes-list-card-title").text().trim(),
          subtitle: section.find(".mes-list-card-subtitle").text().trim(),
          rows: section.find(".mes-row").length,
          names: view.today_sessions.map((row) => row.name),
          quantities: view.today_sessions.map((row) => row.total_completed_qty),
          statuses: view.today_sessions.map((row) => row.status),
          links: section
            .find(".mes-row-id a")
            .map((index, element) => element.getAttribute("href"))
            .get(),
          requests,
          rawDeliveryUnchanged: original === JSON.stringify({ sessions, jobs }),
          timers: Object.keys(view.timer_intervals),
          unsafeNodes: container.find("script, private").length,
          identity: frappe.session.user,
        };
      },
      { template, sessions, jobs }
    );
  } finally {
    await page.close();
  }
}

function unchanged(result) {
  assert.equal(result.rawDeliveryUnchanged, true);
  assert.deepEqual(result.requests, [
    {
      method: "erpnext.manufacturing.page.shop_floor.shop_floor.get_data",
      args: { workstation: null, work_order: "WO-EXAMPLE" },
    },
  ]);
  assert.deepEqual(result.timers, []);
  assert.equal(result.unsafeNodes, 0);
  assert.equal(result.identity, "translator@example.com");
}

test("one confirmed Job Card updated today is a document, not a session or quantity count", async () => {
  const result = await render({ sessions: [card("JC-ONE", 2.5)] });
  assert.equal(result.title, messages["Submitted Job Cards Updated Today"]);
  assert.equal(result.subtitle, messages["1 submitted job card updated today"]);
  assert.equal(result.rows, 1);
  assert.deepEqual(result.quantities, [2.5]);
  assert.deepEqual(result.links, ["/app/job-card/JC-ONE"]);
  unchanged(result);
});

test("multiple Job Cards count documents independently of their fractional quantities", async () => {
  const result = await render({
    sessions: [card("JC-A", 50.5), card("JC-B", 1.25, "On Hold")],
  });
  assert.equal(result.title, messages["Submitted Job Cards Updated Today"]);
  assert.equal(
    result.subtitle,
    "2 schede di lavorazione confermate, aggiornate oggi"
  );
  assert.equal(result.rows, 2);
  assert.deepEqual(result.quantities, [50.5, 1.25]);
  assert.deepEqual(result.statuses, ["Completed", "On Hold"]);
  unchanged(result);
});

test("the supplied ten-document delivery remains ten displayed rows", async () => {
  const result = await render({
    sessions: Array.from({ length: 10 }, (_, i) => card(`JC-${i}`, 0.25)),
  });
  assert.equal(
    result.subtitle,
    "10 schede di lavorazione confermate, aggiornate oggi"
  );
  assert.equal(result.rows, 10);
  unchanged(result);
});

test("Completed Operations deduplicates by canonical name before counting the remaining rows", async () => {
  const duplicate = card("JC-DUPLICATE");
  const result = await render({
    sessions: [duplicate, card("JC-REMAINING")],
    jobs: [duplicate],
  });
  assert.deepEqual(result.names, ["JC-REMAINING"]);
  assert.equal(result.rows, 1);
  assert.equal(result.subtitle, messages["1 submitted job card updated today"]);
  unchanged(result);
});

test("no eligible delivery hides the entire heading and subtitle", async () => {
  const result = await render();
  assert.equal(result.title, "");
  assert.equal(result.subtitle, "");
  assert.equal(result.rows, 0);
  unchanged(result);
});

test("deduplicating every delivery hides the section without changing the canonical input", async () => {
  const duplicate = card("JC-DUPLICATE");
  const result = await render({ sessions: [duplicate], jobs: [duplicate] });
  assert.deepEqual(result.names, []);
  assert.notEqual(result.title, messages["Submitted Job Cards Updated Today"]);
  unchanged(result);
});

test("the whole English heading and plural frame remain the fallback", async () => {
  const result = await render({
    language: "en",
    sessions: [card("JC-A"), card("JC-B")],
  });
  assert.equal(result.title, "Submitted Job Cards Updated Today");
  assert.equal(result.subtitle, "2 submitted job cards updated today");
  unchanged(result);
});

test("canonical link attributes retain quoted names independently of translated captions", async () => {
  const item = card('JC-caffè &"quoted"');
  item.finished_good = "Example authored & product";
  const result = await render({ sessions: [item] });
  assert.deepEqual(result.names, [item.name]);
  assert.deepEqual(result.links, ['/app/job-card/JC-caffè &"quoted"']);
  assert.equal(result.subtitle, messages["1 submitted job card updated today"]);
  unchanged(result);
});
