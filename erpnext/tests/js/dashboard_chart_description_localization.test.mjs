import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const { JSDOM } = require("jsdom");
const { buildSync } = require("esbuild");
const root = fileURLToPath(new URL("../../../", import.meta.url));
const framework = resolve(root, "../frappe");
const cases = [
  {
    path: "erpnext/buying/dashboard_chart/purchase_order_analysis/purchase_order_analysis.json",
    message: "The billed and unbilled amounts on your purchase orders.",
    italian:
      "Gli importi fatturati e ancora da fatturare degli ordini di acquisto.",
  },
  {
    path: "erpnext/selling/dashboard_chart/item_wise_annual_sales/item_wise_annual_sales.json",
    message: "Your leading items by sales order value in the selected period.",
    italian:
      "Gli articoli con il maggiore valore degli ordini di vendita nel periodo selezionato.",
  },
  {
    path: "erpnext/projects/dashboard_chart/completed_projects/completed_projects.json",
    message: "Completed projects, grouped by the month of their last update.",
    italian: "I progetti completati, suddivisi per mese dell'ultima modifica.",
  },
  {
    path: "erpnext/crm/dashboard_chart/won_opportunities/won_opportunities.json",
    message:
      "Converted opportunities, grouped by the month of their last update.",
    italian:
      "Le trattative convertite, suddivise per mese dell'ultima modifica.",
  },
  {
    path: "erpnext/manufacturing/dashboard_chart/completed_operation/completed_operation.json",
    message:
      "Completed quantities on work order operations, grouped by the quarter the operation record was created.",
    italian:
      "Le quantità lavorate nelle operazioni degli ordini di produzione, suddivise per trimestre di creazione dell'operazione.",
  },
  {
    path: "erpnext/manufacturing/dashboard_chart/pending_work_order/pending_work_order.json",
    message:
      "Work orders that are not completed, grouped by age from their actual or planned start date.",
    italian:
      "Gli ordini di produzione non completati, suddivisi per giorni trascorsi dalla data di inizio effettiva o pianificata.",
  },
  {
    path: "erpnext/manufacturing/dashboard_chart/last_month_downtime_analysis/last_month_downtime_analysis.json",
    message: "Workstation downtime within this chart's configured date range.",
    italian:
      "La durata dei fermi delle postazioni di lavoro nel periodo impostato per il grafico.",
  },
  {
    path: "erpnext/manufacturing/dashboard_chart/work_order_qty_analysis/work_order_qty_analysis.json",
    message: "Remaining and produced quantities on your work orders.",
    italian:
      "Le quantità ancora da produrre e quelle già prodotte degli ordini di produzione.",
  },
  {
    path: "erpnext/manufacturing/dashboard_chart/produced_quantity/produced_quantity.json",
    message:
      "Produced quantities on work orders, grouped by the month of their last update.",
    italian:
      "Le quantità prodotte degli ordini di produzione, suddivise per mese dell'ultima modifica.",
  },
  {
    path: "erpnext/accounts/dashboard_chart/accounts_payable_ageing/accounts_payable_ageing.json",
    message:
      "Outstanding supplier balances by age relative to their due date, including amounts not yet due.",
    italian:
      "I debiti verso fornitori per fascia di scadenza, inclusi gli importi non ancora scaduti.",
  },
  {
    path: "erpnext/accounts/dashboard_chart/accounts_receivable_ageing/accounts_receivable_ageing.json",
    message:
      "Outstanding customer balances by age relative to their due date, including amounts not yet due.",
    italian:
      "I crediti verso clienti per fascia di scadenza, inclusi gli importi non ancora scaduti.",
  },
  {
    path: "erpnext/assets/dashboard_chart/asset_value_analytics/asset_value_analytics.json",
    message:
      "Asset values and depreciation by month, using the selected asset date.",
    italian:
      "Valore dei cespiti e ammortamenti per mese, in base alla data selezionata.",
  },
];
const sources = Object.fromEntries(
  cases.map((row) => [row.message, row.italian])
);

const python = String.raw`
import hashlib
import json
from pathlib import Path
import sys
import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
frappe.init(site="", sites_path=str(Path(frappe.get_app_path("frappe")).parents[2] / "sites"))
frappe.local.session = frappe._dict(user="translator@example.com", data=frappe._dict())
frappe.local.lang = "it"
root = Path(sys.argv[1])
methods = get_method_map("erpnext") + get_method_map("frappe")
results = []
for relative in json.load(sys.stdin):
    path = root / relative
    before = path.read_bytes()
    selected = next((method for pattern, method in methods if pathmatch(pattern, relative)), None)
    messages = list(extract_from_file(selected, str(path), keywords=PYTHON_KEYWORDS)) if selected else []
    assert path.read_bytes() == before
    results.append({"path": relative, "method": selected, "messages": messages, "sha256": hashlib.sha256(before).hexdigest()})
frappe.destroy()
print(json.dumps(results))
`;
const extracted = spawnSync(
  resolve(root, "../../env/bin/python"),
  ["-c", python, root],
  {
    input: JSON.stringify([
      ...cases.map((row) => row.path),
      fileURLToPath(import.meta.url).slice(root.length),
    ]),
    encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  }
);
assert.equal(extracted.status, 0, extracted.stderr || extracted.error?.message);
const extraction = JSON.parse(extracted.stdout);

function environment(t, dictionary = sources) {
  const dom = new JSDOM("<body></body>");
  t.after(() => dom.window.close());
  const $ = require("jquery")(dom.window);
  const frappe = {
    ui: {},
    utils: {},
    _messages: dictionary,
    session: { user: "translator@example.com" },
    boot: {},
    xcall() {
      throw new Error("Network forbidden in memory test");
    },
    provide(path) {
      let cursor = context;
      for (const name of path.split(".")) cursor = cursor[name] ||= {};
    },
  };
  const context = vm.createContext({
    $,
    jQuery: $,
    frappe,
    console,
    document: dom.window.document,
    getComputedStyle: dom.window.getComputedStyle.bind(dom.window),
    Widget: class {},
    flt: (value) => Number(value) || 0,
  });
  context.window = context;
  vm.runInContext(
    readFileSync(`${framework}/frappe/public/js/frappe/translate.js`, "utf8"),
    context
  );
  function bundle(path) {
    const { outputFiles } = buildSync({
      entryPoints: [resolve(framework, path)],
      bundle: true,
      write: false,
      format: "iife",
      platform: "browser",
    });
    vm.runInContext(outputFiles[0].text, context, { filename: path });
  }
  bundle("frappe/public/js/frappe/utils/utils.js");
  bundle("frappe/public/js/frappe/ui/components/empty_state.js");
  bundle("frappe/public/js/frappe/ui/chart.js");
  const widget = readFileSync(
    `${framework}/frappe/public/js/frappe/widgets/chart_widget.js`,
    "utf8"
  );
  vm.runInContext(
    widget
      .replace('import Widget from "./base_widget.js";', "")
      .replace(
        "export default class ChartWidget",
        "globalThis.ChartWidget = class ChartWidget"
      ),
    context
  );
  frappe.defaults = { is_enabled: () => false };
  return { $, frappe, context };
}

function render(e, chart) {
  const widget = Object.create(e.context.ChartWidget.prototype);
  widget.chart_doc = chart;
  widget.height = 240;
  widget.empty = e.$("<section></section>").appendTo("body");
  let destroyed = 0;
  widget.empty_sample = { destroy: () => destroyed++ };
  widget.data = { labels: ["Memory period"], datasets: [{ values: [0] }] };
  assert.equal(widget.has_data(), false);
  const before = JSON.stringify(chart);
  widget.show_empty_state();
  assert.equal(JSON.stringify(chart), before);
  assert.equal(destroyed, 1);
  assert.equal(widget.empty_sample, undefined);
  assert.notEqual(widget.empty.css("display"), "none");
  return widget.empty;
}

for (const row of cases) {
  test(`${row.path}: configured extraction contains the truthful whole description`, () => {
    const result = extraction.find((entry) => entry.path === row.path);
    assert.equal(
      result.method,
      "frappe.gettext.extractors.dashboard_chart.extract"
    );
    const chart = JSON.parse(readFileSync(resolve(root, row.path), "utf8"));
    assert.equal(chart.empty_state_message, row.message);
    const description = result.messages.find(
      (entry) => entry[1] === row.message
    );
    assert.ok(description, JSON.stringify(result.messages));
    assert.equal(description[3], null);
    assert.deepEqual(description[2], [
      "Empty state message of a dashboard chart",
    ]);
    assert.equal(result.messages.length, 2);
    assert.ok(result.messages.some((entry) => entry[1] === chart.chart_name));
  });
  test(`${row.path}: actual empty widget renders the contextual Italian description`, (t) => {
    const e = environment(t);
    const chart = JSON.parse(readFileSync(resolve(root, row.path), "utf8"));
    const result = render(e, chart);
    assert.equal(result.text().trim(), row.italian);
    assert.equal(result.find("a,button,input").length, 0);
  });
}

test("English fallback preserves the same complete description and canonical chart configuration", (t) => {
  const e = environment(t, {});
  for (const row of cases) {
    const chart = JSON.parse(readFileSync(resolve(root, row.path), "utf8"));
    assert.equal(render(e, chart).text().trim(), chart.empty_state_message);
  }
});

test("missing descriptions retain the existing localized generic fallback", (t) => {
  const e = environment(t, { "No data yet": "Nessun dato disponibile" });
  for (const value of [undefined, null, ""]) {
    const chart = { type: "Bar", empty_state_message: value, y_axis: [] };
    assert.equal(render(e, chart).text().trim(), "Nessun dato disponibile");
  }
});

test("authored unknown descriptions remain literal text with no HTML interpretation", (t) => {
  const e = environment(t);
  const message =
    '<img src="data:raw" onerror="raw()"> Memory customer & supplier';
  const chart = {
    type: "Donut",
    empty_state_message: message,
    y_axis: [],
    filters_json: '{"customer":"Memory <customer>"}',
  };
  const result = render(e, chart);
  assert.equal(result.text().trim(), message);
  assert.equal(result.find("img,script").length, 0);
});

test("the focused test file contributes no configured translation keys", () => {
  assert.deepEqual(extraction.at(-1).messages, []);
});
