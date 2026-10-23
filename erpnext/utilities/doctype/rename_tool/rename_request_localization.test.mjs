import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const frappeRoot = fileURLToPath(
  new URL("../../../../../frappe/", import.meta.url)
);
const source = readFileSync(
  new URL("./rename_tool.js", import.meta.url),
  "utf8"
);
const frames = [
  "Rename request for {0} sent.",
  "Rename request for {0} failed.",
];
const targets = [
  "Richiesta di rinomina per {0} inviata.",
  "Richiesta di rinomina per {0} non riuscita.",
];
const types = {
  Customer: "Cliente",
  Employee: "Dipendente",
  "Purchase Order": "Ordine di acquisto",
};

function environment(
  messages = { [frames[0]]: targets[0], [frames[1]]: targets[1], ...types }
) {
  const events = [],
    notices = [],
    pending = [],
    errors = [];
  let handler, primary;
  const body = {
    attr: (...args) => {
      events.push(["body", ...args]);
    },
  };
  const $ = (value) =>
    value === "body" ? body : { ajaxSend() {}, ajaxComplete() {} };
  $.extend = Object.assign;
  $.isPlainObject = (value) => value?.constructor === Object;
  $.isArray = Array.isArray;
  $.ajax = (options) => {
    const callbacks = {};
    const request = { options, callbacks };
    pending.push(request);
    const result = {};
    for (const name of ["done", "always", "fail"]) {
      result[name] = (callback) => {
        callbacks[name] = callback;
        return result;
      };
    }
    return result;
  };
  const scope = {
    $,
    jQuery: $,
    document: {},
    console: {
      log: (...args) => errors.push(args),
      error: (...args) => errors.push(args),
    },
    frappe: {
      _messages: messages,
      boot: {},
      session: { user: "translator@example.com" },
      is_online: () => true,
      csrf_token: "MEMORY-TOKEN",
      ui: {
        form: {
          on: (doctype, events) => {
            assert.equal(doctype, "Rename Tool");
            handler = events;
          },
        },
      },
      dom: {
        freeze: (message) => events.push(["freeze", message]),
        unfreeze: () => events.push(["unfreeze"]),
      },
      msgprint: (notice) => notices.push(notice),
      db: { get_list: async () => [] },
    },
  };
  scope.window = scope;
  scope.frappe.provide = (path) => {
    let current = scope;
    for (const part of path.split(".")) current = current[part] ||= {};
  };
  const context = vm.createContext(scope);
  for (const path of [
    "frappe/public/js/frappe/format.js",
    "frappe/public/js/frappe/translate.js",
    "frappe/public/js/frappe/request.js",
  ]) {
    vm.runInContext(readFileSync(frappeRoot + path, "utf8"), context, {
      filename: path,
    });
  }
  vm.runInContext(source, context, { filename: "rename_tool.js" });
  const doc = {
    select_doctype: "Customer",
    file_to_rename: "/private/files/RAW.csv",
  };
  const fields = {
    file_to_rename: { df: {} },
    rename_log: { $wrapper: { html: (html) => events.push(["html", html]) } },
  };
  const frm = {
    doc,
    disable_save: () => events.push(["disable_save"]),
    get_field: (field) => fields[field],
    trigger: (event) => events.push(["trigger", event]),
    page: {
      set_primary_action: (label, action) => {
        primary = action;
        events.push(["primary", label]);
      },
    },
    set_value: (field, value) => {
      events.push(["set_value", field, value]);
      doc[field] = value;
    },
  };
  return {
    scope,
    handler,
    frm,
    fields,
    events,
    notices,
    errors,
    pending,
    start: () => primary(),
  };
}

function deliver(request, success) {
  const response = success ? {} : { message: "RAW-BACKEND-ERROR" };
  const xhr = {
    responseJSON: response,
    responseText: JSON.stringify(response),
    statusCode: () => ({ status: success ? 200 : 417 }),
    getResponseHeader: () => "application/json",
  };
  if (success) request.callbacks.done(response, "success", xhr);
  else request.callbacks.fail(xhr, "error");
  request.callbacks.always(
    success ? response : xhr,
    success ? "success" : "error",
    xhr
  );
}

function run(doctype, success, messages) {
  const env = environment(messages);
  env.frm.doc.select_doctype = doctype;
  env.handler.refresh(env.frm);
  env.start();
  assert.equal(env.pending.length, 1);
  const request = env.pending[0];
  assert.equal(
    request.options.url,
    "/api/method/erpnext.utilities.doctype.rename_tool.rename_tool.upload"
  );
  assert.equal(request.options.type, "POST");
  assert.deepEqual(JSON.parse(JSON.stringify(request.options.data)), {
    select_doctype: doctype,
    file_to_rename: "/private/files/RAW.csv",
  });
  assert.deepEqual(
    JSON.parse(JSON.stringify(env.fields.file_to_rename.df.options)),
    { restrictions: { allowed_file_types: [".csv"] } }
  );
  deliver(request, success);
  assert.deepEqual(env.errors, []);
  assert.equal(env.notices.length, 1);
  assert.equal(env.notices[0].alert, true);
  assert.equal(env.notices[0].indicator, success ? "green" : "red");
  assert.equal(env.frm.doc.select_doctype, success ? "" : doctype);
  assert.equal(
    env.frm.doc.file_to_rename,
    success ? "" : "/private/files/RAW.csv"
  );
  assert.equal(env.events.filter(([name]) => name === "trigger").length, 2);
  assert.equal(env.events.filter(([name]) => name === "freeze").length, 1);
  assert.equal(env.events.filter(([name]) => name === "unfreeze").length, 1);
  assert.equal(
    env.events.filter(([name]) => name === "set_value").length,
    success ? 2 : 0
  );
  return env;
}

test("real request 200 completion localizes a request notice without requiring a job receipt", () => {
  for (const [doctype, target] of Object.entries(types)) {
    assert.equal(
      run(doctype, true).notices[0].message,
      targets[0].replace("{0}", target)
    );
  }
});

test("real request 417 rejection localizes a failed request and keeps attached inputs", () => {
  for (const [doctype, target] of Object.entries(types)) {
    assert.equal(
      run(doctype, false).notices[0].message,
      targets[1].replace("{0}", target)
    );
  }
});

test("custom and absent type labels keep actual translator and formatter fallbacks", () => {
  for (const doctype of ["Custom Document", "", null]) {
    for (const success of [true, false]) {
      assert.equal(
        run(doctype, success).notices[0].message,
        targets[success ? 0 : 1].replace("{0}", String(doctype))
      );
    }
  }
});

test("English and empty targets fall back to the new whole source frames", () => {
  for (const success of [true, false]) {
    for (const messages of [
      {},
      { [frames[0]]: "", [frames[1]]: "", Customer: "" },
    ]) {
      assert.equal(
        run("Customer", success, messages).notices[0].message,
        frames[success ? 0 : 1].replace("{0}", "Customer")
      );
    }
  }
});

test("overview preserves raw status filters, routes and its existing Failed context", async () => {
  const env = environment({
    "Failed:Rename Tool": "Non riuscite",
    Failed: "GENERIC-FAILURE",
  });
  let query;
  env.scope.frappe.db.get_list = async (...args) => {
    query = args;
    return [
      {
        job_name:
          "erpnext.utilities.doctype.rename_tool.rename_tool.rename_rows",
        status: "failed",
      },
      { job_name: "UNRELATED", status: "queued" },
    ];
  };
  env.handler.render_overview(env.frm);
  await Promise.resolve();
  assert.deepEqual(JSON.parse(JSON.stringify(query)), [
    "RQ Job",
    {
      filters: { status: ["in", ["started", "queued", "finished", "failed"]] },
    },
  ]);
  const html = env.events.find(([name]) => name === "html")[1];
  assert.match(
    html,
    /href="\/app\/rq-job\?queue=long&status=failed">Non riuscite: 1/
  );
  assert.match(html, /status=queued">Queued: 0/);
  assert.doesNotMatch(html, /GENERIC-FAILURE/);
});
