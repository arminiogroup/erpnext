import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function setup(expectedHours, timestamp) {
  const elements = new Map();
  const messages = [];
  const sounds = [];
  const cleared = [];
  let tick;
  let saves = 0;
  let visible = true;
  let hidden = false;
  const element = (selector) => {
    if (!elements.has(selector)) {
      elements.set(selector, {
        text(value) {
          this.value = value;
        },
        hide() {
          this.visible = false;
        },
        show() {
          this.visible = true;
        },
        click(callback) {
          this.callback = callback;
        },
        is() {
          return visible;
        },
      });
    }
    return elements.get(selector);
  };
  const row = { idx: 1, completed: 0 };
  const values = {
    activity_type: "Activity Type",
    project: "PROJECT-RAW",
    task: "TASK-RAW",
    expected_hours: expectedHours,
  };
  const dialog = {
    $wrapper: { find: element },
    get_value: () => expectedHours,
    get_values: () => values,
    hide: () => {
      hidden = true;
    },
  };
  const frm = {
    fields_dict: {
      time_logs: { grid: { get_row: () => ({ doc: row, refresh() {} }) } },
    },
    dirty() {},
    save: () => {
      saves += 1;
    },
  };
  const sandbox = {
    $: element,
    erpnext: { timesheet: {} },
    cur_dialog: dialog,
    frappe: {
      provide() {},
      utils: { play_sound: (value) => sounds.push(value) },
      msgprint: (message) => messages.push(message),
      datetime: { get_datetime_as_string: () => "2026-01-01 10:00:00" },
      _messages: {
        "Timer exceeded the given hours.":
          "Il timer ha superato la durata prevista.",
        "Timer has reached the expected duration.":
          "Il timer ha raggiunto la durata prevista.",
      },
    },
    setInterval(callback, delay) {
      assert.equal(delay, 1000);
      tick = callback;
      return "INTERVAL-RAW";
    },
    clearInterval: (id) => cleared.push(id),
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(
    readFileSync(
      new URL(
        "../../../../../frappe/frappe/public/js/frappe/translate.js",
        import.meta.url
      ),
      "utf8"
    ),
    context
  );
  vm.runInContext(
    readFileSync(new URL("./timer.js", import.meta.url), "utf8"),
    context
  );
  sandbox.erpnext.timesheet.control_timer(frm, dialog, row, timestamp);
  return {
    tick: () => tick(),
    messages,
    sounds,
    elements,
    cleared,
    row,
    values,
    hideModal: () => {
      visible = false;
    },
    get saves() {
      return saves;
    },
    get hidden() {
      return hidden;
    },
  };
}

test("the alert includes the exact expected-duration boundary and fires once", () => {
  const state = setup(1, 3598);
  state.tick();
  assert.deepEqual(state.messages, []);
  state.tick();
  assert.deepEqual(state.messages, [
    "Il timer ha raggiunto la durata prevista.",
  ]);
  assert.deepEqual(state.sounds, ["alert"]);
  assert.equal(state.elements.get(".hours").value, "01");
  assert.equal(state.elements.get(".minutes").value, "00");
  assert.equal(state.elements.get(".seconds").value, "00");
  state.tick();
  assert.equal(state.messages.length, 1);
  assert.equal(state.saves, 0);
});

test("resuming above the limit uses the same inclusive wording", () => {
  const state = setup(1, 3600);
  state.tick();
  assert.deepEqual(state.messages, [
    "Il timer ha raggiunto la durata prevista.",
  ]);
  assert.deepEqual(state.sounds, ["alert"]);
});

test("an absent or nonpositive expected duration stays silent", () => {
  for (const expected of [0, -1, undefined, null]) {
    const state = setup(expected, 3600);
    state.tick();
    assert.deepEqual(state.messages, []);
    assert.deepEqual(state.sounds, []);
    assert.equal(state.saves, 0);
  }
});

test("closing or completing retains reset, time-log values and save behavior", () => {
  const closed = setup(1, 0);
  closed.hideModal();
  closed.tick();
  assert.deepEqual(closed.cleared, ["INTERVAL-RAW"]);
  assert.deepEqual(closed.messages, []);
  const state = setup(1, 0);
  state.elements.get(".playpause .btn-complete").callback();
  assert.equal(state.row.completed, 1);
  for (const [key, value] of Object.entries(state.values))
    assert.equal(state.row[key], value);
  assert.equal(state.row.to_time, "2026-01-01 10:00:00");
  assert.deepEqual(state.cleared, ["INTERVAL-RAW"]);
  assert.equal(state.saves, 1);
  assert.equal(state.hidden, true);
});
