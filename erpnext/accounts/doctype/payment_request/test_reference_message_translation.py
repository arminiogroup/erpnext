# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import hashlib
import itertools
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.query_builder.builder import MariaDB
from frappe.tests import UnitTestCase
from frappe.utils import flt

from erpnext.accounts.doctype.payment_request import payment_request

UNSUPPORTED = "Payment Requests cannot be created against: {0}"
LIMIT = "Total Payment Request amount cannot be greater than {0} amount"
TARGETS = {
	UNSUPPORTED: "Non è possibile creare richieste di pagamento per questo tipo di documento: {0}.",
	LIMIT: "L’importo complessivo delle richieste di pagamento non può superare l’importo disponibile per il documento di riferimento ({0}).",
	"ToDo": "Attività da svolgere",
	"Project": "Progetto",
	"Sales Order": "Ordine di vendita",
	"Purchase Order": "Ordine d’acquisto",
	"Sales Invoice": "Fattura di vendita",
	"Purchase Invoice": "Fattura di acquisto",
	"POS Invoice": "Fattura POS",
	"Fees": "Rette",
	"{0} cannot be zero": "{0} non può essere zero",
	"Amount": "Importo",
	"Invalid Amount": "Importo non valido",
	"Payment Entry is already created": "Il pagamento è già stato registrato",
}


def configured_keys():
	paths = [
		Path(payment_request.__file__),
		Path(frappe.get_app_path("erpnext", "accounts/doctype/payment_request/payment_request.json")),
		Path(frappe.get_app_path("frappe", "desk/doctype/todo/todo.json")),
		Path(frappe.get_app_path("erpnext", "projects/doctype/project/project.json")),
		Path(
			frappe.get_app_path(
				"erpnext", "accounts/doctype/journal_entry_account/journal_entry_account.json"
			)
		),
	]
	for doctype in payment_request.ALLOWED_DOCTYPES_FOR_PAYMENT_REQUEST:
		if doctype == "Fees":
			continue
		slug = frappe.scrub(doctype)
		module = (
			"selling" if doctype == "Sales Order" else "buying" if doctype == "Purchase Order" else "accounts"
		)
		paths.append(Path(frappe.get_app_path("erpnext", f"{module}/doctype/{slug}/{slug}.json")))

	keys = set()
	for path in paths:
		app = "frappe" if "/apps/frappe/" in str(path) else "erpnext"
		root = Path(frappe.get_app_path(app)).parent
		relative = str(path.relative_to(root))
		method = next(
			method
			for pattern, method in get_method_map(app) + get_method_map("frappe")
			if pathmatch(pattern, relative)
		)
		keys.update(
			message
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
			if not context and isinstance(message, str)
		)
	return keys


def request_meta():
	path = Path(frappe.get_app_path("erpnext", "accounts/doctype/payment_request/payment_request.json"))
	fields = [
		frappe._dict(parent="Payment Request", **field) for field in json.loads(path.read_text())["fields"]
	]
	meta = Meta.__new__(Meta)
	meta.__dict__.update(
		doctype="DocType",
		name="Payment Request",
		fields=fields,
		_fields={field.fieldname: field for field in fields},
	)
	return meta


class ReadOnlyDatabase:
	db_type = "mariadb"

	def __init__(self):
		self.total = 0
		self.precision = "2"
		self.reads = []

	def get_default(self, key):
		self.reads.append(("get_default", key))
		if key == "currency_precision":
			return self.precision
		if key == "number_format":
			return "#,###.##"
		return None

	def sql(self, query, params, *args, **kwargs):
		assert query.startswith("SELECT SUM(") and "`tabPayment Request`" in query
		assert "`outstanding_amount`" in query and "`docstatus`=1" in query
		assert not any(word in query for word in ("INSERT ", "UPDATE ", "DELETE "))
		self.reads.append(("sql", query, params, args, kwargs))
		return [(self.total,)]


class PermissionBoundary(Exception):
	pass


class TestReferenceMessageTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		self.addCleanup(setattr, frappe.local, "message_log", getattr(frappe.local, "message_log", None))
		self.addCleanup(setattr, frappe.local, "db", getattr(frappe.local, "db", None))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		keys = configured_keys()
		self.assertLessEqual(set(TARGETS), keys)
		self.enterContext(patch("frappe.translate.get_all_translations", return_value=TARGETS))
		self.meta = request_meta()
		self.enterContext(patch("frappe.get_meta", return_value=self.meta))
		self.db = ReadOnlyDatabase()
		frappe.local.db = self.db
		self.enterContext(patch.object(frappe, "qb", MariaDB))
		self.rounding = "Banker's Rounding"
		self.enterContext(
			patch(
				"frappe.get_system_settings",
				side_effect=lambda key: self.rounding if key == "rounding_method" else None,
			)
		)

	def request(self, doctype="Sales Invoice", amount=81, **values):
		doc = payment_request.PaymentRequest.__new__(payment_request.PaymentRequest)
		doc.__dict__.update(
			doctype="Payment Request",
			name="PR-RAW",
			flags=frappe._dict(),
			reference_doctype=doctype,
			reference_name="Project",
			grand_total=amount,
			payment_account="Sales Invoice",
			payment_reference=[],
		)
		doc.__dict__.update(values)
		return doc

	def reference(self, doctype="Sales Invoice", foreign=False, **values):
		doc = frappe._dict(
			doctype=doctype,
			name="Project",
			currency="EUR",
			party_account_currency="USD" if foreign else "EUR",
			conversion_rate=2,
			grand_total=100,
			rounded_total=0,
			advance_paid=40 if foreign else 20,
			outstanding_amount=160 if foreign else 80,
			is_pos=0,
			payments=[
				frappe._dict(type="Phone", account="Sales Invoice", amount=80),
				frappe._dict(type="Cash", account="OTHER", amount=20),
			],
		)
		doc.update(values)
		return doc

	def business_state(self, doc):
		return deepcopy(
			{key: value for key, value in doc.__dict__.items() if key not in ("meta", "_precision")}
		)

	def assert_caches(self, doc, precision=2):
		if "meta" in doc.__dict__:
			self.assertIs(doc.__dict__["meta"], self.meta)
			self.assertEqual(doc.meta.name, "Payment Request")
		if "_precision" in doc.__dict__:
			self.assertEqual(doc._precision, {"main": {"grand_total": precision}})

	def assert_message(self, call, expected, doc=None):
		before = self.business_state(doc) if doc is not None else None
		frappe.local.message_log = []
		with self.assertRaises(frappe.ValidationError) as error:
			call()
		if doc is not None:
			self.assertEqual(before, self.business_state(doc))
			self.assert_caches(doc)
		self.assertEqual(str(error.exception), expected)

	def test_rejected_document_types_use_display_labels(self):
		for doctype in ("ToDo", "Project", "Custom Invoice Type"):
			with self.subTest(doctype=doctype):
				doc = self.request(doctype)
				self.assert_message(
					doc.validate_reference_document,
					TARGETS[UNSUPPORTED].format(frappe.bold(TARGETS.get(doctype, doctype))),
					doc,
				)

	def test_api_rejection_stays_before_permissions_and_reads(self):
		for doctype in ("ToDo", "Project", "Custom Invoice Type", None):
			with self.subTest(doctype=doctype), patch("frappe.has_permission") as permission, patch(
				"frappe.get_doc"
			) as read:
				self.assert_message(
					lambda: payment_request.make_payment_request(dt=doctype, dn="Project"),
					TARGETS[UNSUPPORTED].format(frappe.bold(TARGETS.get(doctype, doctype))),
				)
				permission.assert_not_called()
				read.assert_not_called()

	def test_allowed_types_keep_canonical_permission_boundary(self):
		for doctype in payment_request.ALLOWED_DOCTYPES_FOR_PAYMENT_REQUEST:
			with self.subTest(doctype=doctype):
				doc = self.request(doctype)
				before = deepcopy(doc.__dict__)
				doc.validate_reference_document()
				self.assertEqual(doc.__dict__, before)
				with patch("frappe.has_permission", side_effect=PermissionBoundary) as permission:
					with self.assertRaises(PermissionBoundary):
						payment_request.make_payment_request(dt=doctype, dn="Project")
					permission.assert_called_once_with("Payment Request", "create", throw=True)

	def test_missing_reference_and_invalid_name_guards_are_unchanged(self):
		for doctype, name in (("Sales Invoice", None), (None, "Project"), ("", "Project")):
			with self.subTest(doctype=doctype, name=name):
				self.assert_message(
					self.request(doctype, reference_name=name).validate_reference_document,
					"To create a Payment Request reference document is required",
				)
		with patch("frappe.has_permission") as permission:
			self.assert_message(
				lambda: payment_request.make_payment_request(dt="Sales Invoice", dn=123),
				"Invalid parameter. 'dn' should be of type str",
			)
			permission.assert_not_called()

	def test_amount_limit_uses_translated_type_and_actual_remaining_amount(self):
		for doctype, foreign in itertools.product(
			payment_request.ALLOWED_DOCTYPES_FOR_PAYMENT_REQUEST, (False, True)
		):
			with self.subTest(doctype=doctype, foreign=foreign):
				reference = self.reference(doctype, foreign)
				before = deepcopy(reference)
				available = payment_request.get_amount(reference, "Sales Invoice")
				self.assertEqual(available, 160 if doctype == "Fees" and foreign else 80)
				doc = self.request(doctype, available + 0.01)
				self.assert_message(
					lambda: doc.validate_payment_request_amount(reference),
					TARGETS[LIMIT].format(TARGETS[doctype]),
					doc,
				)
				self.assertEqual(reference, before)
				sql = [row for row in self.db.reads if row[0] == "sql"][-1]
				self.assertEqual(list(sql[2].values()), [doctype, "Project"])

	def test_actual_amount_helper_retains_order_and_phone_rules(self):
		phone = [
			frappe._dict(type="Phone", account="Sales Invoice", amount=30),
			frappe._dict(type="Phone", account="Sales Invoice", amount=20),
			frappe._dict(type="Phone", account="OTHER", amount=99),
		]
		cases = [
			(self.reference("Sales Order", rounded_total=101), "Sales Invoice", 81),
			(self.reference("Purchase Order", True, rounded_total=101), "Sales Invoice", 81),
			(self.reference("Sales Invoice", is_pos=1, payments=phone), "Sales Invoice", 50),
			(self.reference("POS Invoice", payments=phone), "Sales Invoice", 30),
			(self.reference("POS Invoice", payments=phone), "MISSING", 0),
			(self.reference("Sales Invoice", outstanding_amount=0), "Sales Invoice", 0),
			(self.reference("Sales Invoice", outstanding_amount=-20), "Sales Invoice", 0),
			(self.reference("Sales Order", advance_paid=100), "Sales Invoice", 0),
		]
		for reference, account, expected in cases:
			with self.subTest(doctype=reference.doctype, account=account, expected=expected):
				before = deepcopy(reference)
				self.assertEqual(payment_request.get_amount(reference, account), expected)
				self.assertEqual(reference, before)

	def test_actual_aggregate_preserves_raw_types_names_statuses_and_conversion(self):
		for foreign, amount in itertools.product((False, True), (None, 0, 20)):
			with self.subTest(foreign=foreign, amount=amount):
				self.db.total = amount
				reference = self.reference(foreign=foreign)
				before = deepcopy(reference)
				self.assertEqual(
					payment_request.get_existing_payment_request_amount(
						reference, ["Requested", "Initiated"]
					),
					(amount or 0) / (2 if foreign else 1),
				)
				self.assertEqual(reference, before)
				sql = [row for row in self.db.reads if row[0] == "sql"][-1]
				self.assertEqual(
					list(sql[2].values()), ["Sales Invoice", "Project", "Requested", "Initiated"]
				)

	def test_existing_amount_guards_and_bypasses_are_unchanged(self):
		for values, reference in (
			({"payment_reference": [frappe._dict(amount=900)], "grand_total": 0}, self.reference()),
			({"grand_total": 900}, self.reference("Sales Order", order_type="Shopping Cart")),
		):
			with self.subTest(values=values):
				doc = self.request(**values)
				before = deepcopy(doc.__dict__)
				self.db.reads = []
				doc.validate_payment_request_amount(reference)
				self.assertEqual(doc.__dict__, before)
				self.assertEqual(self.db.reads, [])
		doc = self.request(amount=0)
		self.assert_message(
			lambda: doc.validate_payment_request_amount(self.reference()), "Importo non può essere zero", doc
		)
		doc = self.request(amount=1)
		self.assert_message(
			lambda: doc.validate_payment_request_amount(self.reference(outstanding_amount=0)),
			"Il pagamento è già stato registrato",
			doc,
		)

	def test_whole_validate_preserves_read_permission_and_business_names(self):
		for amount in (80, 80.01):
			with self.subTest(amount=amount):
				reference = Document.__new__(Document)
				reference.__dict__.update(self.reference(), flags=frappe._dict(), company="Project")
				doc = self.request(
					amount=amount,
					mode_of_payment=None,
					company="Project",
					print_format=None,
					is_a_subscription=0,
				)
				before = self.business_state(doc)
				before_reference = deepcopy(reference.__dict__)
				with patch("frappe.get_doc", return_value=reference) as read, patch(
					"frappe.permissions.has_permission", return_value=True
				) as permission:
					if amount > 80:
						self.assert_message(
							doc.validate, TARGETS[LIMIT].format(TARGETS["Sales Invoice"]), doc
						)
					else:
						doc.validate()
					read.assert_called_once_with("Sales Invoice", "Project")
					permission.assert_called_once_with(
						"Sales Invoice", "read", reference, debug=False, user=None
					)
				self.assertEqual(self.business_state(doc), before)
				self.assertEqual(reference.__dict__, before_reference)
				self.assert_caches(doc)

	def test_amount_currency_decision_matrix(self):
		cases = []
		for doctype, foreign, pending, amount in itertools.product(
			payment_request.ALLOWED_DOCTYPES_FOR_PAYMENT_REQUEST,
			(False, True),
			(0, 20),
			(0.004, 59.994, 60.004, 60.006, 80, 80.006, -1),
		):
			reference = self.reference(doctype, foreign)
			available = payment_request.get_amount(reference, "Sales Invoice")
			self.db.total = pending * (2 if foreign else 1)
			doc = self.request(doctype, amount)
			before = self.business_state(doc)
			before_reference = deepcopy(reference)
			self.db.reads = []
			existing = payment_request.get_existing_payment_request_amount(reference)
			self.assertEqual(existing, pending)
			expected = flt(existing + flt(amount, 2), 2) > available
			try:
				doc.validate_payment_request_amount(reference)
				rejected = False
			except frappe.ValidationError:
				rejected = True
			self.assertEqual(rejected, expected)
			self.assertEqual(self.business_state(doc), before)
			self.assertEqual(reference, before_reference)
			self.assert_caches(doc)
			cases.append(
				(doctype, foreign, pending, amount, available, existing, rejected, deepcopy(self.db.reads))
			)
		self.parity_evidence = {
			"cases": cases,
			"sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
			"accepted": sum(not row[6] for row in cases),
			"rejected": sum(row[6] for row in cases),
		}

	def test_precision_and_rounding_controls_use_real_field_precision(self):
		cases = []
		for precision, field_precision, rounding, amount in itertools.product(
			(0, 2, 3),
			(None, "1", "3"),
			("Banker's Rounding", "Banker's Rounding (legacy)", "Commercial Rounding"),
			(0.004, 79.994, 80.004, 80.006, 80.0006),
		):
			self.db.precision = str(precision)
			self.meta.get_field("grand_total").precision = field_precision
			self.rounding = rounding
			self.db.total = 0
			reference = self.reference()
			doc = self.request(amount=amount)
			available = payment_request.get_amount(reference, "Sales Invoice")
			effective = int(field_precision) if field_precision else precision
			expected = flt(flt(amount, effective), precision) > available
			before = self.business_state(doc)
			try:
				doc.validate_payment_request_amount(reference)
				rejected = False
			except frappe.ValidationError:
				rejected = True
			self.assertEqual(rejected, expected, (precision, field_precision, rounding, amount))
			self.assertEqual(self.business_state(doc), before)
			self.assert_caches(doc, effective)
			cases.append(
				(
					precision,
					field_precision,
					rounding,
					amount,
					effective,
					available,
					rejected,
					deepcopy(self.db.reads),
				)
			)
			self.db.reads = []
		self.parity_evidence = {
			"precision_cases": cases,
			"sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
			"accepted": sum(not row[6] for row in cases),
			"rejected": sum(row[6] for row in cases),
		}
