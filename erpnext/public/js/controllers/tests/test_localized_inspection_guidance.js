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
const FRAMES = [
	"No items are available for this quality inspection. Check the items and their quality inspection settings.",
];
const TARGETS = {
	"No items are available for this quality inspection. Check the items and their quality inspection settings.":
		"Non sono disponibili articoli per questo controllo qualità. Verificare gli articoli e le relative impostazioni di controllo qualità.",
	"Quality Inspection Not Configured": "Controllo qualità non configurato",
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

test("actual QI initial response branches preserve request, eligibility and dialog behavior", () => {
	for (const [dtype, purpose] of [
		["Purchase Receipt", null],
		["Delivery Note", null],
		["Stock Entry", "Manufacture"],
		["Stock Entry", "Material Issue"],
	]) {
		for (const response of [
			[],
			[
				{
					item_code: "ITEM-RAW",
					name: "ROW-RAW",
					qty: 2,
					is_finished_item: 1,
					s_warehouse: "WH-RAW",
					t_warehouse: "TARGET-RAW",
				},
			],
			[{ item_code: "ITEM-RAW", quality_inspection: "QI-RAW" }],
		]) {
			const e = environment(),
				c = Object.create(e.w.erpnext.TransactionController.prototype),
				doc = {
					doctype: dtype,
					name: "DOC-RAW",
					company: "COMPANY-RAW",
					docstatus: 0,
					purpose,
					items: [],
				};
			c.frm = { doc };
			const before = plain(doc);
			c.make_quality_inspection();
			assert.equal(e.requests.length, 1);
			const request = e.requests[0];
			assert.equal(
				request.method,
				"erpnext.controllers.stock_controller.check_item_quality_inspection"
			);
			assert.deepEqual(plain(request.args), { doctype: dtype, docstatus: 0, items: [] });
			request.callback({ message: response });
			assert.deepEqual(doc, before);
			assert.equal(e.requests.length, 1);
			if (!response.length) {
				assert.equal(e.events[0][1].message, TARGETS[FRAMES[0]]);
				assert.equal(e.events[0][1].title, TARGETS["Quality Inspection Not Configured"]);
				assert.equal(e.dialogs[0].shown, undefined);
			} else assert.equal(Boolean(e.dialogs[0].shown), !response[0].quality_inspection);
		}
	}
});
test("empty-dictionary fallback requires no Item label or guessed Purchase/Delivery grammar", () => {
	const e = environment({}),
		c = Object.create(e.w.erpnext.TransactionController.prototype);
	c.frm = {
		doc: { doctype: "Stock Entry", name: "RAW", docstatus: 0, purpose: "Material Issue", items: [] },
	};
	c.make_quality_inspection();
	e.requests[0].callback({ message: [] });
	assert.equal(e.events[0][1].message, FRAMES[0]);
	assert.equal(e.requests.length, 1);
});
