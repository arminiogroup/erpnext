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
const jquery = require("jquery");
const root = fileURLToPath(new URL("../../../", import.meta.url));
const framework = resolve(root, "../frappe");
const employeeSource =
  "Select the employee's date of birth. It cannot be in the future or after the date of joining.";
const employeeItalian =
  "Seleziona la data di nascita del dipendente. Non può essere successiva a oggi né alla data di assunzione.";
const manufacturingSource =
  "When enabled, material consumption can be recorded in separate Stock Entries against a Work Order without immediately manufacturing finished goods.";
const manufacturingItalian =
  "Consente di registrare il consumo di materiale in movimenti di magazzino separati per un ordine di produzione, senza produrre subito i prodotti finiti.";
const paths = [
  "erpnext/setup/doctype/employee/employee.js",
  "erpnext/manufacturing/doctype/manufacturing_settings/manufacturing_settings.js",
  "erpnext/setup/doctype/employee/employee.json",
  "erpnext/manufacturing/doctype/manufacturing_settings/manufacturing_settings.json",
];
const extractionProgram = [
  "import json, sys",
  "from pathlib import Path",
  "import frappe",
  "from babel.messages.extract import extract_from_file",
  "from babel.util import pathmatch",
  "from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map",
  'frappe.init(site="", sites_path=str(Path(sys.argv[1]).parents[1] / "sites"))',
  'methods = get_method_map("erpnext") + get_method_map("frappe")',
  "records = []",
  "for path in json.load(sys.stdin):",
  "    method = next((method for pattern, method in methods if pathmatch(pattern, path)), None)",
  "    messages = list(extract_from_file(method, str(Path(sys.argv[1]) / path), keywords=PYTHON_KEYWORDS)) if method else []",
  "    records.append(dict(path=path,method=method,messages=messages))",
  "print(json.dumps(records))",
].join("\n");
const extraction = spawnSync(
  resolve(root, "../../env/bin/python"),
  ["-c", extractionProgram, root],
  {
    input: JSON.stringify([
      ...paths,
      fileURLToPath(import.meta.url).slice(root.length),
    ]),
    encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  }
);
assert.equal(
  extraction.status,
  0,
  extraction.stderr || extraction.error?.message
);
const records = JSON.parse(extraction.stdout);
const keys = new Set(
  records.flatMap((record) => record.messages.map((message) => message[1]))
);
const translations = Object.fromEntries(
  Object.entries({
    [employeeSource]: employeeItalian,
    [manufacturingSource]: manufacturingItalian,
    "Allow Continuous Material Consumption":
      "Consenti il consumo continuo di materiale",
    "Date of Birth": "Data di nascita",
  }).filter(([source]) => keys.has(source))
);

function environment({ dictionary = translations, authored = null } = {}) {
  const dom = new JSDOM("<body></body>", { url: "https://example.com" });
  const $ = jquery(dom.window);
  const registrations = {};
  const reads = [];
  const drivers = [];
  const context = {
    $,
    jQuery: $,
    console,
    frappe: {
      tour: {},
      _messages: dictionary,
      session: { user: "translator@example.com" },
      ui: {
        form: {
          Controller: class {},
          on: (name, events) => {
            registrations[name] = events;
          },
          set_controller() {},
        },
      },
      router: { slug: (value) => value.toLowerCase(), on() {} },
      db: {
        exists: async (...args) => {
          reads.push(["exists", ...args]);
          return Boolean(authored);
        },
        get_doc: async (...args) => {
          reads.push(["get_doc", ...args]);
          return authored;
        },
      },
      Driver: class {
        constructor(options) {
          this.options = options;
          drivers.push(this);
        }
        defineSteps(steps) {
          this.steps = steps;
        }
      },
    },
  };
  context.window = context;
  context.frappe.provide = (path) => {
    let current = context;
    for (const key of path.split(".")) current = current[key] ??= {};
  };
  const host = vm.createContext(context);
  for (const [base, path] of [
    [framework, "frappe/public/js/frappe/translate.js"],
    [framework, "frappe/public/js/frappe/form/form_tour.js"],
    [root, paths[0]],
    [root, paths[1]],
  ])
    vm.runInContext(readFileSync(resolve(base, path), "utf8"), host, {
      filename: path,
    });
  return {
    context,
    registrations,
    reads,
    drivers,
    close: () => dom.window.close(),
  };
}

async function tour(e, doctype) {
  const frm = {
    doctype,
    layout: { sections: [] },
    is_dirty: () => false,
    get_field: () => null,
    save: () => assert.fail("Save must not run"),
  };
  const instance = new e.context.frappe.ui.form.FormTour({ frm });
  await instance.init({
    on_finish: () => assert.fail("Tour finish must not run"),
  });
  return instance;
}

test("Employee standard tour describes only the actual birth-date checks in Italian", async () => {
  const e = environment();
  try {
    const before = JSON.stringify(e.context.frappe.tour.Employee);
    const instance = await tour(e, "Employee");
    const step = instance.driver_steps.find((step) =>
      step.element.includes("'date_of_birth'")
    );
    assert.equal(step.popover.title, "Data di nascita");
    assert.equal(step.popover.description, employeeItalian);
    assert.equal(step.popover.position, "bottom");
    assert.equal(JSON.stringify(e.context.frappe.tour.Employee), before);
    assert.deepEqual(e.reads, [["exists", "Form Tour", "Employee"]]);
    assert.equal(e.drivers.length, 1);
    assert.equal(e.drivers[0].steps, instance.driver_steps);
    assert.equal(typeof step.onNext, "function");
  } finally {
    e.close();
  }
});

test("Manufacturing tour describes separate consumption entries and matches the unchanged setting label", async () => {
  const e = environment();
  try {
    const data = JSON.parse(readFileSync(resolve(root, paths[3]), "utf8"));
    const df = data.fields.find(
      (field) => field.fieldname === "material_consumption"
    );
    assert.equal(df.label, "Allow Continuous Material Consumption");
    assert.equal(df.fieldtype, "Check");
    assert.equal(df.default, "0");
    const before = JSON.stringify(
      e.context.frappe.tour["Manufacturing Settings"]
    );
    const instance = await tour(e, "Manufacturing Settings");
    const step = instance.driver_steps.find((step) =>
      step.element.includes("'material_consumption'")
    );
    assert.equal(
      step.popover.title,
      "Consenti il consumo continuo di materiale"
    );
    assert.equal(step.popover.description, manufacturingItalian);
    assert.equal(
      JSON.stringify(e.context.frappe.tour["Manufacturing Settings"]),
      before
    );
    assert.equal(
      e.context.frappe.tour["Manufacturing Settings"][0].fieldname,
      "material_consumption"
    );
  } finally {
    e.close();
  }
});

test("both standard descriptions preserve complete English fallback", async () => {
  const e = environment({ dictionary: {} });
  try {
    for (const [doctype, fieldname, message] of [
      ["Employee", "date_of_birth", employeeSource],
      ["Manufacturing Settings", "material_consumption", manufacturingSource],
    ]) {
      const instance = await tour(e, doctype);
      const step = instance.driver_steps.find((step) =>
        step.element.includes("'" + fieldname + "'")
      );
      assert.equal(step.popover.description, message);
    }
  } finally {
    e.close();
  }
});

test("authored Form Tour records keep precedence, raw content, fields and callbacks", async () => {
  const authored = {
    steps: [
      {
        fieldname: "memory_field",
        title: "Memory title",
        description: "<b>Authored customer & supplier guidance</b>",
      },
    ],
  };
  const e = environment({ authored });
  try {
    const before = JSON.stringify(authored);
    const instance = await tour(e, "Employee");
    assert.equal(instance.tour, authored);
    assert.equal(instance.driver_steps.length, 1);
    assert.equal(
      instance.driver_steps[0].element,
      ".frappe-control[data-fieldname='memory_field']"
    );
    assert.equal(
      instance.driver_steps[0].popover.description,
      authored.steps[0].description
    );
    assert.equal(JSON.stringify(authored), before);
    assert.deepEqual(e.reads, [
      ["exists", "Form Tour", "Employee"],
      ["get_doc", "Form Tour", "Employee"],
    ]);
  } finally {
    e.close();
  }
});

test("a type without a configured tour does not initialize a driver", async () => {
  const e = environment();
  try {
    const instance = await tour(e, "Memory Type");
    assert.equal(instance.driver_steps.length, 0);
    assert.equal(e.drivers.length, 0);
    assert.deepEqual(e.reads, [["exists", "Form Tour", "Memory Type"]]);
  } finally {
    e.close();
  }
});

test("configured extraction includes whole new descriptions and no keys from this test", () => {
  assert.equal(
    records[0].method,
    "frappe.gettext.extractors.javascript.extract"
  );
  assert.equal(
    records[1].method,
    "frappe.gettext.extractors.javascript.extract"
  );
  assert.equal(
    records[0].messages.filter((message) => message[1] === employeeSource)
      .length,
    1
  );
  assert.equal(
    records[1].messages.filter((message) => message[1] === manufacturingSource)
      .length,
    1
  );
  assert.equal(
    records[1].messages.filter(
      (message) => message[1] === "Allow Continuous Material Consumption"
    ).length,
    1
  );
  assert.deepEqual(records.at(-1).messages, []);
});
