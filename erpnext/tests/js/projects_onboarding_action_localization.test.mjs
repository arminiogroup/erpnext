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
const plain = (value) => JSON.parse(JSON.stringify(value));
const stepPath =
  "erpnext/setup/onboarding_step/complete_projects_onboarding/complete_projects_onboarding.json";
const translations = {
  "Open Projects": "Progetti aperti",
  "Open Projects Module": "Apri il modulo Progetti",
  Skip: "Salta",
};

const python = String.raw`
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS,get_method_map
from frappe.model.meta import Meta
from frappe.desk.doctype.onboarding_step.onboarding_step import OnboardingStep,get_step_details
root=Path(sys.argv[1]);args=json.load(sys.stdin)
frappe.init(site="",sites_path=str(root.parents[1]/"sites"))
frappe.local.session=frappe._dict(user="translator@example.com",data=frappe._dict())
frappe.local.lang="it"
schema=json.loads((Path(frappe.get_app_path("frappe"))/"desk/doctype/onboarding_step/onboarding_step.json").read_text())
meta=Meta.__new__(Meta)
meta.__dict__.update(schema,istable=0)
meta.fields=[frappe._dict(field,parent=schema["name"])for field in schema["fields"]]
meta.init_field_caches()
reads=[]
def exists(dt,name):
 assert dt=="Module Onboarding" and name=="Projects Onboarding"
 reads.append(["exists",dt,name])
 return False

def value(dt,name,field):
 assert (dt,name,field)==("Module Onboarding","Projects Onboarding","module")
 reads.append(["value",dt,name,field])
 return "Projects"

def get_meta(dt,*a,**kw):
 assert dt=="Onboarding Step"
 return meta

def cache(key,lang,**kwargs):
 assert key=="merged_translations"
 return args["dictionary"] if lang=="it" else {}

results=[]
try:
 with patch.object(frappe,"get_meta",side_effect=get_meta),patch.object(frappe.cache,"hget",side_effect=cache),patch.object(frappe.local,"db",SimpleNamespace(table_exists=lambda dt:False,exists=exists,get_value=value),create=True):
  source=json.loads((root/args["path"]).read_text())
  for language,dictionary in [("en",{}),("it",args["dictionary"]),("it",{})]:
   frappe.local.lang=language
   args["dictionary"]=dictionary
   doc=OnboardingStep(deepcopy(source))
   before=doc.as_dict()
   def get_doc(dt,name):
    assert dt=="Onboarding Step" and name==source["name"]
    reads.append(["get_doc",dt,name])
    return doc
   with patch.object(frappe,"get_doc",side_effect=get_doc):
    details=get_step_details(source["name"])
   assert doc.as_dict()==before
   assert details.action==source["action"]=="Complete Onboarding"
   assert details.module_onboarding==source["module_onboarding"]=="Projects Onboarding"
   assert details.module=="Projects" and details.is_complete==0 and details.is_skipped==0
   results.append({"language":language,"dictionary":dictionary,"details":details,"reads":deepcopy(reads[-3:])})
 methods=get_method_map("erpnext")+get_method_map("frappe")
 extraction=[]
 for relative in [args["path"],"erpnext/projects/workspace/projects/projects.json","erpnext/setup/doctype/email_digest/email_digest.json",args["test_path"]]:
  selected=next((method for pattern,method in methods if pathmatch(pattern,relative)),None)
  messages=list(extract_from_file(selected,str(root/relative),keywords=PYTHON_KEYWORDS)) if selected and selected!="ignore" else []
  extraction.append({"path":relative,"method":selected,"messages":messages})
 print(json.dumps({"source":source,"steps":results,"extraction":extraction}))
finally:
 frappe.destroy()
`;
const response = spawnSync(
  resolve(root, "../../env/bin/python"),
  ["-c", python, root],
  {
    input: JSON.stringify({
      path: stepPath,
      test_path: fileURLToPath(import.meta.url).slice(root.length),
      dictionary: translations,
    }),
    encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  }
);
assert.equal(response.status, 0, response.stderr);
const data = JSON.parse(response.stdout);

function environment(dictionary = {}) {
  const dom = new JSDOM("<!doctype html><body></body>", {
    url: "http://localhost/",
    runScripts: "outside-only",
  });
  const window = dom.window;
  const context = dom.getInternalVMContext();
  vm.runInContext(readFileSync(require.resolve("jquery"), "utf8"), context);
  const $ = window.jQuery;
  const calls = [];
  window.frappe = {
    provide(path) {
      let target = window;
      for (const part of path.split(".")) target = target[part] ??= {};
    },
    _messages: { ...dictionary },
    ui: {},
    utils: { icon: () => "<svg></svg>" },
    boot: { module_sidebars: { Projects: { module: "Projects" } } },
    session: { user: "translator@example.com" },
    app: { sidebar: { open_module: (module) => calls.push(module) } },
    xcall() {
      throw new Error("Unexpected RPC or persisted onboarding state");
    },
    call() {
      throw new Error("Unexpected network request");
    },
  };
  window.showdown = require("showdown");
  for (const path of [
    "utils/utils.js",
    "dom.js",
    "format.js",
    "translate.js",
    "utils/tools.js",
  ]) {
    vm.runInContext(
      readFileSync(
        resolve(framework, "frappe/public/js/frappe/" + path),
        "utf8"
      ).replace(/^import .*;\n/gm, ""),
      context,
      { filename: path }
    );
  }
  for (const [path, name] of [
    ["base_widget.js", "Widget"],
    ["onboarding_widget.js", "OnboardingWidget"],
    ["number_card_widget.js", "NumberCardWidget"],
  ]) {
    const source = readFileSync(
      resolve(framework, "frappe/public/js/frappe/widgets/" + path),
      "utf8"
    );
    vm.runInContext(
      source
        .replace(/^import .*;\n/gm, "")
        .replace(
          `export default class ${name}`,
          `window.${name} = class ${name}`
        ),
      context,
      { filename: path }
    );
  }
  const buttons = buildSync({
    entryPoints: [
      resolve(framework, "frappe/public/js/frappe/ui/components/button.js"),
    ],
    bundle: true,
    write: false,
    format: "iife",
    platform: "browser",
  }).outputFiles[0].text;
  vm.runInContext(buttons, context);
  return { window, $, calls, close: () => window.close() };
}

for (const [index, caption] of [
  [0, "Open Projects Module"],
  [1, "Apri il modulo Progetti"],
  [2, "Open Projects Module"],
]) {
  test(`actual server and button display an imperative module action (${index})`, () => {
    const row = data.steps[index];
    const env = environment(row.dictionary);
    try {
      assert.equal(row.details.action_label, caption);
      const before = plain(row.details);
      const widget = Object.create(env.window.OnboardingWidget.prototype);
      const body = widget.make_step_content(row.details);
      env.$("body").append(body);
      const button = body.find("button").first();
      assert.equal(button.find(".es-button__label").text(), caption);
      button.trigger("click");
      assert.deepEqual(env.calls, ["Projects"]);
      assert.deepEqual(plain(row.details), before);
      assert.equal(row.details.is_complete, 0);
    } finally {
      env.close();
    }
  });
}

test("configured extraction registers the action separately and retains the open-status noun", () => {
  assert.equal(data.source.action_label, "Open Projects Module");
  assert.equal(
    data.extraction[0].method,
    "frappe.gettext.extractors.onboarding_step.extract"
  );
  assert.ok(
    data.extraction[0].messages.some((row) => row[1] === "Open Projects Module")
  );
  for (const row of data.extraction.slice(1, 3))
    assert.ok(row.messages.some((entry) => entry[1] === "Open Projects"));
  assert.equal(data.extraction[3].method, null);
  assert.deepEqual(data.extraction[3].messages, []);
});

test("actual number-card title and canonical open-status filter remain nouns", () => {
  const env = environment(translations);
  try {
    const cardDocument = JSON.parse(
      readFileSync(
        resolve(
          root,
          "erpnext/projects/number_card/open_projects/open_projects.json"
        ),
        "utf8"
      )
    );
    const card = Object.create(env.window.NumberCardWidget.prototype);
    card.title_field = env.$("<div>");
    card.label = cardDocument.label;
    card.set_title();
    assert.equal(card.title_field.text(), "Progetti aperti");
    assert.equal(cardDocument.name, "Open Projects");
    assert.equal(cardDocument.document_type, "Project");
    assert.equal(cardDocument.function, "Count");
    assert.deepEqual(JSON.parse(cardDocument.filters_json), [
      ["Project", "status", "=", "Open"],
    ]);
  } finally {
    env.close();
  }
});

test("untranslated custom action captions and module dispatch are preserved", () => {
  const env = environment(translations);
  try {
    const widget = Object.create(env.window.OnboardingWidget.prototype);
    const step = {
      ...data.steps[0].details,
      action_label: "Apri il reparto <personalizzato>",
    };
    const body = widget.make_step_content(step);
    const button = body.find("button").first();
    assert.equal(button.find(".es-button__label").text(), step.action_label);
    button.trigger("click");
    assert.deepEqual(env.calls, ["Projects"]);
    assert.equal(step.action, "Complete Onboarding");
    assert.equal(step.name, "Complete Projects Onboarding");
    assert.equal(step.module_onboarding, "Projects Onboarding");
  } finally {
    env.close();
  }
});
