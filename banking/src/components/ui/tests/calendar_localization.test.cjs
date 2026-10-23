const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { test } = require("node:test");
const vm = require("node:vm");
const root =
  process.env.ERPNEXT_SOURCE_ROOT || path.resolve(__dirname, "../../../../..");
const dependencies = createRequire(
  path.join(
    process.env.BANKING_DEPENDENCY_ROOT || path.join(root, "banking"),
    "package.json"
  )
);
const React = dependencies("react");
const { renderToStaticMarkup } = dependencies("react-dom/server");
const dayPicker = dependencies("react-day-picker");
const dateFns = dependencies("date-fns");
const dayjs = dependencies("dayjs");
const ts = dependencies("typescript");
const packageRoot = path.dirname(
  dependencies.resolve("react-day-picker/package.json")
);
const localeFiles = fs
  .readdirSync(packageRoot + "/dist/cjs/locale")
  .filter((file) => file.endsWith(".js") && !file.endsWith("-jalali.js"));
const packageLocale = (code) =>
  dependencies(packageRoot + "/dist/cjs/locale/" + code + ".js");
function compile(
  relative,
  resolver,
  globals,
  extra = "",
  transform = (source) => source
) {
  const filename = path.join(root, relative);
  const source =
    transform(fs.readFileSync(filename, "utf8")).replace(
      /import\.meta\.glob(?:<[^>]+>)?\([\s\S]*?\);/,
      "__localeLoaders;"
    ) + extra;
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
    { exports, require: resolver, ...globals },
    { filename }
  );
  return exports;
}
function environment(language = "it", overrideLoaders) {
  const window = {
    frappe: {
      boot: {
        lang: language,
        user: { defaults: { date_format: "dd/mm/yyyy" } },
        sysdefaults: { date_format: "yyyy-mm-dd" },
        docs: [],
        layout_direction: "ltr",
        __messages: {},
      },
      _messages: {},
    },
  };
  const loaded = [];
  const loaders = Object.fromEntries(
    localeFiles.map((file) => [
      `/node_modules/react-day-picker/dist/esm/locale/${file}`,
      async () => {
        loaded.push(file.slice(0, -3));
        return packageLocale(file.slice(0, -3));
      },
    ])
  );
  const globals = { window, __localeLoaders: overrideLoaders || loaders };
  const calendarLocale = compile(
    "banking/src/lib/calendar-locale.ts",
    dependencies,
    globals
  );
  const translate = compile(
    "banking/src/lib/translate.ts",
    dependencies,
    globals
  ).default;
  const dates = compile(
    "banking/src/lib/date.ts",
    (name) =>
      name === "@/lib/translate"
        ? { __esModule: true, default: translate }
        : dependencies(name),
    {
      window,
      __localeLoaders: {
        "/node_modules/dayjs/locale/it.js": async () =>
          dependencies("dayjs/locale/it"),
      },
    }
  );
  const buttons = [];
  const calendars = [];
  const Button = React.forwardRef(
    ({ variant, isIconButton, children, ...props }, ref) => {
      buttons.push(props);
      return React.createElement("button", { ...props, ref }, children);
    }
  );
  const buttonModule = { Button, buttonVariants: () => "" };
  const utils = compile("banking/src/lib/utils.ts", dependencies, globals);
  const calendar = compile(
    "banking/src/components/ui/calendar.tsx",
    (name) => {
      if (name === "react-day-picker")
        return {
          ...dayPicker,
          DayPicker: (props) => {
            calendars.push(props);
            return React.createElement(dayPicker.DayPicker, props);
          },
        };
      if (name === "@/lib/calendar-locale") return calendarLocale;
      if (name === "@/lib/utils") return utils;
      if (name === "@/components/ui/button") return buttonModule;
      return dependencies(name);
    },
    globals
  );
  const render = (props) => {
    buttons.length = 0;
    calendars.length = 0;
    return renderToStaticMarkup(
      React.createElement(calendar.Calendar, {
        month: new Date(2026, 9, 1),
        today: new Date(2026, 9, 20),
        selected: new Date(2026, 9, 20),
        mode: "single",
        captionLayout: "dropdown",
        startMonth: new Date(2026, 0, 1),
        endMonth: new Date(2026, 11, 1),
        ...props,
      })
    );
  };
  return {
    window,
    globals,
    calendarLocale,
    dates,
    translate,
    calendar,
    buttonModule,
    buttons,
    calendars,
    render,
    loaded,
  };
}

test("actual Calendar uses Italian package dates and accessibility labels", async () => {
  const env = environment();
  await env.calendarLocale.initializeCalendarLocale();
  const html = env.render({ showWeekNumber: true });
  for (const text of [
    "ottobre",
    "lunedì",
    "Vai al mese precedente",
    "Vai al mese successivo",
    "Scegli il mese",
    "Scegli l’anno",
    "Barra di navigazione",
    "Numero settimana",
    "Oggi, martedì 20 ottobre 2026, selezionato",
  ])
    assert.ok(html.includes(text), text);
  assert.ok(!html.includes("Go to the") && !html.includes(">Oct<"));
  assert.ok(html.includes('aria-label="lunedì"'));
});

test("explicit caller locale, labels, formatters and options remain authoritative", async () => {
  const env = environment();
  await env.calendarLocale.initializeCalendarLocale();
  const locale = packageLocale("fr").fr;
  const labels = {
    labelNext: () => "Caller next",
    labelDayButton: () => "Caller day",
  };
  const formatters = { formatMonthDropdown: () => "Caller month" };
  const selected = new Date(2026, 9, 20);
  const onSelect = () => {};
  const html = env.render({
    locale,
    labels,
    formatters,
    selected,
    onSelect,
    weekStartsOn: 4,
  });
  const props = env.calendars.at(-1);
  assert.strictEqual(props.locale, locale);
  assert.strictEqual(props.labels, labels);
  assert.strictEqual(props.formatters, formatters);
  assert.strictEqual(props.selected, selected);
  assert.strictEqual(props.onSelect, onSelect);
  assert.equal(props.weekStartsOn, 4);
  for (const text of ["Caller next", "Caller day", "Caller month", "jeudi"])
    assert.ok(html.includes(text), text);
  const partial = { code: "fr", localize: locale.localize };
  env.render({ locale: partial });
  assert.strictEqual(env.calendars.at(-1).locale, partial);
});

test("actual single and range day clicks preserve canonical Date identities", async () => {
  const env = environment();
  await env.calendarLocale.initializeCalendarLocale();
  for (const mode of ["single", "range"]) {
    const from = new Date(2026, 9, 20);
    const to = new Date(2026, 9, 21);
    const selections = [];
    const clicks = [];
    env.render({
      mode,
      selected: mode === "single" ? from : { from, to },
      onSelect: (...args) => selections.push(args),
      onDayClick: (...args) => clicks.push(args),
    });
    const target = new Date(2026, 9, 22);
    const button = env.buttons.find(
      (props) => props["data-day"] === target.toLocaleDateString()
    );
    assert.ok(button);
    const event = { preventDefault() {}, stopPropagation() {} };
    button.onClick(event);
    assert.equal(selections.length, 1);
    assert.strictEqual(selections[0][1], clicks[0][0]);
    assert.strictEqual(selections[0][3], event);
    if (mode === "single") assert.strictEqual(selections[0][0], clicks[0][0]);
    else {
      assert.strictEqual(selections[0][0].from, from);
      assert.strictEqual(selections[0][0].to, clicks[0][0]);
    }
    assert.equal(dayjs(clicks[0][0]).format("YYYY-MM-DD"), "2026-10-22");
  }
});

test("verified locale aliases and unknown/import-failure fallback never mutate defaults", async () => {
  const defaults = JSON.stringify(dateFns.getDefaultOptions());
  for (const [language, filename] of [
    ["it_IT", "it"],
    ["it-CH", "it-CH"],
    ["en", "en-US"],
    ["am", "am-ET"],
    ["fa", "fa-IR"],
    ["no", "nb"],
    ["sr", "sr"],
    ["sr-CS", "sr-Latn"],
    ["uz", "uz"],
    ["zh", "zh-CN"],
    ["zh-Hans", "zh-CN"],
  ]) {
    const env = environment(language);
    await env.calendarLocale.initializeCalendarLocale();
    assert.deepEqual(env.loaded, [filename], language);
    assert.strictEqual(
      env.calendarLocale.getCalendarLocale(),
      Object.values(packageLocale(filename))[0]
    );
    assert.equal(JSON.stringify(dateFns.getDefaultOptions()), defaults);
    assert.equal(dayjs.locale(), "en");
  }
  for (const language of ["ps", "unknown-XX", ""]) {
    const env = environment(language);
    await env.calendarLocale.initializeCalendarLocale();
    assert.equal(env.calendarLocale.getCalendarLocale(), undefined);
    assert.deepEqual(env.loaded, []);
    assert.ok(env.render().includes("Go to the Next Month"));
  }
  const env = environment("it", {
    "/node_modules/react-day-picker/dist/esm/locale/it.js": () =>
      Promise.reject(new Error("missing locale chunk")),
  });
  await env.calendarLocale.initializeCalendarLocale();
  assert.equal(env.calendarLocale.getCalendarLocale(), undefined);
  assert.ok(env.render().includes("Go to the Next Month"));
  assert.equal(JSON.stringify(dateFns.getDefaultOptions()), defaults);
});

function presentation() {
  return new Proxy(
    { __esModule: true },
    {
      get(target, name) {
        if (name === "__esModule") return true;
        return ({ children }) => React.createElement("div", null, children);
      },
    }
  );
}

test("real DateField and bank date-filter producers keep ISO callbacks", async () => {
  const env = environment();
  await Promise.all([
    env.calendarLocale.initializeCalendarLocale(),
    env.dates.initializeDateLocale(),
  ]);
  const values = [];
  const field = {
    name: "date",
    value: "2026-10-20",
    onChange: (value) => values.push(value),
    onBlur() {},
  };
  const ui = presentation();
  const resolver = (name) => {
    if (
      name === "react" ||
      name === "react/jsx-runtime" ||
      name === "dayjs" ||
      name === "chrono-node"
    )
      return dependencies(name);
    if (name === "@/lib/date") return env.dates;
    if (name === "@/lib/translate")
      return { __esModule: true, default: env.translate };
    if (name === "./calendar" || name === "@/components/ui/calendar")
      return env.calendar;
    if (name === "./button" || name === "@/components/ui/button")
      return env.buttonModule;
    if (name === "react-hook-form")
      return { useFormContext: () => ({ control: {} }) };
    if (name === "@/components/ui/form")
      return {
        ...ui,
        FormField: (props) => props.render({ field }),
        FormControl: ui.FormControl,
        FormItem: ui.FormItem,
        FormLabel: ui.FormLabel,
        FormMessage: ui.FormMessage,
      };
    if (name === "jotai")
      return {
        useAtom: () => [
          { fromDate: "2026-10-20", toDate: "2026-10-21" },
          (value) => values.push(value),
        ],
      };
    if (name === "@/hooks/useFiscalYear")
      return { __esModule: true, default: () => ({ fiscalYear: null }) };
    if (name === "@/hooks/useResetScrollOnSearch")
      return { __esModule: true, default: () => ({ current: null }) };
    if (name === "@/components/ui/direction")
      return { useDirection: () => "ltr" };
    return ui;
  };
  const fields = compile(
    "banking/src/components/ui/form-elements.tsx",
    resolver,
    env.globals
  );
  renderToStaticMarkup(
    React.createElement(fields.DateField, {
      name: "date",
      label: "Example date",
    })
  );
  const single = env.calendars.at(-1);
  assert.equal(single.mode, "single");
  single.onSelect(new Date(2026, 9, 22));
  assert.deepEqual(values, ["2026-10-22"]);
  const filter = compile(
    "banking/src/components/features/BankReconciliation/BankRecDateFilter.tsx",
    resolver,
    env.globals
  ).default;
  renderToStaticMarkup(React.createElement(filter));
  const range = env.calendars.at(-1);
  assert.equal(range.mode, "range");
  range.onSelect({ from: new Date(2026, 9, 20), to: new Date(2026, 9, 22) });
  assert.deepEqual(JSON.parse(JSON.stringify(values)), [
    "2026-10-22",
    { fromDate: "2026-10-20", toDate: "2026-10-22" },
  ]);
});

test("actual development and production boot wait for both selected locale promises", async () => {
  for (const development of [false, true]) {
    for (const failCalendar of [false, true]) {
      let releaseCalendar;
      const env = environment("it", {
        "/node_modules/react-day-picker/dist/esm/locale/it.js": () =>
          new Promise((resolve, reject) => {
            releaseCalendar = () =>
              failCalendar
                ? reject(new Error("missing calendar locale"))
                : resolve(packageLocale("it"));
          }),
      });
      const events = [];
      const boot = env.window.frappe.boot;
      env.window.frappe.model = { sync: () => events.push("sync") };
      if (development) delete env.window.frappe.boot;
      let releaseDate;
      const dates = {
        initializeDateLocale: () =>
          new Promise((resolve) => {
            releaseDate = resolve;
          }),
      };
      compile(
        "banking/src/main.tsx",
        (name) => {
          if (name.endsWith("/calendar-locale")) return env.calendarLocale;
          if (name.endsWith("/date")) return dates;
          if (name === "react-dom/client")
            return {
              createRoot: () => ({
                render: () =>
                  events.push(
                    env.calendarLocale.getCalendarLocale()?.code || "fallback"
                  ),
              }),
            };
          if (name === "react" || name === "react/jsx-runtime")
            return dependencies(name);
          return {};
        },
        {
          window: env.window,
          frappe: env.window.frappe,
          document: { dir: "", getElementById: () => ({}) },
          fetch: async () => ({
            json: async () => ({
              message: { boot: JSON.stringify(boot), layout_direction: "ltr" },
            }),
          }),
        },
        "",
        (source) =>
          source.replaceAll("import.meta.env.DEV", String(development))
      );
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(typeof releaseDate, "function");
      assert.equal(typeof releaseCalendar, "function");
      releaseDate();
      await new Promise((resolve) => setImmediate(resolve));
      assert.deepEqual(events, ["sync"]);
      releaseCalendar();
      await new Promise((resolve) => setImmediate(resolve));
      assert.deepEqual(events, ["sync", failCalendar ? "fallback" : "it"]);
    }
  }
});
