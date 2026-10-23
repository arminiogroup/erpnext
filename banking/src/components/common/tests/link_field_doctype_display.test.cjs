const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");
const banking = path.resolve(__dirname, "../../../..");
const messages = {
  Company: "Azienda",
  "Payment Entry": "Registrazione di pagamento",
  "Create New {0}": "Crea {0}",
  "Select {0}": "Seleziona {0}",
};

function environment(options = {}) {
  const doctype = options.doctype || "Company";
  const calls = [],
    changes = [],
    states = [];
  const scope = {
    window: {
      frappe: {
        _messages: options.dictionary ?? messages,
        boot: { user: { can_create: options.denied ? [] : [doctype] } },
      },
    },
    locals: { DocType: { [doctype]: { name: doctype, ...options.meta } } },
  };
  const context = vm.createContext(scope);
  let translator,
    permissions,
    useDocType,
    state = 0;
  const UI = new Proxy({}, { get: (_, name) => String(name) });
  function leaf(name) {
    if (name === "react/jsx-runtime")
      return {
        jsx: (type, props) => ({ type, props }),
        jsxs: (type, props) => ({ type, props }),
      };
    if (name === "react")
      return {
        useState: (value) => {
          const index = state++;
          return [
            index === 0 ? !!options.open : value,
            (next) => states.push([index, next]),
          ];
        },
        useMemo: (fn) => fn(),
        useRef: () => ({ current: null }),
        useLayoutEffect: () => {},
      };
    if (name === "frappe-react-sdk")
      return {
        useFrappeGetCall: (method, args, key) => {
          calls.push({
            method,
            args,
            key: typeof key === "function" ? key() : key,
          });
          return {
            data:
              method === "frappe.client.get_value"
                ? { message: options.title ?? {} }
                : { message: options.results ?? [] },
          };
        },
      };
    if (name === "@/lib/translate") return { default: translator };
    if (name === "@/lib/permissions") return permissions;
    if (name === "@/hooks/useDocType") return useDocType;
    if (name === "usehooks-ts")
      return {
        useDebounceValue: () => [
          options.search || "",
          (value) => states.push(["search", value]),
        ],
      };
    if (name === "@/hooks/useResetScrollOnSearch")
      return { default: () => ({ current: null }) };
    if (name === "@/lib/frappe")
      return {
        slug: (value) => value.toLowerCase().replaceAll(" ", "-"),
        getSystemDefault: () => 20,
      };
    if (name === "@/lib/utils")
      return { cn: (...values) => values.filter(Boolean).join(" ") };
    return UI;
  }
  function load(relative) {
    const source = fs.readFileSync(path.join(banking, relative), "utf8");
    const code = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText;
    const exports = {};
    vm.runInContext(`(function(require,exports){${code}\n})`, context, {
      filename: relative,
    })(leaf, exports);
    return exports;
  }
  translator = load("src/lib/translate.ts").default;
  permissions = load("src/lib/permissions.ts");
  useDocType = load("src/hooks/useDocType.ts");
  const component = load("src/components/common/LinkFieldCombobox.tsx").default;
  const props = {
    doctype,
    onChange: (value) => changes.push(value),
    ...options.props,
  };
  const original = JSON.stringify(props);
  const tree = component(props);
  assert.equal(JSON.stringify(props), original);
  return { tree, calls, changes, states, translator, props };
}
function nodes(node, type) {
  if (Array.isArray(node)) return node.flatMap((child) => nodes(child, type));
  if (!node || typeof node !== "object") return [];
  return [
    ...(node.type === type ? [node] : []),
    ...nodes(node.props?.children, type),
  ];
}
function text(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(text).join("");
  return typeof node === "object" ? text(node.props?.children) : String(node);
}
function createAnchor(result) {
  return nodes(result.tree, "a").find((anchor) =>
    anchor.props.href.includes("/new-")
  );
}

for (const [doctype, display] of [
  ["Company", "Azienda"],
  ["Payment Entry", "Registrazione di pagamento"],
  ["QA Custom Kind", "QA Custom Kind"],
]) {
  test(`localized creation and default prompt keep canonical ${doctype} queries and route`, () => {
    const result = environment({ doctype });
    assert.equal(text(createAnchor(result)), `Crea ${display}`);
    assert.equal(
      nodes(result.tree, "CommandInput")[0].props.placeholder,
      `Seleziona ${display}`
    );
    assert.equal(text(nodes(result.tree, "Button")[0]), `Seleziona ${display}`);
    const slug = doctype.toLowerCase().replaceAll(" ", "-");
    assert.equal(
      createAnchor(result).props.href,
      `/desk/${slug}/new-${slug}-1`
    );
    assert.ok(result.calls.every((call) => call.args.doctype === doctype));
    assert.equal(result.changes.length, 0);
  });
}
test("empty English dictionary preserves whole-frame fallback and raw type", () => {
  const result = environment({ dictionary: {} });
  assert.equal(text(createAnchor(result)), "Create New Company");
  assert.equal(
    nodes(result.tree, "CommandInput")[0].props.placeholder,
    "Select Company"
  );
});
test("explicit authored or empty placeholders remain exact while the selected business title stays raw", () => {
  for (const placeholder of ['Authored <label> & "quoted"', ""]) {
    const result = environment({
      props: { placeholder, value: "Company", useInForm: true },
    });
    assert.equal(
      nodes(result.tree, "CommandInput")[0].props.placeholder,
      placeholder
    );
    assert.equal(text(nodes(result.tree, "Button")[0]), "Company");
    assert.equal(
      nodes(result.tree, "a")[0].props.href,
      "/desk/company/Company"
    );
  }
});
test("actual create permission uses canonical boot membership rather than the translated caption", () => {
  const result = environment({ denied: true });
  assert.equal(createAnchor(result), undefined);
  assert.equal(result.changes.length, 0);
});
test("supplied option labels and selection callbacks preserve their business identity", () => {
  const results = [
    {
      value: "Company",
      label: 'Authored <name> & "quoted"',
      description: "Description",
    },
  ];
  const result = environment({ results });
  const option = nodes(result.tree, "CommandItem").find(
    (item) => item.props.onSelect
  );
  assert.match(text(option), /Authored <name> & "quoted"/);
  option.props.onSelect();
  assert.deepEqual(result.changes, ["Company"]);
  assert.deepEqual(result.states, [[0, false]]);
});
test("custom search parameters and metadata title delivery remain canonical", () => {
  const result = environment({
    open: true,
    search: "needle",
    meta: { title_field: "title", show_title_field_in_link: 1 },
    title: { title: "Company" },
    props: {
      value: "RAW",
      limit: 7,
      searchAPIPath: "example.test.search",
      searchfield: "code",
      reference_doctype: "Sales Invoice",
      customQuery: {
        query: "example.test.query",
        filters: { company: "Company" },
      },
    },
  });
  const search = result.calls.find(
    (call) => call.method === "example.test.search"
  );
  assert.deepEqual(JSON.parse(JSON.stringify(search.args)), {
    doctype: "Company",
    txt: "needle",
    page_length: 7,
    query: "example.test.query",
    searchfield: "code",
    filters: '{"company":"Company"}',
    reference_doctype: "Sales Invoice",
  });
  assert.equal(
    search.key,
    'example.test.search_Company_needle_7_{"company":"Company"}_example.test.query_Sales Invoice_code'
  );
  assert.equal(text(nodes(result.tree, "Button")[0]), "Company");
});
test("hostile or empty translated type values remain display data and never affect routing", () => {
  for (const display of ['<b>Azienda</b> & "quoted"', ""]) {
    const result = environment({
      dictionary: { ...messages, Company: display },
    });
    assert.equal(text(createAnchor(result)), `Crea ${display || "Company"}`);
    assert.equal(
      createAnchor(result).props.href,
      "/desk/company/new-company-1"
    );
    assert.ok(result.calls.every((call) => call.args.doctype === "Company"));
  }
});
