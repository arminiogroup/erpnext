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
const FRAMES = ["Select a value for {0} in {1} {2}."];
const TARGETS = {
	"Select a value for {0} in {1} {2}.": "Nel documento {1} {2}, selezionare un valore per {0}.",
	Quotation: "Preventivo",
	"Quotation To": "Tipo di destinatario del preventivo",
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

test("actual Quotation controller missing recipient, Lead bypass and parent validation", () => {
	for (const recipient of [null, "Lead", "Customer", "Prospect", "CRM Deal"])
		for (const company of ["", "COMPANY-RAW"]) {
			const e = environment(),
				controller = Object.create(e.controllers.Quotation.prototype),
				doc = { doctype: "Quotation", name: "QUOTE-RAW", quotation_to: recipient, company };
			controller.frm = { doc };
			const before = plain(doc),
				valid = controller.validate_company_and_party();
			assert.deepEqual(doc, before);
			assert.equal(valid, recipient === "Lead" || Boolean(recipient && company));
			assert.equal(e.requests.length, 0);
			if (!recipient)
				assert.equal(
					e.events[0][1],
					TARGETS[FRAMES[0]]
						.replace("{0}", TARGETS["Quotation To"])
						.replace("{1}", TARGETS.Quotation)
						.replace("{2}", "QUOTE-RAW")
				);
		}
});
test("actual owning metadata override and ignore flag preserve contracts", () => {
	const e = environment({ ...TARGETS, "Custom recipient:Quotation": "Destinatario personalizzato" }),
		field = e.w.frappe.meta.get_docfield("Quotation", "quotation_to");
	field.label = "Custom recipient";
	const c = Object.create(e.controllers.Quotation.prototype);
	c.frm = { doc: { doctype: "Quotation", name: "QUOTE-RAW", quotation_to: null, company: "" } };
	assert.equal(c.validate_company_and_party(), false);
	assert(e.events[0][1].includes("Destinatario personalizzato"));
	c.frm.doc.quotation_to = "Customer";
	e.w.frappe.flags.ignore_company_party_validation = true;
	assert.equal(c.validate_company_and_party(), true);
});
test("English dictionary fallback preserves raw document name", () => {
	const e = environment({}),
		c = Object.create(e.controllers.Quotation.prototype);
	c.frm = { doc: { doctype: "Quotation", name: "QUOTE-RAW", quotation_to: null } };
	assert.equal(c.validate_company_and_party(), false);
	assert.equal(
		e.events[0][1],
		FRAMES[0].replace("{0}", "Quotation To").replace("{1}", "Quotation").replace("{2}", "QUOTE-RAW")
	);
});
