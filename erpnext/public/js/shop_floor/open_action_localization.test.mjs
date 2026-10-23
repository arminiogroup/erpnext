import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const { JSDOM } = require("jsdom");
const jquery = require("jquery");

test("actual work-order detail action keeps encoded link, loader filters and back callback", () => {
  const dom = new JSDOM("<body></body>");
  const $ = jquery(dom.window);
  const requests = [];
  const host = {
    $,
    jQuery: $,
    cint: (value) => Number.parseInt(value, 10) || 0,
    frappe: {
      ui: {},
      utils: {},
      _messages: { Open: "Aperto", "Open:Access": "Apri" },
      session: { user: "translator@example.com" },
      call: (options) => requests.push(options),
    },
  };
  host.window = host;
  host.frappe.provide = (path) => {
    let value = host;
    for (const part of path.split(".")) value = value[part] ??= {};
  };
  const context = vm.createContext(host);
  const frappeRoot = new URL(
    "../../../../../frappe/frappe/public/js/frappe/",
    import.meta.url
  );
  for (const file of ["format.js", "translate.js", "utils/utils.js"]) {
    const code = readFileSync(new URL(file, frappeRoot), "utf8").replace(
      /^import .*;\n/gm,
      ""
    );
    vm.runInContext(`(function(){${code}\n})();`, context);
  }
  vm.runInContext(
    readFileSync(new URL("./shop_floor.js", import.meta.url), "utf8"),
    context
  );
  const view = Object.create(host.frappe.ui.ShopFloor.prototype);
  view.body = $("<div></div>");
  view.detail_container = $("<div></div>");
  view.board_container = $("<div><div class='sf-wo-card'></div></div>");
  const name = "WO / caffè <privato>";
  view.board_container.find(".sf-wo-card").attr("data-name", name);
  view.open_wo(null);
  assert.equal(requests.length, 0);
  view.open_wo(name);
  const anchor = view.detail_container[0].querySelector("a");
  assert.equal(anchor.textContent, "Apri");
  assert.equal(
    anchor.getAttribute("href"),
    `/app/work-order/${encodeURIComponent(name)}`
  );
  assert.equal(anchor.target, "_blank");
  assert.equal(view.selected_wo, name);
  assert.equal(view.op_state.work_order, name);
  assert.equal(view.op_state.workstation, null);
  assert.equal(view.detail_container[0].querySelector("privato"), null);
  assert.equal(requests.length, 1);
  assert.equal(
    requests[0].method,
    "erpnext.manufacturing.page.shop_floor.shop_floor.get_data"
  );
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0].args)), {
    workstation: null,
    work_order: name,
  });
  assert.equal(typeof requests[0].callback, "function");
  assert.ok(view.board_container.find(".sf-wo-card").hasClass("sf-selected"));
  view.detail_container.find(".sf-detail-back").trigger("click");
  assert.equal(view.selected_wo, null);
  assert.equal(view.op_container_target, null);
  assert.equal(view.detail_container.html(), "");
  assert.ok(!view.body.hasClass("detail-open"));
  assert.ok(!view.board_container.find(".sf-wo-card").hasClass("sf-selected"));
  assert.equal(requests.length, 1);
  assert.equal(host.__("Open"), "Aperto");
  assert.equal(host.frappe.session.user, "translator@example.com");
  dom.window.close();
});
