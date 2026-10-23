import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const frappeRoot = fileURLToPath(
  new URL("../../../../../frappe/", import.meta.url)
);
const source = readFileSync(
  new URL("./incoming_call_settings.js", import.meta.url),
  "utf8"
);
const weekdays = {
  Monday: "Lunedì",
  Tuesday: "Martedì",
  Wednesday: "Mercoledì",
  Thursday: "Giovedì",
  Friday: "Venerdì",
  Saturday: "Sabato",
  Sunday: "Domenica",
};
const frame = "Please fix overlapping time slots for {0}";
const italian = "Correggere le fasce orarie sovrapposte per {0}.";

function environment(messages = { [frame]: italian, ...weekdays }) {
  let handler;
  const scope = {
    frappe: {
      _messages: messages,
      ui: {
        form: {
          on: (doctype, events) => {
            assert.equal(doctype, "Incoming Call Settings");
            handler = events;
          },
        },
      },
      throw: (message) => {
        throw new Error(message);
      },
    },
    $: {},
  };
  scope.window = scope;
  scope.jQuery = scope.$;
  const context = vm.createContext(scope);
  for (const path of [
    "frappe/public/js/frappe/format.js",
    "frappe/public/js/frappe/translate.js",
  ]) {
    vm.runInContext(readFileSync(frappeRoot + path, "utf8"), context, {
      filename: path,
    });
  }
  vm.runInContext(source, context, {
    filename: fileURLToPath(
      new URL("./incoming_call_settings.js", import.meta.url)
    ),
  });
  return (slots) => {
    const schedule = slots.map(([day, from, to], index) => ({
      idx: index + 1,
      day_of_week: day,
      from_time: from,
      to_time: to,
      agent_group: "RAW-GROUP",
    }));
    const before = structuredClone(schedule);
    let message;
    try {
      handler.validate({ doc: { call_handling_schedule: schedule } });
    } catch (error) {
      message = error.message;
    } finally {
      assert.deepEqual(schedule, before);
    }
    return message;
  };
}

test("all seven canonical weekdays are localized in the actual form validator", () => {
  const validate = environment();
  for (const [day, target] of Object.entries(weekdays)) {
    assert.equal(
      validate([
        [day, "09:00:00", "12:00:00"],
        [day, "11:00:00", "13:00:00"],
      ]),
      italian.replace("{0}", target)
    );
  }
});

test("custom weekdays, empty translations and English retain source fallback", () => {
  for (const [messages, day, expected] of [
    [{ [frame]: italian }, "Custom Day", italian.replace("{0}", "Custom Day")],
    [
      { [frame]: italian, Monday: "" },
      "Monday",
      italian.replace("{0}", "Monday"),
    ],
    [{}, "Monday", frame.replace("{0}", "Monday")],
  ]) {
    assert.equal(
      environment(messages)([
        [day, "09:00:00", "12:00:00"],
        [day, "11:00:00", "13:00:00"],
      ]),
      expected
    );
  }
});

test("adjacent and disjoint slots, empty rows and separate days stay valid", () => {
  const validate = environment();
  for (const slots of [
    [],
    [
      ["Monday", "09:00:00", "12:00:00"],
      ["Monday", "12:00:00", "13:00:00"],
    ],
    [
      ["Monday", "09:00:00", "10:00:00"],
      ["Monday", "11:00:00", "12:00:00"],
    ],
    [
      ["Monday", "09:00:00", "12:00:00"],
      ["Tuesday", "09:00:00", "12:00:00"],
    ],
  ]) {
    assert.equal(validate(slots), undefined);
  }
});

test("duration rejections and multi-day grouping keep existing client semantics", () => {
  const validate = environment();
  for (const [from, to] of [
    ["09:00:00", "09:00:00"],
    ["10:00:00", "09:00:00"],
  ]) {
    assert.equal(
      validate([["Monday", from, to]]),
      "Call Schedule Row 0: To time slot should always be ahead of From time slot."
    );
  }
  assert.equal(
    validate([
      ["Tuesday", "09:00:00", "10:00:00"],
      ["Monday", "09:00:00", "12:00:00"],
      ["Monday", "11:00:00", "13:00:00"],
    ]),
    "Correggere le fasce orarie sovrapposte per Lunedì."
  );
});
