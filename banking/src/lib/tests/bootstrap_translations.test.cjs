const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const vm = require("node:vm");
const { test } = require("node:test");

const root =
  process.env.ERPNEXT_SOURCE_ROOT || path.resolve(__dirname, "../../../..");
const dependencies = createRequire(
  path.join(
    process.env.BANKING_DEPENDENCY_ROOT || path.join(root, "banking"),
    "package.json"
  )
);
const ts = dependencies("typescript");
const italian = {
  Banking: "Servizi bancari",
  Settings: "Impostazioni",
  "No bank accounts found": "Nessun conto bancario trovato",
  "You have not added any bank accounts to your company.":
    "Non hai aggiunto conti bancari alla tua azienda.",
  "Configure Bank Accounts": "Configura conti bancari",
};
const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function environment(development, delayLocales = false) {
  const dictionary = deferred(),
    dates = deferred(),
    calendar = deferred();
  if (!delayLocales) {
    dates.resolve();
    calendar.resolve();
  }
  const events = [],
    requests = [],
    renders = [],
    synced = [];
  const boot = {
    lang: "it",
    translations_version: "version-fixture",
    layout_direction: "rtl",
    docs: [{ doctype: "Company", name: "Impresa originale" }],
    user: { defaults: { date_format: "dd/mm/yyyy" } },
    sysdefaults: { date_format: "yyyy-mm-dd" },
  };
  const sandbox = {
    console,
    document: { dir: "", getElementById: () => ({}) },
    frappe: {
      model: {
        sync: (docs) => {
          synced.push(docs);
          events.push("sync");
        },
      },
    },
    fetch(url, options) {
      requests.push({ url, options });
      if (url.includes("get_context_for_dev"))
        return Promise.resolve({
          json: async () => ({
            message: { boot: JSON.stringify(boot), layout_direction: "rtl" },
          }),
        });
      assert.match(url, /get_boot_translations\?v=version-fixture&lang=it$/);
      return dictionary.promise;
    },
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  const jsx = {
    jsx: (type, props) => ({ type, props }),
    jsxs: (type, props) => ({ type, props }),
    Fragment: "fragment",
  };
  const generic = new Proxy(
    { __esModule: true, default: () => null },
    { get: (target, key) => (key in target ? target[key] : () => null) }
  );
  function compile(relative, resolver, extra = "", transform = (s) => s) {
    const filename = path.resolve(root, relative);
    const output = ts.transpileModule(
      transform(fs.readFileSync(filename, "utf8")) + extra,
      {
        fileName: filename,
        reportDiagnostics: true,
        compilerOptions: {
          target: ts.ScriptTarget.ES2020,
          module: ts.ModuleKind.CommonJS,
          jsx: ts.JsxEmit.ReactJSX,
          esModuleInterop: true,
        },
      }
    );
    assert.deepEqual(output.diagnostics, []);
    const module = { exports: {} };
    const wrapper = vm.runInContext(
      `(function(require, module, exports){${output.outputText}\n})`,
      context,
      { filename }
    );
    wrapper(resolver, module, module.exports);
    return module.exports;
  }
  const translate = compile(
    "banking/src/lib/translate.ts",
    dependencies
  ).default;
  const dateApi = compile(
    "banking/src/lib/date.ts",
    (name) =>
      name === "@/lib/translate"
        ? { __esModule: true, default: translate }
        : dependencies(name),
    "",
    (s) => s.replace(/import\.meta\.glob(?:<[^>]+>)?\([\s\S]*?\);/, "{};")
  );
  let atoms;
  function atom(value) {
    return { value };
  }
  let app;
  function appModule() {
    if (app) return app;
    events.push({
      appEvaluation: true,
      bootReady: !!sandbox.frappe.boot,
      dictionaryReady: sandbox.frappe._messages?.Banking === italian.Banking,
    });
    atoms = compile(
      "banking/src/components/features/BankReconciliation/bankRecAtoms.ts",
      (name) => {
        if (name === "@/lib/date") return dateApi;
        if (name === "jotai") return { atom };
        if (name === "jotai/utils")
          return {
            atomWithStorage: (key, value) => ({ key, value }),
            createJSONStorage: () => ({}),
          };
        if (name === "jotai-family") return { atomFamily: (create) => create };
        return generic;
      }
    );
    app = { __esModule: true, default: () => null };
    return app;
  }
  function producer(relative) {
    return compile(relative, (name) => {
      if (name === "@/lib/translate")
        return { __esModule: true, default: translate };
      if (name === "react/jsx-runtime") return jsx;
      if (name === "react")
        return {
          lazy: () => () => null,
          Suspense: () => null,
          useState: () => [false, () => {}],
          useCallback: (callback) => callback,
        };
      if (name === "jotai")
        return { useAtom: () => [null, () => {}], useAtomValue: () => null };
      if (name.endsWith("/bankRecAtoms") || name === "./bankRecAtoms")
        return atoms;
      if (name === "./utils")
        return {
          useGetBankAccounts: () => ({ banks: [], isLoading: false }),
          useGetUnreconciledTransactions: () => ({}),
        };
      if (name === "@/components/ui/theme-provider")
        return { useTheme: () => ({ themeValue: "Light" }) };
      if (name === "@/hooks/useCurrentCompany")
        return { useCurrentCompany: () => "Impresa originale" };
      if (name === "@/lib/utils")
        return { cn: (...values) => values.join(" ") };
      return generic;
    }).default;
  }
  function texts(element) {
    if (typeof element === "string") return [element];
    if (Array.isArray(element)) return element.flatMap(texts);
    return element?.props
      ? [
          ...(element.props["aria-label"] ? [element.props["aria-label"]] : []),
          ...texts(element.props.children),
        ]
      : [];
  }
  function snapshot() {
    const page = producer("banking/src/pages/BankReconciliation.tsx")();
    const settings = producer(
      "banking/src/components/features/Settings/Settings.tsx"
    )();
    const picker = producer(
      "banking/src/components/features/BankReconciliation/BankPicker.tsx"
    )({});
    return {
      page: texts(page),
      settings: texts(settings),
      picker: texts(picker),
    };
  }
  if (!development) {
    const index = fs.readFileSync(
      path.join(root, "banking/index.html"),
      "utf8"
    );
    const script = index
      .match(/<script>([\s\S]*?)<\/script>/)[1]
      .replace("{{ frappe.session.csrf_token }}", "csrf-fixture")
      .replace("{{ boot }}", JSON.stringify(JSON.stringify(boot)))
      .replaceAll("{{ layout_direction }}", "rtl");
    vm.runInContext(script, context);
  }
  const mainPath = process.env.BANKING_MAIN_SOURCE || "banking/src/main.tsx";
  compile(
    mainPath,
    (name) => {
      if (name === "./App.tsx") return appModule();
      if (name === "react" || name === "react/jsx-runtime")
        return dependencies(name);
      if (name === "react-dom/client")
        return {
          createRoot: () => ({
            render: () => {
              renders.push(snapshot());
              events.push("render");
            },
          }),
        };
      if (name.endsWith("/date"))
        return {
          initializeDateLocale: () => {
            events.push("dates");
            return dates.promise;
          },
        };
      if (name.endsWith("/calendar-locale"))
        return {
          initializeCalendarLocale: () => {
            events.push("calendar");
            return calendar.promise;
          },
        };
      return generic;
    },
    "",
    (source) => source.replaceAll("import.meta.env.DEV", String(development))
  );
  return {
    boot,
    sandbox,
    dictionary,
    dates,
    calendar,
    events,
    requests,
    renders,
    synced,
    snapshot,
    getAtoms: () => atoms,
  };
}

for (const development of [false, true]) {
  test(`${
    development ? "development" : "production"
  } waits for the actual dictionary before App evaluation and first rendered labels`, async () => {
    const env = environment(development);
    await flush();
    assert.deepEqual(env.renders, []);
    assert.equal(
      env.events.some((e) => e.appEvaluation),
      false
    );
    assert.equal(env.requests.length, development ? 2 : 1);
    const dictionaryRequest = env.requests.at(-1);
    assert.equal(dictionaryRequest.options.credentials, "same-origin");
    env.dictionary.resolve({ json: async () => ({ message: italian }) });
    await flush();
    assert.equal(env.renders.length, 1);
    assert.ok(env.renders[0].page.includes(italian.Banking));
    assert.ok(env.renders[0].settings.includes(italian.Settings));
    assert.ok(
      env.renders[0].picker.includes(italian["No bank accounts found"])
    );
    assert.deepEqual(JSON.parse(JSON.stringify(env.synced)), [env.boot.docs]);
    assert.equal(env.sandbox.frappe.boot.lang, "it");
    if (development) assert.equal(env.sandbox.document.dir, "rtl");
    const evaluation = env.events.find((e) => e.appEvaluation);
    assert.equal(evaluation.bootReady, true);
    assert.equal(evaluation.dictionaryReady, true);
    assert.equal(env.getAtoms().bankRecDateAtom.key, "bank-rec-date");
    assert.match(
      env.getAtoms().bankRecDateAtom.value.fromDate,
      /^\d{4}-\d{2}-01$/
    );
    assert.match(
      env.getAtoms().bankRecDateAtom.value.toDate,
      /^\d{4}-\d{2}-\d{2}$/
    );
  });

  test(`${
    development ? "development" : "production"
  } retains both locale gates and nonblocking dictionary failure fallback`, async () => {
    const env = environment(development, true);
    await flush();
    assert.ok(
      env.requests.some((r) => r.url.includes("get_boot_translations"))
    );
    env.dictionary.reject(new Error("dictionary unavailable"));
    env.dates.resolve();
    await flush();
    assert.deepEqual(env.renders, []);
    env.calendar.resolve();
    await flush();
    assert.equal(env.renders.length, 1);
    assert.ok(env.renders[0].page.includes("Banking"));
    assert.ok(env.renders[0].settings.includes("Settings"));
    assert.equal(typeof env.sandbox.frappe._messages, "object");
  });
}
