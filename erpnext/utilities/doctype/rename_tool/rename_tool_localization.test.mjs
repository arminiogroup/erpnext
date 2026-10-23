import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

for (const failed of [0, 1, 2]) {
  test(`actual rename overview contextualizes ${failed} failures and preserves job filter`, async () => {
    const reads = [];
    const handlers = {};
    const jobs = [
      ...Array.from({ length: failed }, () => ({
        job_name:
          "erpnext.utilities.doctype.rename_tool.rename_tool.rename_rows",
        status: "failed",
      })),
      { job_name: "unrelated.business_job", status: "failed" },
      ...["queued", "started", "finished"].map((status) => ({
        job_name:
          "erpnext.utilities.doctype.rename_tool.rename_tool.rename_rows",
        status,
      })),
    ];
    const before = structuredClone(jobs);
    const host = {
      $: {},
      frappe: {
        ui: { form: { on: (doctype, events) => (handlers[doctype] = events) } },
        db: {
          get_list: async (...args) => {
            reads.push(args);
            return jobs;
          },
        },
        _messages: {
          Failed: "Fallito",
          "Failed:Rename Tool": "Operazioni non riuscite",
        },
      },
    };
    host.window = host;
    host.jQuery = host.$;
    const context = vm.createContext(host);
    for (const file of ["format.js", "translate.js"])
      vm.runInContext(
        readFileSync(
          new URL(
            `../../../../../frappe/frappe/public/js/frappe/${file}`,
            import.meta.url
          ),
          "utf8"
        ),
        context
      );
    vm.runInContext(
      readFileSync(new URL("./rename_tool.js", import.meta.url), "utf8"),
      context
    );
    let html = "";
    const frm = {
      get_field(name) {
        assert.equal(name, "rename_log");
        return { $wrapper: { html: (value) => (html = value) } };
      },
    };
    handlers["Rename Tool"].render_overview(frm);
    await Promise.resolve();
    assert.ok(
      html.includes(
        `<a href="/app/rq-job?queue=long&status=failed">Operazioni non riuscite: ${failed}</a>`
      )
    );
    for (const status of ["queued", "started", "finished"])
      assert.match(html, new RegExp(`status=${status}">[^<]+: 1</a>`));
    assert.deepEqual(JSON.parse(JSON.stringify(reads)), [
      [
        "RQ Job",
        {
          filters: {
            status: ["in", ["started", "queued", "finished", "failed"]],
          },
        },
      ],
    ]);
    assert.deepEqual(jobs, before);
    assert.equal(host.__("Failed"), "Fallito");
  });
}
