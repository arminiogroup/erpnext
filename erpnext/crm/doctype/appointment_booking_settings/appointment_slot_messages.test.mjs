import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = "Row {0}: To Time cannot be earlier than From Time.";
const target = "Riga {0}: l’ora di fine non può precedere l’ora di inizio.";

function setup(lang) {
  let validate;
  const jquery = {
    each: (rows, callback) =>
      rows.forEach((row, index) => callback(index, row)),
  };
  const sandbox = {
    $: jquery,
    jQuery: jquery,
    frappe: {
      _messages: lang === "it" ? { [source]: target } : {},
      ui: {
        form: {
          on(doctype, event, handler) {
            assert.equal(doctype, "Appointment Booking Settings");
            assert.equal(event, "validate");
            validate = handler;
          },
        },
      },
      throw(message) {
        throw new Error(message);
      },
    },
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  for (const path of [
    "../../../../../frappe/frappe/public/js/frappe/format.js",
    "../../../../../frappe/frappe/public/js/frappe/translate.js",
    "./appointment_booking_settings.js",
  ]) {
    vm.runInContext(
      readFileSync(new URL(path, import.meta.url), "utf8"),
      context
    );
  }
  return (rows) => validate({ doc: { availability_of_slots: rows } });
}

test("reversed rows show the whole Italian message and retain the correct row number", () => {
  const validate = setup("it");
  const rows = [
    { day_of_week: "Monday", from_time: "09:00:00", to_time: "10:00:00" },
    { day_of_week: "Tuesday", from_time: "11:00:00", to_time: "10:00:00" },
  ];
  const before = structuredClone(rows);
  assert.throws(() => validate(rows), {
    message: "Riga 2: l’ora di fine non può precedere l’ora di inizio.",
  });
  assert.deepEqual(rows, before);
});

test("English fallback describes the unchanged inclusive boundary", () => {
  const validate = setup("en");
  assert.throws(
    () => validate([{ from_time: "18:00:00", to_time: "09:00:00" }]),
    { message: "Row 1: To Time cannot be earlier than From Time." }
  );
});

test("equal and forward times remain accepted in either language", () => {
  for (const lang of ["it", "en"]) {
    const validate = setup(lang);
    for (const to_time of ["09:00:00", "09:30:00", "10:00:00"]) {
      const rows = [{ day_of_week: "Monday", from_time: "09:00:00", to_time }];
      const before = structuredClone(rows);
      assert.equal(validate(rows), undefined);
      assert.deepEqual(rows, before);
    }
  }
});

test("empty tables keep the existing client behavior", () => {
  for (const rows of [undefined, null, []]) {
    assert.equal(setup("it")(rows), undefined);
  }
});
