/* eslint-env node */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const { createRequire } = require("node:module");
const appRoot = path.resolve(__dirname, "../../../../..");
const apps = path.dirname(appRoot);
const { parse } = createRequire(path.join(apps, "frappe/package.json"))("@babel/parser");
const plain = (value) => JSON.parse(JSON.stringify(value));
const FRAMES = ["Editing {0} is not allowed as per POS Profile settings"];
const TARGETS = {
	"Editing {0} is not allowed as per POS Profile settings":
		"Le impostazioni del profilo POS non consentono di modificare {0}.",
	Rate: "Prezzo unitario",
	Discount: "Sconto",
};
function environment(messages = TARGETS) {
	const w = {};
	w.window = w;
	w.locals = { DocType: {} };
	w.$ = w.jQuery = {
		extend: Object.assign,
		isPlainObject: (v) => v !== null && typeof v === "object" && !Array.isArray(v),
		each(values, callback) {
			for (const [k, v] of Object.entries(values))
				if (callback(Number.isNaN(+k) ? k : +k, v) === false) break;
		},
	};
	const forms = {},
		controllers = {},
		events = [],
		requests = [],
		prompts = [],
		dialogs = [];
	w.fetch = () => {
		throw Error("No network");
	};
	w.XMLHttpRequest = class {
		constructor() {
			throw Error("No XHR");
		}
	};
	w.erpnext = {
		taxes_and_totals: class {},
		PointOfSale: {},
		pre_sales: { set_as_lost() {} },
		accounts: { taxes: { setup_tax_validations() {}, setup_tax_filters() {} } },
	};
	w.flt = (v) => Number(v) || 0;
	w.copy_dict = plain;
	w.frappe = {
		_messages: messages,
		ui: {
			form: {
				on(dtype, handlers) {
					if (typeof handlers === "object") forms[dtype] = handlers;
				},
				set_controller(dtype, controller) {
					controllers[dtype] = controller;
				},
			},
			Dialog: class {
				constructor(options) {
					this.options = options;
					this.fields_dict = { items: { df: { data: [] }, grid: { refresh() {} } } };
					dialogs.push(this);
				}
				show() {
					this.shown = true;
				}
				hide() {
					this.hidden = true;
				}
			},
		},
		boot: { lang: "it" },
		session: { user: "translator@example.com" },
		flags: {},
		utils: {
			play_sound(name) {
				events.push(["sound", name]);
			},
		},
		provide(name) {
			name.split(".").reduce((o, key) => (o[key] ||= {}), w);
		},
		throw(message) {
			throw Error(message);
		},
		msgprint(...args) {
			events.push(["notice", ...args]);
		},
		show_alert(message) {
			events.push(["alert", message]);
		},
		call(spec) {
			requests.push(spec);
		},
		prompt(...args) {
			prompts.push(args);
			return {
				get_primary_btn() {
					return {
						addClass(name) {
							events.push(["button-class", name]);
						},
					};
				},
			};
		},
	};
	const context = vm.createContext(w);
	const run = (app, file) =>
		vm.runInContext(fs.readFileSync(apps + "/" + app + "/" + file, "utf8"), context, { filename: file });
	for (const file of [
		"frappe/public/js/frappe/utils/datatype.js",
		"frappe/public/js/frappe/format.js",
		"frappe/public/js/frappe/translate.js",
		"frappe/public/js/frappe/model/model.js",
		"frappe/public/js/frappe/model/meta.js",
	])
		run("frappe", file);
	const utility = fs.readFileSync(apps + "/frappe/frappe/public/js/frappe/utils/utils.js", "utf8");
	const methods = [];
	function walk(node) {
		if (!node || typeof node !== "object") return;
		if (node.type === "ObjectProperty" && ["bold", "escape_html"].includes(node.key?.name))
			methods.push(utility.slice(node.start, node.end));
		for (const value of Object.values(node))
			if (Array.isArray(value)) value.forEach(walk);
			else if (value && typeof value === "object") walk(value);
	}
	walk(parse(utility, { sourceType: "module" }));
	assert.equal(methods.length, 2);
	vm.runInContext("Object.assign(frappe.utils,{" + methods.join(",") + "})", context);
	for (const file of [
		"erpnext/public/js/controllers/transaction.js",
		"erpnext/public/js/utils/sales_common.js",
		"erpnext/selling/doctype/quotation/quotation.js",
		"erpnext/selling/page/point_of_sale/pos_item_cart.js",
		"erpnext/setup/doctype/company/company.js",
	])
		run("erpnext", file);
	for (const [dtype, file] of [
		["Quotation", "erpnext/selling/doctype/quotation/quotation.json"],
		["Item", "erpnext/stock/doctype/item/item.json"],
		["Company", "erpnext/setup/doctype/company/company.json"],
	]) {
		const meta = JSON.parse(fs.readFileSync(apps + "/erpnext/" + file, "utf8"));
		for (const field of meta.fields) field.parent = dtype;
		w.locals.DocType[dtype] = meta;
		w.frappe.meta.sync(meta);
	}
	return { w, forms, controllers, events, requests, prompts, dialogs };
}

test("real numpad permissions preserve raw actions, delivery, sound and state", () => {
	for (const action of ["rate", "discount_percentage", "qty"])
		for (const allowed of [false, true]) {
			const e = environment(),
				cart = Object.create(e.w.erpnext.PointOfSale.ItemCart.prototype),
				calls = [];
			Object.assign(cart, {
				allow_rate_change: allowed,
				allow_discount_change: allowed,
				prev_action: undefined,
				numpad_value: "BEFORE",
				highlight_numpad_btn(...args) {
					calls.push(["highlight", args[1]]);
				},
				events: {
					numpad_event(...args) {
						calls.push(["delivery", ...args]);
					},
				},
			});
			cart.on_numpad_event({
				attr(key) {
					assert.equal(key, "data-button-value");
					return action;
				},
			});
			const rejected = !allowed && action !== "qty";
			assert.equal(
				e.events.some((r) => r[0] === "alert"),
				rejected
			);
			if (rejected) {
				assert.equal(cart.numpad_value, "BEFORE");
				assert.equal(cart.prev_action, undefined);
				assert.deepEqual(calls, []);
				assert.equal(
					e.events[0][1].message,
					TARGETS[FRAMES[0]].replace(
						"{0}",
						"<b>" + TARGETS[action === "rate" ? "Rate" : "Discount"] + "</b>"
					)
				);
				assert.deepEqual(plain(e.events[1]), ["sound", "error"]);
			} else {
				assert.equal(cart.prev_action, action);
				assert.equal(cart.numpad_value, "");
				assert.deepEqual(calls, [
					["highlight", action],
					["delivery", "", action],
				]);
			}
			assert.equal(e.requests.length, 0);
		}
});
test("empty dictionary keeps English field labels and whole fallback", () => {
	const e = environment({}),
		cart = Object.create(e.w.erpnext.PointOfSale.ItemCart.prototype);
	Object.assign(cart, { allow_rate_change: false, allow_discount_change: false });
	cart.on_numpad_event({
		attr() {
			return "rate";
		},
	});
	assert.equal(e.events[0][1].message, FRAMES[0].replace("{0}", "<b>Rate</b>"));
});
