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
	"This will start deleting records associated with {0}, including transactions and other records selected by the deletion workflow. Master records such as Leads and related addresses may also be affected. This action cannot be undone.",
];
const TARGETS = {
	"This will start deleting records associated with {0}, including transactions and other records selected by the deletion workflow. Master records such as Leads and related addresses may also be affected. This action cannot be undone.":
		"Verrà avviata l’eliminazione dei record associati a {0}, comprese le transazioni e gli altri record selezionati dal processo di eliminazione. Potranno essere interessate anche anagrafiche come i contatti commerciali e i relativi indirizzi. Questa operazione non può essere annullata.",
	Delete: "Elimina",
	"COMPANY<&>RAW": "DO NOT TRANSLATE COMPANY",
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

test("actual Company handler only renders truthful prompt, never invokes destructive closure", () => {
	const e = environment(),
		doc = { name: "COMPANY<&>RAW" },
		before = plain(doc);
	e.forms.Company.delete_company_transactions({ doc });
	assert.equal(e.requests.length, 1);
	assert.equal(
		e.requests[0].method,
		"erpnext.setup.doctype.transaction_deletion_record.transaction_deletion_record.is_deletion_doc_running"
	);
	assert.deepEqual(plain(e.requests[0].args), { company: doc.name });
	e.requests[0].callback({});
	assert.equal(e.prompts.length, 1);
	const [field, confirm, title, button] = e.prompts[0];
	assert.equal(typeof confirm, "function");
	assert.equal(
		field.description,
		TARGETS[FRAMES[0]].replace("{0}", "<strong>COMPANY&lt;&amp;&gt;RAW</strong>")
	);
	assert.equal(field.fieldname, "company_name");
	assert.equal(field.reqd, 1);
	assert.equal(e.requests.length, 1);
	assert.deepEqual(doc, before);
	assert(title.includes("COMPANY&lt;&amp;&gt;RAW"));
	assert.equal(button, TARGETS.Delete);
});
test("running error blocks prompt and creation route remains uninvoked", () => {
	const e = environment();
	e.forms.Company.delete_company_transactions({ doc: { name: "RAW" } });
	e.requests[0].callback({ exc: "running" });
	assert.equal(e.prompts.length, 0);
	assert.equal(e.requests.length, 1);
});
test("empty dictionary and custom company name remain raw", () => {
	const e = environment({});
	e.forms.Company.delete_company_transactions({ doc: { name: "USER AUTHORED NAME" } });
	e.requests[0].callback({});
	assert.equal(
		e.prompts[0][0].description,
		FRAMES[0].replace("{0}", "<strong>USER AUTHORED NAME</strong>")
	);
	assert.equal(e.requests.length, 1);
});
