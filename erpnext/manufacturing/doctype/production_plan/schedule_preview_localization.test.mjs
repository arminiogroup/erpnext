import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const require = createRequire(import.meta.url);
const { JSDOM } = require("jsdom");
const jquery = require("jquery");
const moment = require("moment");
const frappeRoot = fileURLToPath(
  new URL("../../../../../frappe/", import.meta.url)
);
const plain = (value) => JSON.parse(JSON.stringify(value));

function load(t) {
  const dom = new JSDOM("<!doctype html><body></body>");
  const $ = jquery(dom.window);
  const dialogs = [];
  const requests = [];
  const handlers = {};
  const priorNow = moment.now;
  moment.now = () => Date.parse("2026-10-20T08:00:00Z");
  t.after(() => {
    moment.now = priorNow;
    dom.window.close();
  });
  const frappe = {
    session: { user: "translator@example.com" },
    _messages: { Start: "Avvia", "Start:Time": "Inizio" },
    provide() {},
    tour: {},
    utils: {},
    datetime: { now_datetime: () => "2026-10-20 08:00:00" },
    call: (request) => requests.push(request),
    ui: {
      form: { on: (doctype, events) => (handlers[doctype] = events) },
      Dialog: class {
        constructor(options) {
          this.options = options;
          this.$body = $("<div></div>");
          dialogs.push(this);
        }
        show() {
          this.shown = true;
        }
        hide() {
          this.hidden = true;
        }
      },
    },
  };
  const sandbox = {
    frappe,
    erpnext: {},
    $,
    jQuery: $,
    document: dom.window.document,
    moment,
    CHART_PALETTE: [],
    number_systems: {},
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  for (const path of [
    "frappe/public/js/frappe/format.js",
    "frappe/public/js/frappe/translate.js",
    "frappe/public/js/frappe/utils/datatype.js",
    "frappe/public/js/frappe/utils/common.js",
    "frappe/public/js/frappe/utils/utils.js",
  ]) {
    const source = readFileSync(frappeRoot + path, "utf8").replace(
      /^import .+;\n/gm,
      ""
    );
    vm.runInContext(source, context, { filename: path });
  }
  vm.runInContext(
    readFileSync(new URL("./production_plan.js", import.meta.url), "utf8"),
    context,
    { filename: "production_plan.js" }
  );
  const frm = {
    doc: {
      name: "PLAN-EXAMPLE",
      po_items: [
        {
          name: "PLAN-ROW",
          item_code: "CAFFE",
          planned_qty: 2,
          planned_start_date: null,
        },
      ],
    },
    events: handlers["Production Plan"],
  };
  return { frappe, sandbox, frm, dialogs, requests, context };
}

function proposal() {
  return {
    orders_exist: false,
    completion_date: "2026-11-02 12:00:00",
    unscheduled: {},
    rows: {
      FG: {
        row_type: "Finished Good",
        item_code: "Start <b>caffè</b>",
        start: "2026-11-02 10:00:00",
        end: "2026-11-02 12:00:00",
        blocks: [{ workstation: "Set <central>" }],
      },
      RM: {
        row_type: "Raw Material",
        item_code: "MATERIALE",
        supplier: "Fornitore <example>",
        consumers: ["FG"],
        start: "2026-11-02 08:00:00",
        end: "2026-11-02 09:00:00",
        blocks: [],
      },
    },
  };
}

test("actual preview renders a timestamp heading and preserves request/apply data", (t) => {
  const env = load(t);
  assert.equal(env.frappe.session.user, "translator@example.com");
  env.frm.events.show_schedule_dialog(env.frm);
  const form = env.dialogs[0];
  const values = {
    start_date: "2026-11-02 07:00:00",
    use_item_dates: 1,
    items: [
      { plan_row: "PLAN-ROW", start_date: "2026-11-02 10:00:00" },
      { plan_row: "EMPTY", start_date: "" },
      { start_date: "2026-11-03 10:00:00" },
    ],
  };
  const initialValues = JSON.stringify(values);
  form.options.primary_action(values);
  assert.equal(form.hidden, true);
  assert.equal(env.requests.length, 1);
  const request = env.requests[0];
  const args = {
    production_plan: "PLAN-EXAMPLE",
    start_date: values.start_date,
    use_item_dates: 1,
    item_dates: { "PLAN-ROW": "2026-11-02 10:00:00" },
  };
  assert.equal(
    request.method,
    "erpnext.manufacturing.scheduling.plan_adapter.get_schedule_preview"
  );
  assert.equal(request.type, "GET");
  assert.equal(request.freeze, true);
  assert.deepEqual(plain(request.args), args);
  const data = proposal();
  const initialProposal = JSON.stringify(data);
  request.callback({ message: data });
  const preview = env.dialogs[1];
  assert.equal(preview.shown, true);
  const body = preview.$body;
  const rows = body.find("tbody tr");
  assert.equal(rows.length, 2);
  assert.equal(rows.eq(0).find("td").eq(0).text().trim(), "Start <b>caffè</b>");
  assert.equal(rows.eq(0).find("td").eq(1).text().trim(), "Set <central>");
  assert.equal(rows.eq(0).find("b").length, 0);
  assert.equal(
    rows.eq(0).find("td").eq(2).text(),
    env.sandbox.format_schedule_date(data.rows.FG.start)
  );
  assert.equal(
    rows.eq(0).find("td").eq(3).text(),
    env.sandbox.format_schedule_date(data.rows.FG.end)
  );
  assert.match(rows.eq(1).text(), /MATERIALE/);
  preview.options.primary_action();
  assert.equal(preview.hidden, true);
  assert.equal(env.requests.length, 2);
  assert.equal(
    env.requests[1].method,
    "erpnext.manufacturing.scheduling.plan_adapter.apply_schedule"
  );
  assert.deepEqual(plain(env.requests[1].args), args);
  assert.equal(JSON.stringify(values), initialValues);
  assert.equal(JSON.stringify(data), initialProposal);
  assert.equal(env.sandbox.__("Start"), "Avvia");
  assert.equal(body.find("thead th").eq(2).text(), "Inizio");
});

test("locked or incomplete previews retain the no-apply decision", (t) => {
  const env = load(t);
  for (const [orders_exist, unscheduled] of [
    [true, {}],
    [false, { TASK: "Capacity" }],
  ]) {
    const data = { ...proposal(), orders_exist, unscheduled };
    env.frm.events.show_schedule_preview(
      env.frm,
      { start_date: "2026-11-02 07:00:00" },
      data
    );
    const preview = env.dialogs.at(-1);
    assert.equal(preview.shown, true);
    assert.equal(preview.options.primary_action, undefined);
    assert.equal(preview.options.primary_action_label, undefined);
    assert.equal(preview.$body.find("tbody tr").length, 2);
  }
  assert.equal(env.requests.length, 0);
});

test("item-date opt-out and failed preview callbacks keep their existing contracts", (t) => {
  const env = load(t);
  const args = env.frm.events.get_schedule_args(env.frm, {
    start_date: "2026-11-02 07:00:00",
    use_item_dates: 0,
    items: [{ plan_row: "PLAN-ROW", start_date: "2026-11-03 09:00:00" }],
  });
  assert.deepEqual(plain(args.item_dates), {});
  env.frm.events.fetch_schedule_preview(env.frm, args);
  env.requests[0].callback({ exc: "PermissionError" });
  assert.equal(env.dialogs.length, 0);
  assert.equal(env.requests.length, 1);
});
