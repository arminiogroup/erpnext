# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import itertools
import json
import re
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.budget import budget
from erpnext.controllers.budget_controller import BudgetValidation

INDIVIDUAL_ANNUAL = "Annual Budget for Account {0} against {1}: {2} is {3}. It will be exceeded by {4}"
INDIVIDUAL_MONTHLY = (
	"Accumulated Monthly Budget for Account {0} against {1}: {2} is {3}. It will be exceeded by {4}"
)
CUMULATIVE_ANNUAL = (
	"Annual Budget for Account {0} against {1} {2} is {3}. It will be collectively ({4}) exceeded by {5}"
)
CUMULATIVE_MONTHLY = "Accumulated Monthly Budget for Account {0} against {1} {2} is {3}. It will be collectively ({4}) exceeded by {5}"
ALREADY_EXCEEDED = "{0} Budget for Account {1} against {2} {3} is {4}. It is already exceeded by {5}."
WILL_EXCEED = "{0} Budget for Account {1} against {2} {3} is {4}. It will be exceeded by {5}."
FORBIDDEN_DIMENSION = "Not allowed to create accounting dimension for {0}"
STANDALONE_DIMENSION = (
	"{0} cannot be used as an accounting dimension as it is not a standalone document type."
)

PRODUCERS = (
	("controllers/budget_controller.py", "python"),
	("accounts/doctype/budget/budget.py", "python"),
	("accounts/doctype/accounting_dimension/accounting_dimension.py", "python"),
	("accounts/doctype/accounting_dimension/accounting_dimension.js", "javascript"),
	("accounts/report/budget_variance_report/budget_variance_report.py", "python"),
)
METADATA = (
	("accounts", "Budget"),
	("accounts", "Accounting Dimension"),
	("accounts", "Cost Center"),
	("accounts", "Account"),
	("projects", "Project"),
	("stock", "Warehouse"),
	("setup", "Company"),
	("selling", "Sales Team"),
	("selling", "Selling Settings"),
	("buying", "Purchase Order"),
	("stock", "Material Request"),
)
TARGETS = {
	INDIVIDUAL_ANNUAL: "Il budget annuale per il conto {0}, relativo a {1}: {2}, è {3}. Verrà superato di {4}.",
	INDIVIDUAL_MONTHLY: "Il budget mensile cumulato per il conto {0}, relativo a {1}: {2}, è {3}. Verrà superato di {4}.",
	CUMULATIVE_ANNUAL: "Il budget annuale per il conto {0}, relativo a {1} {2}, è {3}. Il totale ({4}) lo supererà di {5}.",
	CUMULATIVE_MONTHLY: "Il budget mensile cumulato per il conto {0}, relativo a {1} {2}, è {3}. Il totale ({4}) lo supererà di {5}.",
	ALREADY_EXCEEDED: "Il budget {0} per il conto {1}, relativo a {2} {3}, è {4}. È già stato superato di {5}.",
	WILL_EXCEED: "Il budget {0} per il conto {1}, relativo a {2} {3}, è {4}. Verrà superato di {5}.",
	FORBIDDEN_DIMENSION: "Non è consentito creare una dimensione contabile per {0}.",
	STANDALONE_DIMENSION: "{0} non può essere usato come dimensione contabile perché non è un tipo di documento autonomo.",
	"Show {0}": "Mostra {0}",
	"Project": "Progetto",
	"Cost Center": "Centro di costo",
	"Warehouse": "Magazzino",
	"Accounting Dimension": "Dimensione contabile",
	"Company": "Azienda",
	"Account": "Conto",
	"Sales Team": "Team di vendita",
	"Selling Settings": "Impostazioni di vendita",
	"Purchase Order": "Ordine di acquisto",
	"Material Request": "Richiesta di materiale",
	"Actual Expense": "Costo effettivo",
	"This Document": "Questo documento",
	"Annual": "Annuale",
	"Accumulated Monthly": "Cumulativo mensile",
	"Budget Exceeded": "Budget superato",
	"Total Expenses booked through": "Costi totali registrati tramite",
	"Actual Expenses": "Costi effettivi",
	"Material Requests": "Richieste di materiale",
	"Unbilled Orders": "Ordini da fatturare",
	"Document Type already used as a dimension": "Tipo di documento già usato come dimensione",
}


def metadata_path(module, doctype):
	slug = frappe.scrub(doctype)
	return Path(frappe.get_app_path("erpnext", module, "doctype", slug, f"{slug}.json"))


def read_meta(module, doctype):
	data = json.loads(metadata_path(module, doctype).read_text())
	meta = Meta.__new__(Meta)
	meta.__dict__.update(
		name=data["name"],
		istable=data.get("istable", 0),
		issingle=data.get("issingle", 0),
		fields=[frappe._dict(field) for field in data["fields"]],
		_fields={field["fieldname"]: frappe._dict(field) for field in data["fields"]},
	)
	return meta


def extracted_keys():
	keys = set()
	paths = [(Path(frappe.get_app_path("erpnext", path)), kind) for path, kind in PRODUCERS]
	paths.extend((metadata_path(module, doctype), "doctype") for module, doctype in METADATA)
	for path, kind in paths:
		with path.open("rb") as source:
			keys.update(
				message
				for _, message, _, context in extract(
					f"frappe.gettext.extractors.{kind}.extract", source, keywords=PYTHON_KEYWORDS
				)
				if context is None
			)
	return keys


def make_doc(cls, **values):
	doc = cls.__new__(cls)
	doc.__dict__.update(flags=frappe._dict(), **values)
	return doc


def raise_message(message, exc=frappe.ValidationError, **kwargs):
	raise exc(message)


class TestBudgetLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		keys = extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={k: v for k, v in TARGETS.items() if k in keys},
			)
		)
		self.metas = {doctype: read_meta(module, doctype) for module, doctype in METADATA}
		# A generated dimension's label can differ from both its fieldname and linked DocType.
		custom = frappe._dict(
			fieldname="funding_axis", label="Project", fieldtype="Link", options="Warehouse"
		)
		self.metas["Budget"]._fields[custom.fieldname] = custom
		self.metas["Budget"].fields.append(custom)
		self.enterContext(patch("frappe.get_meta", side_effect=lambda name: self.metas[name]))
		self.enterContext(patch("frappe.get_system_settings", return_value="Banker's Rounding"))
		self.throw = self.enterContext(patch("frappe.throw", side_effect=raise_message))
		self.messages = self.enterContext(patch("frappe.msgprint"))
		self.enterContext(patch("frappe.get_cached_value", side_effect=self.cached_value))
		for path in ("erpnext.controllers.budget_controller", "erpnext.accounts.doctype.budget.budget"):
			self.enterContext(
				patch(path + ".fmt_money", side_effect=lambda value, currency=None: f"{value:.2f} {currency}")
			)
		self.db = self.enterContext(patch("frappe.db", new=Mock(db_type="mariadb")))
		self.enterContext(patch.object(frappe.local, "db", self.db, create=True))
		self.enterContext(patch.dict(frappe.flags, exception_approver_role=None))
		self.enterContext(
			patch("frappe.utils.data.get_url", side_effect=lambda uri: "https://example.invalid" + uri)
		)
		self.actual, self.requested, self.ordered, self.monthly = 50, 20, 30, 80
		self.queries = []
		self.db.sql.side_effect = self.sql
		self.validator = BudgetValidation.__new__(BudgetValidation)
		self.validator.company = "Company"
		self.validator.exception_approver_role = None

	def cached_value(self, doctype, name, field):
		if doctype == "Company":
			self.assertEqual((name, field), ("Company", "default_currency"))
			return "EUR"
		self.assertEqual((doctype, name), ("Fiscal Year", "FY-RAW"))
		return {"year_start_date": "2026-01-01", "year_end_date": "2026-12-31"}[field]

	def sql(self, query, values=None, **kwargs):
		self.queries.append((query, deepcopy(values), kwargs.copy()))
		if "tabBudget Distribution" in query:
			return [frappe._dict(accumulated_amount=self.monthly)]
		if "tabGL Entry" in query:
			return [[self.actual]]
		if "tabMaterial Request Item" in query:
			return [[self.requested]]
		if "tabPurchase Order Item" in query:
			return [[self.ordered]]
		raise AssertionError(query)

	def key(self, field="cost_center"):
		# All business identities intentionally collide with real translation source keys.
		return (field, "Project", "Account")

	def vmap(self, **values):
		result = frappe._dict(
			budget_doc=frappe._dict(
				name="BUDGET-RAW",
				applicable_on_purchase_order=1,
				applicable_on_material_request=1,
				applicable_on_booking_actual_expenses=1,
				applicable_on_cumulative_expense=1,
				action_if_annual_budget_exceeded_on_po="Warn",
				action_if_accumulated_monthly_budget_exceeded_on_po="Warn",
				action_if_annual_budget_exceeded_on_mr="Warn",
				action_if_accumulated_monthly_budget_exceeded_on_mr="Warn",
				action_if_annual_budget_exceeded="Warn",
				action_if_accumulated_monthly_budget_exceeded="Warn",
				action_if_annual_exceeded_on_cumulative_expense="Warn",
				action_if_accumulated_monthly_exceeded_on_cumulative_expense="Warn",
			),
			budget_amount=100,
			accumulated_monthly_budget=80,
			ordered_amount=20,
			requested_amount=30,
			actual_expense=40,
			current_ordered_amount=5,
			current_requested_amount=10,
			current_actual_exp_amount=15,
		)
		result.update(values)
		return result

	def params(self, field="cost_center", **values):
		result = frappe._dict(
			company="Company",
			account="Account",
			expense_account="Account",
			item_code="ITEM-RAW",
			budget_against_field=field,
			budget_against_doctype="Warehouse" if field == "funding_axis" else frappe.unscrub(field),
			is_tree=False,
			budget_start_date="2026-01-01",
			budget_end_date="2026-12-31",
			from_fiscal_year="FY-RAW",
			to_fiscal_year="FY-RAW",
			posting_date="2026-04-15",
			for_material_request=1,
			for_purchase_order=1,
		)
		result[field] = "Project"
		result.update(values)
		return result

	def dimensions(self):
		return (("cost_center", "Centro di costo"), ("project", "Progetto"), ("funding_axis", "Progetto"))

	def test_configured_extractors_register_all_display_literals(self):
		keys = extracted_keys()
		for source in (
			INDIVIDUAL_ANNUAL,
			INDIVIDUAL_MONTHLY,
			CUMULATIVE_ANNUAL,
			CUMULATIVE_MONTHLY,
			ALREADY_EXCEEDED,
			WILL_EXCEED,
			FORBIDDEN_DIMENSION,
			STANDALONE_DIMENSION,
			"Show {0}",
			"Project",
			"Purchase Order",
			"Material Request",
			"Actual Expense",
			"This Document",
		):
			with self.subTest(source=source):
				self.assertIn(source, keys)

	def test_real_meta_preserves_configured_label_instead_of_unscrubbing(self):
		for field, label in self.dimensions():
			self.assertEqual(self.metas["Budget"].get_translated_label(field), label)
		self.assertEqual(self.metas["Budget"].get_field("funding_axis").options, "Warehouse")
		self.metas["Budget"].get_field("funding_axis").label = "Programma finanziario"
		self.assertEqual(self.metas["Budget"].get_translated_label("funding_axis"), "Programma finanziario")

	def test_individual_annual_message_translates_dimension_only(self):
		config = frappe._dict(applies=1, action_for_annual="Warn", action_for_monthly="Warn")
		for field, label in self.dimensions():
			with self.subTest(field=field):
				self.messages.reset_mock()
				self.validator.handle_individual_doctype_action(
					self.key(field), config, "BUDGET-RAW", 100, 90, 30, 200
				)
				self.messages.assert_called_once_with(
					f"Il budget annuale per il conto <strong>Account</strong>, relativo a <strong>{label}</strong>: <strong>Project</strong>, è <strong>100.00 EUR</strong>. Verrà superato di <strong>20.00 EUR</strong>.",
					"Budget superato",
				)

	def test_individual_monthly_message_translates_dimension_only(self):
		config = frappe._dict(applies=1, action_for_annual="Warn", action_for_monthly="Warn")
		for field, label in self.dimensions():
			with self.subTest(field=field):
				self.messages.reset_mock()
				self.validator.handle_individual_doctype_action(
					self.key(field), config, "BUDGET-RAW", 200, 90, 30, 100
				)
				self.messages.assert_called_once_with(
					f"Il budget mensile cumulato per il conto <strong>Account</strong>, relativo a <strong>{label}</strong>: <strong>Project</strong>, è <strong>100.00 EUR</strong>. Verrà superato di <strong>20.00 EUR</strong>.",
					"Budget superato",
				)

	def test_individual_equal_below_and_disabled_controls(self):
		for applies, existing, current in ((1, 80, 20), (1, 80, 19), (0, 200, 30), (1, 110, -20)):
			config = frappe._dict(applies=applies, action_for_annual="Warn", action_for_monthly="Warn")
			self.validator.handle_individual_doctype_action(
				self.key(), config, "BUDGET-RAW", 100, existing, current, 100
			)
		self.messages.assert_not_called()
		self.throw.assert_not_called()

	def test_purchase_order_material_request_and_actual_expense_dispatch(self):
		for method, amount_field, current_field in (
			("handle_purchase_order_overlimit", "ordered_amount", "current_ordered_amount"),
			("handle_material_request_overlimit", "requested_amount", "current_requested_amount"),
			("handle_actual_expense_overlimit", "actual_expense", "current_actual_exp_amount"),
		):
			with self.subTest(method=method):
				vmap = self.vmap(budget_amount=10, accumulated_monthly_budget=100)
				vmap[amount_field], vmap[current_field] = 20, 5
				before = deepcopy(vmap)
				self.messages.reset_mock()
				getattr(self.validator, method)(self.key(), vmap)
				self.assertEqual(self.messages.call_count, 1)
				self.assertIn("<strong>Centro di costo</strong>", self.messages.call_args.args[0])
				self.assertIn("<strong>15.00 EUR</strong>", self.messages.call_args.args[0])
				self.assertEqual(vmap, before)

	def test_cumulative_annual_message_translates_components_and_dimension(self):
		components = "'Ordine di acquisto'+'Richiesta di materiale'+'Costo effettivo'+'Questo documento'"
		for field, label in self.dimensions():
			with self.subTest(field=field):
				vmap = self.vmap()
				before = deepcopy(vmap)
				self.messages.reset_mock()
				self.validator.handle_cumulative_overlimit_for_annual(self.key(field), vmap)
				self.messages.assert_called_once_with(
					f"Il budget annuale per il conto <strong>Account</strong>, relativo a <strong>{label}</strong> <strong>Project</strong>, è <strong>100.00 EUR</strong>. Il totale ({components}) lo supererà di <strong>20.00 EUR</strong>.",
					"Budget superato",
				)
				self.assertEqual(vmap, before)

	def test_cumulative_monthly_message_translates_components_and_dimension(self):
		components = "'Ordine di acquisto'+'Richiesta di materiale'+'Costo effettivo'+'Questo documento'"
		for field, label in self.dimensions():
			with self.subTest(field=field):
				self.messages.reset_mock()
				self.validator.handle_cumulative_overlimit_for_monthly(self.key(field), self.vmap())
				self.messages.assert_called_once_with(
					f"Il budget mensile cumulato per il conto <strong>Account</strong>, relativo a <strong>{label}</strong> <strong>Project</strong>, è <strong>80.00 EUR</strong>. Il totale ({components}) lo supererà di <strong>40.00 EUR</strong>.",
					"Budget superato",
				)

	def test_applicability_all_flags_amounts_and_current_document_combinations(self):
		fields = (
			("applicable_on_purchase_order", "ordered_amount", "Ordine di acquisto"),
			("applicable_on_material_request", "requested_amount", "Richiesta di materiale"),
			("applicable_on_booking_actual_expenses", "actual_expense", "Costo effettivo"),
		)
		for bits in itertools.product((0, 1), repeat=7):
			vmap, expected = self.vmap(), []
			for index, (flag, amount, label) in enumerate(fields):
				vmap.budget_doc[flag], vmap[amount] = bits[index], bits[index + 3]
				if bits[index] and bits[index + 3]:
					expected.append(label)
			if bits[6]:
				expected.append("Questo documento")
			with self.subTest(bits=bits):
				before = deepcopy(vmap)
				self.assertEqual(
					self.validator.budget_applicable_for(vmap, bits[6]), "+".join(f"'{x}'" for x in expected)
				)
				self.assertEqual(vmap, before)

	def test_cumulative_threshold_and_configuration_controls(self):
		for total in (99, 100):
			vmap = self.vmap(
				ordered_amount=total,
				requested_amount=0,
				actual_expense=0,
				current_ordered_amount=0,
				current_requested_amount=0,
				current_actual_exp_amount=0,
				accumulated_monthly_budget=100,
			)
			self.validator.handle_cumulative_overlimit(self.key(), vmap)
		vmap = self.vmap()
		vmap.budget_doc.applicable_on_cumulative_expense = 0
		self.validator.handle_cumulative_overlimit(self.key(), vmap)
		self.messages.assert_not_called()
		self.validator.handle_cumulative_overlimit(self.key(), self.vmap())
		self.assertEqual(self.messages.call_count, 2)

	def test_real_action_dispatch_and_role_override(self):
		self.validator.execute_action("Warn", "MESSAGE-RAW")
		self.messages.assert_called_once_with("MESSAGE-RAW", "Budget superato")
		self.messages.reset_mock()
		self.validator.execute_action("Ignore", "MESSAGE-RAW")
		self.messages.assert_not_called()
		with self.assertRaises(budget.BudgetError):
			self.validator.execute_action("Stop", "MESSAGE-RAW")
		self.throw.assert_called_once_with("MESSAGE-RAW", budget.BudgetError, title="Budget superato")
		self.validator.exception_approver_role = "ROLE-RAW"
		with patch("frappe.get_roles", return_value=["ROLE-RAW"]) as roles:
			self.validator.execute_action("Stop", "OVERRIDE-RAW")
		roles.assert_called_once_with("translator@example.com")
		self.messages.assert_called_once_with("OVERRIDE-RAW", "Budget superato")

	def assert_legacy_links(self, message, field):
		self.assertIn("<hr> Costi totali registrati tramite - <ul>", message)
		self.assertIn("Costi effettivi", message)
		self.assertIn("Richieste di materiale", message)
		self.assertIn("Ordini da fatturare", message)
		for raw in ("Project", "Account", "Company", "ITEM-RAW", field, "2026-01-01", "2026-12-31"):
			self.assertIn(raw, message)
		anchors = re.findall(r"<a href='([^']*)'>([^<]*)</a>", message)
		self.assertEqual(len(anchors), 3)
		expected = (
			("/desk/query-report/General%20Ledger", "Costi effettivi"),
			("/desk/material-request/view/report", "Richieste di materiale"),
			("/desk/purchase-order/view/report", "Ordini da fatturare"),
		)
		for index, ((href, label), (route, expected_label)) in enumerate(zip(anchors, expected, strict=True)):
			url = urlsplit(href)
			self.assertEqual(
				(url.scheme, url.netloc, url.path, label), ("https", "example.invalid", route, expected_label)
			)
			filters = {key: values[0] for key, values in parse_qs(url.query).items()}
			common = {field: "Project", "account": "Account", "company": "Company"}
			if index == 0:
				self.assertEqual(
					filters,
					common | {"from_date": "2026-01-01", "to_date": "2026-12-31", "is_cancelled": "0"},
				)
			else:
				date_field, percentage, status = (
					("schedule_date", "per_ordered", "Stopped")
					if index == 1
					else ("transaction_date", "per_billed", "Closed")
				)
				for key in ("status", date_field, percentage):
					filters[key] = json.loads(filters[key])
				additional = {"material_request_type": "Purchase"} if index == 1 else {}
				self.assertEqual(
					filters,
					common
					| {
						"status": ["!=", status],
						"docstatus": "1",
						date_field: ["between", ["2026-01-01", "2026-12-31"]],
						"item_code": "ITEM-RAW",
						percentage: ["<", "100"],
					}
					| additional,
				)

	def test_legacy_already_exceeded_annual_and_monthly(self):
		self.actual = 120
		for field, label in self.dimensions():
			for period, translated in (("Annual", "Annuale"), ("Accumulated Monthly", "Cumulativo mensile")):
				with self.subTest(field=field, period=period):
					params = self.params(field)
					self.messages.reset_mock()
					budget.compare_expense_with_budget(params, 100, frappe._(period), "Warn", "Project", 1)
					message = self.messages.call_args.args[0]
					self.assertEqual(
						message.split("<hr>")[0],
						f"Il budget {translated} per il conto <strong>Account</strong>, relativo a {label} <strong>Project</strong>, è <strong>100.00 EUR</strong>. È già stato superato di <strong>20.00 EUR</strong>.",
					)
					self.assert_legacy_links(message, field)
					self.assertEqual(
						(params.company, params.account, params[field]), ("Company", "Account", "Project")
					)

	def test_legacy_projected_exceeded_annual_and_monthly(self):
		for field, label in self.dimensions():
			for period, translated in (("Annual", "Annuale"), ("Accumulated Monthly", "Cumulativo mensile")):
				with self.subTest(field=field, period=period):
					self.messages.reset_mock()
					budget.compare_expense_with_budget(
						self.params(field), 100, frappe._(period), "Warn", "Project", 60
					)
					message = self.messages.call_args.args[0]
					self.assertEqual(
						message.split("<hr>")[0],
						f"Il budget {translated} per il conto <strong>Account</strong>, relativo a {label} <strong>Project</strong>, è <strong>100.00 EUR</strong>. Verrà superato di <strong>10.00 EUR</strong>.",
					)
					self.assert_legacy_links(message, field)

	def test_legacy_equal_and_below_threshold_controls(self):
		for actual, amount in ((50, 50), (50, 49), (110, -20)):
			self.actual = actual
			budget.compare_expense_with_budget(self.params(), 100, "Annual", "Stop", "Project", amount)
		self.messages.assert_not_called()
		self.throw.assert_not_called()
		self.assertEqual(len(self.queries), 3)

	def test_legacy_real_requested_ordered_queries_and_raw_filters(self):
		for doctype, expected in (("Material Request", 100), ("Purchase Order", 80)):
			params = self.params("funding_axis", doctype=doctype)
			self.messages.reset_mock()
			self.queries.clear()
			budget.compare_expense_with_budget(params, 60, frappe._("Annual"), "Warn", "Project")
			self.assertEqual(
				(params.actual_expense, params.requested_amount, params.ordered_amount), (50, 20, 30)
			)
			self.assertIn(f"<strong>{expected - 60:.2f} EUR</strong>", self.messages.call_args.args[0])
			self.assertEqual(len(self.queries), 3)
			for sql, values, _ in self.queries:
				self.assertIn("funding_axis", sql)
				self.assertIn("Project", values.values())
				self.assertIn("Account", values.values())
				self.assertIn("2026-01-01", values.values())
				self.assertIn("2026-12-31", values.values())
			self.assertIn("tabGL Entry", self.queries[0][0])
			self.assertIn("tabMaterial Request Item", self.queries[1][0])
			self.assertIn("tabPurchase Order Item", self.queries[2][0])

	def test_legacy_actual_tree_query_preserves_canonical_doctype_and_bounds(self):
		params = self.params(is_tree=True)
		self.db.get_value.return_value = frappe._dict(lft=5, rgt=10)
		self.assertEqual(budget.get_actual_expense(params), 50)
		self.db.get_value.assert_called_once_with("Cost Center", "Project", ["lft", "rgt"], as_dict=1)
		self.assertEqual((params.lft, params.rgt, params.cost_center), (5, 10, "Project"))
		query, _, _ = self.queries[0]
		self.assertIn("tabCost Center", query)
		self.assertIn("cost_center", query)
		self.assertIn(">=5", query)
		self.assertIn("<=10", query)
		self.messages.assert_not_called()

	def test_legacy_monthly_records_real_allocation_and_action_selection(self):
		params = self.params(doctype="Purchase Order")
		record = frappe._dict(
			name="BUDGET-RAW",
			budget_amount=100,
			budget_against="Project",
			from_fiscal_year="FY-RAW",
			to_fiscal_year="FY-RAW",
			budget_start_date="2026-01-01",
			budget_end_date="2026-12-31",
			for_material_request=1,
			for_purchase_order=1,
			action_if_annual_budget_exceeded="Stop",
			action_if_accumulated_monthly_budget_exceeded="Stop",
			action_if_annual_budget_exceeded_on_po="Warn",
			action_if_accumulated_monthly_budget_exceeded_on_po="Warn",
		)
		before = deepcopy(record)
		budget.validate_budget_records(params, [record], 60)
		self.assertEqual(self.messages.call_count, 2)
		self.assertIn("Il budget Annuale", self.messages.call_args_list[0].args[0])
		self.assertIn("Il budget Cumulativo mensile", self.messages.call_args_list[1].args[0])
		self.assertIn("<strong>30.00 EUR</strong>", self.messages.call_args_list[1].args[0])
		self.assertEqual(str(params.month_end_date), "2026-04-30")
		self.assertEqual(record, before)
		allocation_query = next(row for row in self.queries if "tabBudget Distribution" in row[0])
		self.assertIn("BUDGET-RAW", allocation_query[1].values())
		self.assertIn("2026-04-15", allocation_query[0])
		self.assertIn("month_end_date", params)

	def test_legacy_stop_and_exception_role_keep_behavior(self):
		with self.assertRaises(budget.BudgetError):
			budget.compare_expense_with_budget(self.params(), 60, frappe._("Annual"), "Stop", "Project", 20)
		self.assertEqual(self.throw.call_args.kwargs, {"title": "Budget superato"})
		with patch.dict(frappe.flags, exception_approver_role="ROLE-RAW"), patch(
			"frappe.get_roles", return_value=["ROLE-RAW"]
		) as roles:
			budget.compare_expense_with_budget(self.params(), 60, frappe._("Annual"), "Stop", "Project", 20)
		roles.assert_called_once_with("translator@example.com")
		self.messages.assert_called_once()
		self.assertEqual(self.messages.call_args.kwargs, {"indicator": "orange", "title": "Budget superato"})
