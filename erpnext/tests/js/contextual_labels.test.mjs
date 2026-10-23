import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../.."
);
const ORDER_SELECTION =
  "Please select at least one item row to create a Purchase Order or Work Order.";

function load(relative_path) {
  const translations = {
    Issue: "Segnalazione",
    "Issue:Asset": "Assegnato",
    Issued: "Emesso",
    "Issued:Material Request": "Materiale scaricato",
    Lost: "Smarrita",
    "Lost:Quotation": "Perso",
    Latest: "Anzianità minima",
    "Latest:Quotation": "Ultima revisione",
    [ORDER_SELECTION]:
      "Seleziona una riga per creare ordini di acquisto o produzione.",
  };
  const frappe = {
    listview_settings: {},
    query_reports: {},
    defaults: { get_default: () => 3, get_user_default: () => "" },
    datetime: {
      get_today: () => "2026-01-01",
      add_days: (date) => date,
      add_months: (date) => date,
    },
    throw: (message) => {
      throw new Error(message);
    },
  };
  const translate = (source, _replacements, context) =>
    translations[`${source}:${context}`] ?? translations[source] ?? source;
  vm.runInNewContext(fs.readFileSync(path.join(ROOT, relative_path), "utf8"), {
    frappe,
    __: translate,
    flt: (value) => Number(value),
  });
  return frappe;
}

describe("Contextual stock and asset labels", () => {
  it("labels custody assignment without changing the stored Issue filter", () => {
    const frappe = load("assets/doctype/asset/asset_list.js");
    const indicator = frappe.listview_settings.Asset.get_indicator({
      status: "Issue",
    });
    assert.deepEqual(Array.from(indicator), [
      "Assegnato",
      "orange",
      "status,=,Issue",
    ]);
  });

  it("distinguishes material issued from stock from a document being issued", () => {
    const frappe = load(
      "stock/doctype/material_request/material_request_list.js"
    );
    const indicator = frappe.listview_settings[
      "Material Request"
    ].get_indicator({
      docstatus: 1,
      per_ordered: 100,
      material_request_type: "Material Issue",
    });
    assert.deepEqual(Array.from(indicator), [
      "Materiale scaricato",
      "green",
      "per_ordered,=,100",
    ]);
  });

  it("uses quotation meanings for Lost and Latest while retaining raw filters", () => {
    const frappe = load("selling/doctype/quotation/quotation_list.js");
    const settings = frappe.listview_settings.Quotation;
    assert.deepEqual(Array.from(settings.get_indicator({ status: "Lost" })), [
      "Perso",
      "gray",
      "status,=,Lost",
    ]);
    const indicator = settings.get_indicator({
      status: "Open",
      docstatus: 1,
      is_active: 1,
      is_latest_revision: 1,
    });
    assert.deepEqual(Array.from(indicator), [
      "Ultima revisione",
      "orange",
      "is_latest_revision,=,1",
    ]);
  });

  it("asks for order rows rather than valuation reposts in material planning", () => {
    const frappe = load(
      "manufacturing/report/material_requirements_planning_report/material_requirements_planning_report.js"
    );
    let action;
    const report = {
      page: { add_inner_button: (_label, callback) => (action = callback) },
      datatable: { rowmanager: { getCheckedRows: () => [] } },
      data: [],
    };
    frappe.query_report = report;
    frappe.query_reports["Material Requirements Planning Report"].onload(
      report
    );
    assert.throws(action, {
      message: "Seleziona una riga per creare ordini di acquisto o produzione.",
    });
  });
});
