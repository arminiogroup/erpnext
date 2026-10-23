const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { test } = require("node:test");
const vm = require("node:vm");

const root =
  process.env.ERPNEXT_SOURCE_ROOT || path.resolve(__dirname, "../../../..");
const dependencies = createRequire(
  path.join(
    process.env.BANKING_DEPENDENCY_ROOT || path.join(root, "banking"),
    "package.json"
  )
);
const RealDate = Date;
const now = RealDate.parse("2026-10-21T12:00:00Z");
globalThis.Date = class extends RealDate {
  constructor(...args) {
    super(...(args.length ? args : [now]));
  }
  static now() {
    return now;
  }
};
const dayjs = dependencies("dayjs");
const ts = dependencies("typescript");
const localeDirectory = path.join(
  path.dirname(dependencies.resolve("dayjs/package.json")),
  "locale"
);
const localeFiles = fs
  .readdirSync(localeDirectory)
  .filter((file) => file.endsWith(".js"));

function compile(
  relative,
  requireModule,
  globals = {},
  extra = "",
  transform = (source) => source
) {
  const filename = path.join(root, relative);
  const source = transform(fs.readFileSync(filename, "utf8")) + extra;
  const result = ts.transpileModule(source, {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2020,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  });
  assert.deepEqual(result.diagnostics, []);
  const exports = {};
  vm.runInNewContext(
    result.outputText,
    { exports, require: requireModule, ...globals },
    { filename }
  );
  return exports;
}

function makeDateModule(language = "it", overrideLoaders) {
  const window = {
    frappe: {
      boot: {
        lang: language,
        user: { defaults: { date_format: "dd/mm/yyyy" } },
        sysdefaults: { date_format: "yyyy-mm-dd" },
        time_zone: { system: "UTC", user: "Europe/Rome" },
      },
      _messages: {},
    },
  };
  const loaded = [];
  const loaders = Object.fromEntries(
    localeFiles.map((file) => [
      `/node_modules/dayjs/locale/${file}`,
      async () => {
        loaded.push(file.slice(0, -3));
        return dependencies(`dayjs/locale/${file}`);
      },
    ])
  );
  const translate = compile("banking/src/lib/translate.ts", dependencies, {
    window,
  }).default;
  const api = compile(
    "banking/src/lib/date.ts",
    (name) =>
      name === "@/lib/translate"
        ? { __esModule: true, default: translate }
        : dependencies(name),
    {
      window,
      __localeLoaders: overrideLoaders || loaders,
    },
    "\nexport { toUserTimezone };\n",
    (source) =>
      source.replace(
        /import\.meta\.glob(?:<[^>]+>)?\([\s\S]*?\);/,
        "__localeLoaders;"
      )
  );
  return {
    api,
    window,
    loaded,
    initialize: () => api.initializeDateLocale?.(),
  };
}

test("Italian display uses the boot language without changing explicit formats", async () => {
  const { api, initialize, loaded } = makeDateModule();
  await initialize();
  assert.equal(
    api.formatDate("2026-10-20", "Do MMMM YYYY"),
    "20º ottobre 2026"
  );
  assert.equal(api.getTimeago("2026-10-20 12:00:00"), "un giorno fa");
  assert.equal(
    api.formatDate("2026-10-20", "[Document] YYYY-MM-DD [literal]"),
    "Document 2026-10-20 literal"
  );
  assert.deepEqual(loaded, ["it"]);
  assert.equal(dayjs.locale(), "en");
});

test("supported locale aliases and regional fallback select only one module", async () => {
  for (const [language, expected] of [
    ["it-IT", "it"],
    ["it_IT", "it"],
    ["pt-BR", "pt-br"],
    ["en-GB", "en-gb"],
    ["en-US", "en"],
    ["fil", "tl-ph"],
    ["no", "nb"],
    ["sr", "sr-cyrl"],
    ["sr-CS", "sr"],
    ["uz", "uz-latn"],
    ["zh-TW", "zh-tw"],
  ]) {
    const { api, initialize, loaded } = makeDateModule(language);
    await initialize();
    assert.deepEqual(loaded, [expected], language);
    assert.equal(
      api.formatDate("2026-10-20", "MMMM"),
      dayjs("2026-10-20").locale(expected).format("MMMM"),
      language
    );
    assert.equal(dayjs.locale(), "en");
  }
});

test("unknown and unavailable locales retain the existing fallback", async () => {
  for (const language of [undefined, "ps", "unknown-XX"]) {
    const { api, window, initialize, loaded } = makeDateModule(language);
    if (language === undefined) delete window.frappe.boot.lang;
    await initialize();
    assert.equal(api.formatDate("2026-10-20", "MMMM"), "October");
    assert.deepEqual(loaded, []);
  }
  const { api, initialize } = makeDateModule("it", {
    "/node_modules/dayjs/locale/it.js": () =>
      Promise.reject(new Error("locale chunk unavailable")),
  });
  await initialize();
  assert.equal(api.formatDate("2026-10-20", "MMMM"), "October");
  assert.equal(dayjs.locale(), "en");
});

test("numeric formats, canonical values, parsing and financial periods stay unchanged", async () => {
  const { api, window, initialize } = makeDateModule();
  const formats = [
    "yyyy-mm-dd",
    "dd-mm-yyyy",
    "dd/mm/yyyy",
    "dd.mm.yyyy",
    "mm/dd/yyyy",
    "mm-dd-yyyy",
  ];
  const beforeFormats = formats.map((format) =>
    api.formatDate("2026-10-20", format.toUpperCase())
  );
  const bases = ["2026-10-21", "2026-01-01", "2024-02-29"];
  const bounds = () =>
    bases.flatMap((base) =>
      [...api.AVAILABLE_TIME_PERIODS, "Date Range", "Unknown"].map((period) =>
        api.getDatesForTimePeriod(period, undefined, base)
      )
    );
  const beforeBounds = JSON.stringify(bounds());
  const parse = () =>
    [
      ["20/10/2026", "DD/MM/YYYY"],
      ["20 October 2026", "DD MMMM YYYY"],
      ["20 ottobre 2026", "DD MMMM YYYY"],
    ].map(([value, format]) => api.toDate(value, format).valueOf());
  const beforeParse = parse();
  const today = api.today();
  const beforeTimezone = [
    "2026-03-29 00:30:00",
    "2026-10-25 00:30:00",
    "2026-01-01 23:45:00",
  ].map((value) => {
    const date = api.toUserTimezone(value);
    return [
      date.valueOf(),
      date.utcOffset(),
      date.format("YYYY-MM-DD HH:mm:ss Z"),
    ];
  });
  await initialize();
  assert.equal(JSON.stringify(bounds()), beforeBounds);
  assert.deepEqual(parse(), beforeParse);
  assert.equal(api.today(), today);
  formats.forEach((format, index) => {
    window.frappe.boot.user.defaults.date_format = format;
    assert.equal(api.formatDate("2026-10-20"), beforeFormats[index]);
  });
  assert.equal(api.formatDate("2026-10-20", "YYYY-MM-DD"), "2026-10-20");
  ["2026-03-29 00:30:00", "2026-10-25 00:30:00", "2026-01-01 23:45:00"].forEach(
    (value, index) => {
      const date = api.toUserTimezone(value);
      assert.deepEqual(
        [
          date.valueOf(),
          date.utcOffset(),
          date.format("YYYY-MM-DD HH:mm:ss Z"),
        ],
        beforeTimezone[index]
      );
    }
  );
  assert.equal(api.formatDate(undefined), "");
  assert.equal(api.getTimeago(undefined), "");
  assert.equal(dayjs.locale(), "en");
});

test("both actual bootstrap branches wait for the selected locale before first render", async () => {
  for (const [development, failLoad] of [
    [false, false],
    [true, false],
    [false, true],
    [true, true],
  ]) {
    let release;
    const events = [];
    const { api, window } = makeDateModule("it", {
      "/node_modules/dayjs/locale/it.js": () => {
        events.push("load");
        return new Promise((resolve, reject) => {
          release = () =>
            failLoad
              ? reject(new Error("locale chunk unavailable"))
              : resolve(dependencies("dayjs/locale/it"));
        });
      },
    });
    const boot = window.frappe.boot;
    boot.docs = [];
    boot.layout_direction = "ltr";
    boot.__messages = {};
    window.frappe.model = {
      sync: (docs) => {
        assert.deepEqual(JSON.parse(JSON.stringify(docs)), []);
        events.push("sync");
      },
    };
    if (development) delete window.frappe.boot;
    const document = {
      dir: "",
      getElementById: (id) => {
        assert.equal(id, "root");
        return {};
      },
    };
    compile(
      "banking/src/main.tsx",
      (name) => {
        if (name === "react-dom/client")
          return {
            createRoot: () => ({
              render: () => {
                events.push(`render:${api.formatDate("2026-10-20", "MMMM")}`);
              },
            }),
          };
        if (name.endsWith("/calendar-locale"))
          return { initializeCalendarLocale: async () => {} };
        if (name.endsWith("/date") || name.endsWith("/date.ts")) return api;
        if (name === "react" || name === "react/jsx-runtime")
          return dependencies(name);
        return {};
      },
      {
        window,
        frappe: window.frappe,
        document,
        fetch: async () => ({
          json: async () => ({
            message: { boot: JSON.stringify(boot), layout_direction: "ltr" },
          }),
        }),
      },
      "",
      (source) => source.replaceAll("import.meta.env.DEV", String(development))
    );
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(events, ["sync", "load"]);
    release();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(events, [
      "sync",
      "load",
      failLoad ? "render:October" : "render:ottobre",
    ]);
    if (development) assert.equal(document.dir, "ltr");
    assert.equal(dayjs.locale(), "en");
  }
});

test("display locale changes preserve ASCII canonical dates and period bounds across locales", async () => {
  const baseline = makeDateModule("en").api;
  const periods = [...baseline.AVAILABLE_TIME_PERIODS, "Date Range", "Unknown"];
  const expected = JSON.stringify(
    periods.map((period) =>
      baseline.getDatesForTimePeriod(period, undefined, "2026-10-21")
    )
  );
  for (const language of ["it", "ar", "fr", "pt-BR", "en-GB"]) {
    const { api, initialize } = makeDateModule(language);
    await initialize();
    assert.equal(
      api.formatDate("2026-10-20", "YYYY-MM-DD"),
      "2026-10-20",
      language
    );
    assert.equal(
      JSON.stringify(
        periods.map((period) =>
          api.getDatesForTimePeriod(period, undefined, "2026-10-21")
        )
      ),
      expected,
      language
    );
    assert.equal(dayjs.locale(), "en");
  }
});
