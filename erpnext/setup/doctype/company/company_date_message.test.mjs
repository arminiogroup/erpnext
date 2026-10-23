import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const directory = path.dirname(fileURLToPath(import.meta.url));
const appDirectory = path.resolve(directory, "../../../..");
const translatePath = path.resolve(
  appDirectory,
  "../frappe/frappe/public/js/frappe/translate.js"
);
const message = "Date of Commencement cannot be before Date of Incorporation.";
const italian =
  "La data di inizio attività non può precedere la data di costituzione.";

function companyForm(dictionary = {}) {
  const registrations = [];
  const frappe = {
    _messages: dictionary,
    provide(name) {
      assert.equal(name, "erpnext.company");
    },
    ui: {
      form: {
        on(doctype, handlers) {
          registrations.push({ doctype, handlers });
        },
      },
    },
    throw(text) {
      throw new Error(text);
    },
  };
  const context = vm.createContext({ frappe, erpnext: { company: {} } });
  context.window = context;
  vm.runInContext(readFileSync(translatePath, "utf8"), context, {
    filename: translatePath,
  });
  vm.runInContext(
    readFileSync(path.join(directory, "company.js"), "utf8"),
    context,
    {
      filename: "company.js",
    }
  );
  assert.equal(registrations.length, 1);
  assert.equal(registrations[0].doctype, "Company");
  return registrations[0].handlers.date_of_commencement;
}

function document(commencement, incorporation) {
  return {
    doctype: "Company",
    name: "Company <raw>",
    date_of_commencement: commencement,
    date_of_incorporation: incorporation,
    default_currency: "EUR",
    custom_date: "2026-01-01",
  };
}

test("the registered callback explains the earlier-date rejection in Italian", () => {
  const callback = companyForm({ [message]: italian });
  const doc = document("2026-10-19", "2026-10-20");
  const before = structuredClone(doc);
  assert.throws(() => callback({ doc }), { message: italian });
  assert.deepEqual(doc, before);
});

test("the English message also states the inclusive boundary", () => {
  const doc = document("2026-10-19", "2026-10-20");
  assert.throws(() => companyForm()({ doc }), { message });
});

test("equal and later dates remain allowed without document mutation", () => {
  const callback = companyForm();
  for (const value of ["2026-10-20", "2026-10-21"]) {
    const doc = document(value, "2026-10-20");
    const before = structuredClone(doc);
    callback({ doc });
    assert.deepEqual(doc, before);
  }
});

test("existing blank and undefined comparisons and subsequent clearing remain intact", () => {
  const callback = companyForm();
  for (const value of [null, undefined, ""]) {
    const doc = document(value, undefined);
    callback({ doc });
    assert.equal(doc.date_of_commencement, value);
    assert.equal(doc.date_of_incorporation, "");
    assert.equal(doc.default_currency, "EUR");
  }
  const empty = document("", "2026-10-20");
  assert.throws(() => callback({ doc: empty }));
  assert.equal(empty.date_of_incorporation, "2026-10-20");
  const nullDate = document(null, "2026-10-20");
  callback({ doc: nullDate });
  assert.equal(nullDate.date_of_incorporation, "");
});

test("canonical comparison outcomes and complete post-state matrix", () => {
  const values = [
    undefined,
    null,
    "",
    "2026-10-19",
    "2026-10-20",
    "2026-10-21",
  ];
  const callback = companyForm();
  const records = [];
  for (const commencement of values) {
    for (const incorporation of values) {
      const doc = document(commencement, incorporation);
      let rejected = false;
      try {
        callback({ doc });
      } catch (error) {
        assert.ok(error instanceof Error);
        rejected = true;
      }
      assert.equal(rejected, commencement < incorporation);
      records.push({
        input: [commencement, incorporation],
        rejected,
        post_state: doc,
      });
    }
  }
  const serialised = JSON.stringify(records, (key, value) =>
    value === undefined ? { undefined: true } : value
  );
  const digest = createHash("sha256").update(serialised).digest("hex");
  console.log(
    `COMPANY_DATE_PARITY ${JSON.stringify({
      cases: records.length,
      digest,
      records,
    })}`
  );
});
