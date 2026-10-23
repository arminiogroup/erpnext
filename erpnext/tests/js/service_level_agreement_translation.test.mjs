import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../.."
);
const FRAPPE_ROOT =
  process.env.FRAPPE_SOURCE_ROOT || path.resolve(ROOT, "../../frappe/frappe");
const require = createRequire(path.join(FRAPPE_ROOT, "../package.json"));
const moment = require("moment");
require("moment/locale/it");
const CUSTOM_TYPE = "Test SLA on Custom Dt";
const TARGET_TYPES = ["Lead", "Task", CUSTOM_TYPE];
const NON_FULFILLED =
  "Le condizioni dell’accordo sul livello di servizio non risultano soddisfatte.";
const MESSAGES = {
  "This SLA is the default for documents of type {0}.":
    "Questo accordo sul livello di servizio è quello predefinito per i documenti di tipo {0}.",
  "Enable to make this SLA the default for documents of type {0}.":
    "Attivare per usare questo accordo sul livello di servizio come predefinito per i documenti di tipo {0}.",
  "SLA applies to documents of type {0} associated with {1}: {2}.":
    "L’accordo sul livello di servizio si applica ai documenti di tipo {0} associati a {1}: {2}.",
  "SLA applies to documents of type {0} associated with {1}: {2}, including descendants.":
    "L’accordo sul livello di servizio si applica ai documenti di tipo {0} associati a {1}: {2}, inclusi i discendenti.",
  "Service Level Agreement has been fulfilled":
    "Accordo sul livello di servizio rispettato",
  "Service Level Agreement is not fulfilled.": NON_FULFILLED,
  "Time to Respond: {0}": "Prima risposta: {0}",
  "Time to Resolve: {0}": "Risoluzione: {0}",
  "Less than a minute until the SLA deadline":
    "Meno di un minuto alla scadenza",
  "SLA deadline reached": "Scadenza raggiunta",
  "SLA deadline passed": "Scadenza superata",
  "SLA deadline unavailable": "Scadenza non disponibile",
  "SLA deadline met": "Scadenza rispettata",
  "SLA deadline missed": "Scadenza non rispettata",
  "SLA is on hold since {0}": "Lo SLA è sospeso da {0}",
  Lead: "Contatto commerciale",
  Task: "Attività",
  Customer: "Cliente",
  "Customer Group": "Gruppo clienti",
  Territory: "Territorio",
};

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function load() {
  moment.locale("it");
  moment.now = () => Date.parse("2026-10-21T12:00:00");
  const handlers = {};
  const events = [];
  const calls = [];
  const metadata_reads = [];
  const metadata = Object.fromEntries(
    [
      ["crm", "Lead"],
      ["projects", "Task"],
    ].map(([module, name]) => {
      const slug = name.toLowerCase();
      return [
        name,
        JSON.parse(
          fs.readFileSync(
            path.join(ROOT, module, "doctype", slug, `${slug}.json`),
            "utf8"
          )
        ),
      ];
    })
  );
  metadata[CUSTOM_TYPE] = {
    fields: [
      {
        fieldname: "status",
        label: "Status",
        fieldtype: "Select",
        options: "Open\nReplied\nClosed\nFailed",
      },
    ],
  };
  const jquery = () => ({
    on: (event, callback) => events.push({ event, callback }),
  });
  jquery.extend = Object.assign;
  jquery.each = (items, callback) =>
    items.forEach((value, index) => callback(index, value));
  const frappe = {
    _messages: MESSAGES,
    boot: { service_level_agreement_doctypes: TARGET_TYPES },
    model: {
      core_doctypes_list: ["DocType", "User"],
      with_doctype: (doctype, callback) => {
        metadata_reads.push(doctype);
        callback();
      },
    },
    meta: {
      get_docfield: (doctype, fieldname) =>
        metadata[doctype].fields.find((field) => field.fieldname === fieldname),
    },
    ui: {
      form: {
        on: (doctype, registered) => {
          handlers[doctype] = registered;
        },
      },
    },
    form: { link_formatters: {} },
    datetime: {
      is_timezone_same: () => true,
      system_datetime: () => moment("2026-10-21T12:00:00"),
    },
    call: (args) => calls.push(args),
  };
  const context = vm.createContext({
    frappe,
    jQuery: jquery,
    $: jquery,
    window: {},
    document: {},
    moment,
    cint: Number,
  });
  frappe.provide = (name) => {
    let target = context;
    for (const part of name.split(".")) {
      target = target[part] || (target[part] = {});
    }
  };
  for (const name of ["format.js", "translate.js"]) {
    vm.runInContext(
      fs.readFileSync(path.join(FRAPPE_ROOT, "public/js/frappe", name), "utf8"),
      context
    );
  }
  context.__ = context.window.__;
  for (const relative of [
    "support/doctype/service_level_agreement/service_level_agreement.js",
    "public/js/utils.js",
  ]) {
    vm.runInContext(
      fs.readFileSync(path.join(ROOT, relative), "utf8"),
      context
    );
  }
  for (const event of events) {
    assert.equal(event.event, "app_ready");
    event.callback();
  }
  return { handlers, calls, context, metadata, metadata_reads };
}

function form(values = {}) {
  const descriptions = {};
  const updates = [];
  const queries = [];
  const triggers = [];
  const refreshed = [];
  const headlines = [];
  const model_updates = [];
  let cleared = 0;
  const fields = {};
  for (const name of [
    "priorities",
    "pause_sla_on",
    "sla_fulfilled_on",
    "default_service_level_agreement",
    "entity",
  ]) {
    fields[name] = {
      grid: {
        update_docfield_property: (...args) => updates.push([name, ...args]),
      },
      set_description: (message) => {
        descriptions[name] = message;
      },
    };
  }
  const frm = {
    doc: {
      doctype: "Lead",
      name: "TARGET-RAW",
      customer: "Customer",
      service_level_agreement: "SLA-RAW",
      status: "Open",
      agreement_status: "First Response Due",
      response_by: "2026-10-21T14:00:00",
      sla_resolution_by: "2026-10-21T15:00:00",
      ...values,
    },
    get_field: (field) => fields[field],
    fields_dict: fields,
    set_query: (...args) => queries.push(args),
    trigger: (event) => triggers.push(event),
    refresh_field: (field) => refreshed.push(field),
    set_value: (field, value) => {
      model_updates.push([field, value]);
      frm.doc[field] = value;
    },
    dashboard: {
      clear_headline: () => {
        cleared++;
      },
      set_headline_alert: (html) => headlines.push(html),
    },
  };
  return {
    frm,
    descriptions,
    updates,
    queries,
    triggers,
    refreshed,
    headlines,
    model_updates,
    get cleared() {
      return cleared;
    },
  };
}

function configure_form(values = {}) {
  return form({
    doctype: "Service Level Agreement",
    document_type: "Lead",
    default_service_level_agreement: 1,
    entity_type: "Customer",
    entity: "Customer",
    ...values,
  });
}

describe("Complete SLA descriptions and headlines", () => {
  it("labels default settings for standard and arbitrary document types", () => {
    const { handlers } = load();
    for (const [doctype, label] of [
      ["Lead", "Contatto commerciale"],
      ["Task", "Attività"],
      [CUSTOM_TYPE, CUSTOM_TYPE],
    ]) {
      for (const enabled of [0, 1]) {
        const state = configure_form({
          document_type: doctype,
          default_service_level_agreement: enabled,
        });
        const before = plain(state.frm.doc);
        handlers["Service Level Agreement"].default_service_level_agreement(
          state.frm
        );
        assert.equal(
          state.descriptions.default_service_level_agreement,
          enabled
            ? `Questo accordo sul livello di servizio è quello predefinito per i documenti di tipo ${label}.`
            : `Attivare per usare questo accordo sul livello di servizio come predefinito per i documenti di tipo ${label}.`
        );
        assert.deepEqual(state.frm.doc, before);
      }
    }
  });

  it("uses complete exact and descendant entity descriptions with raw business names", () => {
    const { handlers } = load();
    for (const [doctype, label] of [
      ["Lead", "Contatto commerciale"],
      ["Task", "Attività"],
      [CUSTOM_TYPE, CUSTOM_TYPE],
    ]) {
      for (const [entity_type, entity_label] of [
        ["Customer", "Cliente"],
        ["Customer Group", "Gruppo clienti"],
        ["Territory", "Territorio"],
      ]) {
        const state = configure_form({ document_type: doctype, entity_type });
        const before = plain(state.frm.doc);
        handlers["Service Level Agreement"].entity(state.frm);
        assert.equal(
          state.descriptions.entity,
          `L’accordo sul livello di servizio si applica ai documenti di tipo ${label} associati a ${entity_label}: Customer${
            entity_type === "Customer" ? "." : ", inclusi i discendenti."
          }`
        );
        assert.deepEqual(state.frm.doc, before);
      }
    }
    const state = configure_form({ entity: "" });
    handlers["Service Level Agreement"].entity(state.frm);
    assert.equal(state.descriptions.entity, "");
  });

  it("keeps refresh and type-change trigger order and raw entity clearing", () => {
    const { handlers } = load();
    const state = configure_form();
    handlers["Service Level Agreement"].refresh(state.frm);
    assert.deepEqual(state.triggers, [
      "fetch_status_fields",
      "toggle_resolution_fields",
      "default_service_level_agreement",
      "entity",
    ]);
    state.triggers.length = 0;
    handlers["Service Level Agreement"].document_type(state.frm);
    assert.deepEqual(state.triggers, [
      "fetch_status_fields",
      "default_service_level_agreement",
    ]);
    handlers["Service Level Agreement"].entity_type(state.frm);
    assert.deepEqual(state.model_updates, [["entity", undefined]]);
  });

  it("preserves priority layouts and resolution field requirements", () => {
    const { handlers } = load();
    for (const enabled of [0, 1]) {
      const state = configure_form({ apply_sla_for_resolution: enabled });
      handlers["Service Level Agreement"].setup(state.frm);
      assert.deepEqual(
        plain(state.frm.get_field("priorities").grid.editable_fields),
        enabled
          ? [
              { fieldname: "default_priority", columns: 1 },
              { fieldname: "priority", columns: 2 },
              { fieldname: "response_time", columns: 2 },
              { fieldname: "resolution_time", columns: 2 },
            ]
          : [
              { fieldname: "default_priority", columns: 1 },
              { fieldname: "priority", columns: 2 },
              { fieldname: "response_time", columns: 3 },
            ]
      );
      handlers["Service Level Agreement"].toggle_resolution_fields(state.frm);
      assert.deepEqual(plain(state.updates), [
        ["priorities", "resolution_time", "hidden", 1 - enabled],
        ["priorities", "resolution_time", "reqd", enabled],
      ]);
      assert.deepEqual(state.refreshed, ["priorities"]);
    }
  });

  it("keeps canonical custom target status options and excluded values", () => {
    const { handlers, metadata_reads } = load();
    const state = configure_form({ document_type: CUSTOM_TYPE });
    handlers["Service Level Agreement"].fetch_status_fields(state.frm);
    assert.deepEqual(metadata_reads, [CUSTOM_TYPE]);
    assert.deepEqual(plain(state.updates), [
      ["pause_sla_on", "status", "options", ["", "Replied", "Failed"]],
      [
        "sla_fulfilled_on",
        "status",
        "options",
        ["", "Replied", "Closed", "Failed"],
      ],
    ]);
    assert.deepEqual(state.refreshed, ["pause_sla_on"]);
  });

  it("keeps the document-type query filters canonical", () => {
    const { handlers } = load();
    const state = configure_form();
    handlers["Service Level Agreement"].onload(state.frm);
    assert.equal(state.queries[0][0], "document_type");
    assert.deepEqual(plain(state.queries[0][1]()), {
      filters: [
        ["DocType", "issingle", "=", 0],
        ["DocType", "istable", "=", 0],
        ["DocType", "is_submittable", "=", 0],
        [
          "DocType",
          "name",
          "not in",
          [
            "DocType",
            "User",
            "Service Level Agreement",
            "Cost Center",
            "Company",
          ],
        ],
        [
          "DocType",
          "module",
          "not in",
          [
            "Email",
            "Core",
            "Custom",
            "Event Streaming",
            "Social",
            "Data Migration",
            "Geo",
            "Desk",
          ],
        ],
      ],
    });
  });

  it("registers actual standard and arbitrary target handlers and labels fulfilled headlines", () => {
    const { handlers } = load();
    assert.deepEqual(Object.keys(handlers), [
      "Service Level Agreement",
      ...TARGET_TYPES,
    ]);
    for (const doctype of TARGET_TYPES) {
      const state = form({ doctype, agreement_status: "Fulfilled" });
      const before = plain(state.frm.doc);
      handlers[doctype].refresh(state.frm);
      assert.equal(
        state.headlines[0],
        '<div class="row"><div class="col-xs-12"><span class="indicator whitespace-nowrap green"><span class="hidden-xs">Accordo sul livello di servizio rispettato</span></span> </div></div>'
      );
      assert.deepEqual(state.frm.doc, before);
    }
  });

  it("uses neutral non-fulfillment text for failed and closed-pending cases", () => {
    const { handlers } = load();
    for (const doctype of TARGET_TYPES) {
      for (const [status, agreement_status] of [
        ["Open", "Failed"],
        ["Closed", "First Response Due"],
        ["Closed", "Resolution Due"],
        ["Closed", ""],
      ]) {
        const state = form({ doctype, status, agreement_status });
        const before = plain(state.frm.doc);
        handlers[doctype].refresh(state.frm);
        assert.equal(
          state.headlines[0],
          `<div class="row"><div class="col-xs-12"><span class="indicator whitespace-nowrap red"><span class="hidden-xs">${NON_FULFILLED}</span></span> </div></div>`
        );
        assert.deepEqual(state.frm.doc, before);
      }
    }
  });

  it("leaves documents without an SLA untouched", () => {
    const { handlers, calls } = load();
    const state = form({ service_level_agreement: null });
    handlers.Lead.onload(state.frm);
    handlers.Lead.refresh(state.frm);
    assert.equal(calls.length, 0);
    assert.equal(state.headlines.length, 0);
    assert.equal(state.cleared, 0);
  });

  it("preserves canonical arbitrary-target filter RPC and returned query values", () => {
    const { handlers, calls } = load();
    const state = form({ doctype: CUSTOM_TYPE });
    handlers[CUSTOM_TYPE].onload(state.frm);
    assert.equal(
      calls[0].method,
      "erpnext.support.doctype.service_level_agreement.service_level_agreement.get_service_level_agreement_filters"
    );
    assert.deepEqual(plain(calls[0].args), {
      doctype: CUSTOM_TYPE,
      name: "SLA-RAW",
      customer: "Customer",
    });
    calls[0].callback({
      message: {
        priority: ["Customer"],
        service_level_agreements: ["SLA-RAW"],
      },
    });
    assert.equal(state.queries[0][0], "priority");
    assert.deepEqual(plain(state.queries[0][1]()), {
      filters: { name: ["in", ["Customer"]] },
    });
    assert.equal(state.queries[1][0], "service_level_agreement");
    assert.deepEqual(plain(state.queries[1][1]()), {
      filters: { name: ["in", ["SLA-RAW"]] },
    });
  });

  it("translates complete timer labels while preserving actual Italian durations", () => {
    const { handlers, calls } = load();
    const state = form();
    handlers.Lead.refresh(state.frm);
    assert.equal(calls[0].method, "frappe.client.get");
    assert.deepEqual(plain(calls[0].args), {
      doctype: "Service Level Agreement",
      name: "SLA-RAW",
    });
    calls[0].callback({
      message: { pause_sla_on: [], apply_sla_for_resolution: 1 },
    });
    assert.ok(
      state.headlines[0].includes("<span>Prima risposta: 2 ore</span>")
    );
    assert.ok(state.headlines[0].includes("<span>Risoluzione: 3 ore</span>"));
    assert.equal(
      (state.headlines[0].match(/indicator whitespace-nowrap green/g) || [])
        .length,
      2
    );
  });

  it("preserves on-hold branching and its actual relative-time phrase", () => {
    const { handlers, calls } = load();
    const state = form({
      status: "Replied",
      on_hold_since: "2026-10-21T11:00:00",
    });
    handlers.Lead.refresh(state.frm);
    calls[0].callback({
      message: {
        pause_sla_on: [{ status: "Replied" }],
        apply_sla_for_resolution: 1,
      },
    });
    assert.ok(
      state.headlines[0].includes("indicator whitespace-nowrap orange")
    );
    assert.ok(state.headlines[0].includes("Lo SLA è sospeso da un'ora"));
    assert.ok(!state.headlines[0].includes("Risoluzione:"));
  });

  it("preserves raw helper outcomes and the existing short-future threshold", () => {
    const { context } = load();
    assert.deepEqual(
      plain(context.get_time_left("2026-10-21T12:00:20", "First Response Due")),
      { diff_display: "Failed", indicator: "red" }
    );
    assert.deepEqual(
      plain(context.get_time_left("2026-10-21T11:00:00", "Fulfilled")),
      { diff_display: "Failed", indicator: "green" }
    );
    assert.deepEqual(
      plain(context.get_status("2026-10-21T12:00:00", "2026-10-21T12:00:00")),
      { diff_display: "Fulfilled", indicator: "green" }
    );
    assert.deepEqual(
      plain(context.get_status("2026-10-21T12:00:00", "2026-10-21T12:00:01")),
      { diff_display: "Failed", indicator: "red" }
    );
  });

  it("keeps response-only rendering with a translated completed outcome", () => {
    const { context } = load();
    const state = form({ first_responded_on: "2026-10-21T13:00:00" });
    context.set_time_to_resolve_and_response(state.frm, 0);
    assert.ok(
      state.headlines[0].includes(
        "<span>Prima risposta: Scadenza rispettata</span>"
      )
    );
    assert.ok(!state.headlines[0].includes("Risoluzione:"));
  });

  it("uses the canonical completion field in the direct resolution helper branch", () => {
    const { context } = load();
    const state = form({
      first_responded_on: "2026-10-21T10:00:00",
      response_by: "2026-10-21T11:00:00",
      sla_resolution_by: "2026-10-21T11:00:00",
      sla_resolution_date: "2026-10-21T10:30:00",
    });
    context.set_time_to_resolve_and_response(state.frm, 1);
    assert.ok(
      state.headlines[0].includes(
        "<span>Prima risposta: Scadenza rispettata</span>"
      )
    );
    assert.ok(
      state.headlines[0].includes(
        "<span>Risoluzione: Scadenza rispettata</span>"
      )
    );
    assert.equal(
      context.get_status(
        state.frm.doc.sla_resolution_by,
        state.frm.doc.sla_resolution_date
      ).diff_display,
      "Fulfilled"
    );
  });

  for (const [milliseconds, label] of [
    [-1, "Scadenza superata"],
    [0, "Scadenza raggiunta"],
    [1, "Meno di un minuto alla scadenza"],
    [20000, "Meno di un minuto alla scadenza"],
    [44499, "Meno di un minuto alla scadenza"],
    [44500, "un minuto"],
    [60000, "un minuto"],
  ]) {
    it(`renders truthful pending timer text at ${milliseconds} milliseconds`, () => {
      const { context } = load();
      const deadline = moment("2026-10-21T12:00:00")
        .add(milliseconds, "milliseconds")
        .format("YYYY-MM-DDTHH:mm:ss.SSS");
      const indicator = milliseconds < 44500 ? "red" : "green";
      assert.deepEqual(
        plain(context.get_time_left(deadline, "Resolution Due")),
        {
          diff_display: milliseconds < 44500 ? "Failed" : "un minuto",
          indicator,
        }
      );
      for (const resolution of [false, true]) {
        const state = form({
          response_by: resolution ? "2026-10-21T12:00:00" : deadline,
          first_responded_on: resolution ? "2026-10-21T11:00:00" : null,
          sla_resolution_by: deadline,
          sla_resolution_date: null,
          agreement_status: resolution
            ? "Resolution Due"
            : "First Response Due",
        });
        const before = plain(state.frm.doc);
        context.set_time_to_resolve_and_response(state.frm, Number(resolution));
        const metric = resolution ? "Risoluzione" : "Prima risposta";
        assert.match(
          state.headlines[0],
          new RegExp(
            `indicator whitespace-nowrap ${indicator}">\\s*<span>${metric}: ${label}</span>`
          )
        );
        assert.deepEqual(state.frm.doc, before);
        assert.deepEqual(state.model_updates, []);
      }
    });
  }

  for (const [offset, outcome, indicator, label] of [
    [-1, "Fulfilled", "green", "Scadenza rispettata"],
    [0, "Fulfilled", "green", "Scadenza rispettata"],
    [1, "Failed", "red", "Scadenza non rispettata"],
  ]) {
    it(`labels direct completed metric outcomes at ${offset} milliseconds`, () => {
      const { context } = load();
      const deadline = "2026-10-21T11:00:00";
      const completion = moment(deadline)
        .add(offset, "milliseconds")
        .format("YYYY-MM-DDTHH:mm:ss.SSS");
      assert.deepEqual(plain(context.get_status(deadline, completion)), {
        diff_display: outcome,
        indicator,
      });
      const state = form({
        response_by: deadline,
        first_responded_on: completion,
        sla_resolution_by: deadline,
        sla_resolution_date: completion,
      });
      const before = plain(state.frm.doc);
      context.set_time_to_resolve_and_response(state.frm, 1);
      for (const metric of ["Prima risposta", "Risoluzione"]) {
        assert.match(
          state.headlines[0],
          new RegExp(
            `indicator whitespace-nowrap ${indicator}">\\s*<span>${metric}: ${label}</span>`
          )
        );
      }
      assert.deepEqual(state.frm.doc, before);
    });
  }

  for (const deadline of [undefined, null, ""]) {
    it(`labels an unavailable deadline ${String(
      deadline
    )} without a false outcome`, () => {
      const { context } = load();
      for (const completion of [null, "2026-10-21T11:00:00"]) {
        const state = form({
          response_by: deadline,
          first_responded_on: completion,
          sla_resolution_by: deadline,
          sla_resolution_date: completion,
        });
        const before = plain(state.frm.doc);
        context.set_time_to_resolve_and_response(state.frm, 1);
        assert.ok(
          state.headlines[0].includes(
            "<span>Prima risposta: Scadenza non disponibile</span>"
          )
        );
        assert.ok(
          state.headlines[0].includes(
            "<span>Risoluzione: Scadenza non disponibile</span>"
          )
        );
        assert.deepEqual(plain(state.frm.doc), before);
      }
    });
  }

  it("keeps an unrelated business resolution date out of the registered target SLA timer", () => {
    const { handlers, calls, metadata, context } = load();
    metadata[CUSTOM_TYPE].fields.push({
      fieldname: "resolution_date",
      label: "Business Resolution Date",
      fieldtype: "Datetime",
    });
    const state = form({
      doctype: CUSTOM_TYPE,
      agreement_status: "Resolution Due",
      response_by: "2026-10-21T11:00:00",
      first_responded_on: "2026-10-21T10:00:00",
      sla_resolution_by: "2026-10-21T14:00:00",
      sla_resolution_date: null,
      resolution_date: "2026-10-20T10:00:00",
    });
    const before = plain(state.frm.doc);
    handlers[CUSTOM_TYPE].refresh(state.frm);
    assert.deepEqual(plain(calls[0].args), {
      doctype: "Service Level Agreement",
      name: "SLA-RAW",
    });
    calls[0].callback({
      message: { pause_sla_on: [], apply_sla_for_resolution: 1 },
    });
    assert.deepEqual(
      plain(
        context.get_time_left(state.frm.doc.sla_resolution_by, "Resolution Due")
      ),
      { diff_display: "2 ore", indicator: "green" }
    );
    assert.match(
      state.headlines[0],
      /indicator whitespace-nowrap green">\s*<span>Risoluzione: 2 ore<\/span>/
    );
    assert.deepEqual(state.frm.doc, before);
    assert.deepEqual(state.model_updates, []);
    assert.equal(state.cleared, 1);
  });
});
