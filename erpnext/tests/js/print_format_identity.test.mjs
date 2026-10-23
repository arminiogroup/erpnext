import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

test("company-details eligibility reads the canonical format and preserves raw and letterhead fallbacks", () => {
  const input = { value: "Ordine di vendita (classico)" };
  const letterhead = { value: "" };
  const wrapper = {};
  const selector = {
    val: () => input.value,
    closest: () => ({ get: () => wrapper }),
  };
  const route = ["print", "Unrelated example", "QA-1"];
  const sandbox = {
    $: (query) =>
      query.includes("letterhead") ? { val: () => letterhead.value } : selector,
    frappe: {
      ui: { form: { ControlData: class {} } },
      get_route: () => route,
      call() {
        assert.fail("No company request is allowed");
      },
    },
    Awesomplete: null,
    setTimeout() {
      assert.fail("An unrelated route must not schedule company work");
    },
  };
  const context = vm.createContext(sandbox);
  const link = new URL(
    "../../../../frappe/frappe/public/js/frappe/form/controls/link.js",
    import.meta.url
  );
  vm.runInContext(
    readFileSync(link, "utf8").replace(
      'import Awesomplete from "../../ui/awesomplete";',
      ""
    ),
    context
  );
  const field = Object.create(sandbox.frappe.ui.form.ControlLink.prototype);
  field.$input = { val: () => input.value };
  field.title_value_map = {
    "Ordine di vendita (classico)": "Sales Order Classic",
  };
  wrapper.fieldobj = field;
  vm.runInContext(
    readFileSync(new URL("../../public/js/print.js", import.meta.url), "utf8"),
    context
  );
  assert.equal(sandbox.should_fetch_company_details(), true);
  assert.equal(field.get_input_value(), "Sales Order Classic");
  input.value = "Open";
  assert.equal(sandbox.should_fetch_company_details(), false);
  letterhead.value = "Company Letterhead";
  assert.equal(sandbox.should_fetch_company_details(), true);
  letterhead.value = "";
  delete wrapper.fieldobj;
  input.value = "Sales Order Classic";
  assert.equal(sandbox.should_fetch_company_details(), true);
  input.value = "Ordine di vendita (classico)";
  assert.equal(sandbox.should_fetch_company_details(), false);
  assert.deepEqual(route, ["print", "Unrelated example", "QA-1"]);
});
