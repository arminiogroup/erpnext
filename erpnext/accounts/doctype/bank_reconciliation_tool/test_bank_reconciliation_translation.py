# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

import erpnext.hooks
from erpnext.accounts.doctype.bank_reconciliation_tool import bank_reconciliation_tool as tool

CLEARANCE = "{0} is not a voucher this tool can clear"
RECONCILIATION = "{0} cannot be reconciled against a bank transaction"
TARGETS = {
	CLEARANCE: "Questo strumento non può gestire la data di regolamento dei documenti di tipo {0}.",
	RECONCILIATION: "I documenti di tipo {0} non possono essere riconciliati con una transazione bancaria.",
	"Sales Order": "Ordine di vendita",
	"Customer": "Cliente",
	"Payment Entry": "Registrazione di pagamento",
	"Journal Entry": "Registrazione contabile",
	"Purchase Invoice": "Fattura di acquisto",
	"Sales Invoice": "Fattura di vendita",
	"Bank Transaction": "Transazione bancaria",
	"Custom Voucher": "Documento personalizzato",
	"Not permitted": "Operazione non consentita",
}


class DocumentReadBoundary(Exception):
	pass


class TestBankReconciliationTranslation(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe.local, "request", SimpleNamespace(method="POST"), create=True))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TARGETS if lang == "it" else {},
			)
		)
		self.events = []
		self.denied = set()
		self.hook_doctypes = list(erpnext.hooks.bank_reconciliation_doctypes)
		self.enterContext(patch("frappe.has_permission", side_effect=self.permission))
		self.enterContext(patch("frappe.get_hooks", side_effect=self.hooks))
		self.enterContext(patch("frappe.get_doc", side_effect=self.document_read))
		self.enterContext(patch.object(frappe, "db", SimpleNamespace(set_value=self.deliver_clearance)))

	def permission(self, doctype, ptype=None, doc=None, throw=False):
		self.events.append(("permission", doctype, ptype, doc, throw))
		if (doctype, doc) in self.denied or (doctype, None) in self.denied:
			if throw:
				frappe.throw_permission_error()
			return False
		return True

	def hooks(self, key, *args, **kwargs):
		if key == "require_type_annotated_api_methods":
			return []
		self.assertEqual((key, args, kwargs), ("bank_reconciliation_doctypes", (), {}))
		self.events.append(("hooks", key))
		return self.hook_doctypes[:]

	def document_read(self, *args, **kwargs):
		self.events.append(("document_read", args, kwargs))
		raise DocumentReadBoundary

	def deliver_clearance(self, *args, **kwargs):
		self.events.append(("clearance_delivery", args, kwargs))

	def reset(self):
		self.events.clear()
		frappe.local.message_log.clear()

	def test_unsupported_clearance_type_translates_only_display_label(self):
		for doctype in ("Sales Order", "Customer", "Unmapped Voucher", ""):
			with self.subTest(doctype=doctype):
				self.reset()
				with self.assertRaises(frappe.ValidationError) as caught:
					tool.update_clearance_date(doctype, "Sales Order", "Customer", "2026-01-03")
				self.assertEqual(
					str(caught.exception), TARGETS[CLEARANCE].format(TARGETS.get(doctype, doctype))
				)
				self.assertEqual(
					self.events,
					[
						("permission", "Bank Clearance", "write", None, True),
						("hooks", "bank_reconciliation_doctypes"),
					],
				)

	def test_request_adapter_rejects_nonstring_clearance_type_before_body(self):
		for doctype in (None, 0, {"doctype": "Payment Entry"}):
			with self.subTest(doctype=doctype):
				self.reset()
				with self.assertRaises(frappe.FrappeTypeError):
					tool.update_clearance_date(doctype, "RAW-REF", "RAW-ACCOUNT", None)
				self.assertEqual(self.events, [])

	def test_direct_python_clearance_unknown_types_keep_validation_fallback(self):
		with patch.object(frappe.local, "request", None):
			for doctype in (None, 0, {"doctype": "Payment Entry"}):
				with self.subTest(doctype=doctype):
					self.reset()
					with self.assertRaises(frappe.ValidationError) as caught:
						tool.update_clearance_date(doctype, "RAW-REF", "RAW-ACCOUNT", None)
					self.assertEqual(str(caught.exception), TARGETS[CLEARANCE].format(doctype))
					self.assertEqual(
						self.events,
						[
							("permission", "Bank Clearance", "write", None, True),
							("hooks", "bank_reconciliation_doctypes"),
						],
					)

	def test_clearance_permissions_precede_diagnostics_and_memory_delivery(self):
		self.denied.add(("Bank Clearance", None))
		with self.assertRaises(frappe.PermissionError):
			tool.update_clearance_date("Sales Order", "RAW-REF", "RAW-ACCOUNT", None)
		self.assertEqual(self.events, [("permission", "Bank Clearance", "write", None, True)])
		self.denied.clear()
		self.denied.add(("Payment Entry", "707"))
		self.reset()
		with self.assertRaises(frappe.PermissionError):
			tool.update_clearance_date("Payment Entry", "707", "RAW-ACCOUNT", None)
		self.assertEqual(
			self.events,
			[
				("permission", "Bank Clearance", "write", None, True),
				("hooks", "bank_reconciliation_doctypes"),
				("permission", "Payment Entry", "write", "707", False),
			],
		)
		self.assertTrue(frappe.flags.disable_traceback)

	def test_clearance_memory_delivery_keeps_raw_types_names_filters_and_dates(self):
		self.hook_doctypes.append("Custom Voucher")
		for doctype in self.hook_doctypes:
			for clearance_date in (None, "", "2026-01-03"):
				with self.subTest(doctype=doctype, clearance_date=clearance_date):
					self.reset()
					self.assertIsNone(
						tool.update_clearance_date(doctype, "Sales Order", "Customer", clearance_date)
					)
					if doctype == "Sales Invoice":
						args = (
							"Sales Invoice Payment",
							{"parent": "Sales Order", "account": "Customer", "amount": [">", 0]},
							"clearance_date",
							clearance_date or None,
						)
					else:
						args = (doctype, "Sales Order", "clearance_date", clearance_date or None)
					self.assertEqual(
						self.events,
						[
							("permission", "Bank Clearance", "write", None, True),
							("hooks", "bank_reconciliation_doctypes"),
							("permission", doctype, "write", "Sales Order", False),
							("clearance_delivery", args, {}),
						],
					)

	def test_unsupported_reconciliation_types_keep_raw_payload_and_permission_order(self):
		for doctype in (
			"Sales Order",
			"Customer",
			"Unmapped Voucher",
			"",
			None,
			0,
			{"doctype": "Payment Entry"},
		):
			for encode in (False, True):
				with self.subTest(doctype=doctype, json=encode):
					self.reset()
					vouchers = [{"payment_doctype": doctype, "payment_name": 707, "amount": 12.5}]
					before = deepcopy(vouchers)
					with self.assertRaises(frappe.ValidationError) as caught:
						tool.reconcile_vouchers(901, json.dumps(vouchers) if encode else vouchers)
					self.assertEqual(
						str(caught.exception),
						TARGETS[RECONCILIATION].format(
							TARGETS.get(doctype, doctype) if isinstance(doctype, str) else doctype
						),
					)
					self.assertEqual(vouchers, before)
					self.assertEqual(
						self.events,
						[
							("permission", "Bank Transaction", "write", "901", False),
							("hooks", "bank_reconciliation_doctypes"),
						],
					)

	def test_reconciliation_permission_denials_stop_before_reads(self):
		vouchers = [{"payment_doctype": "Sales Order", "payment_name": "RAW-REF"}]
		self.denied.add(("Bank Transaction", "RAW-BT"))
		with self.assertRaises(frappe.PermissionError):
			tool.reconcile_vouchers("RAW-BT", vouchers)
		self.assertEqual(self.events, [("permission", "Bank Transaction", "write", "RAW-BT", False)])
		self.assertTrue(frappe.flags.disable_traceback)
		self.reset()
		with self.assertRaises(frappe.PermissionError):
			tool.reconcile_vouchers("", vouchers)
		self.assertEqual(self.events, [])
		self.denied.clear()
		self.denied.add(("Payment Entry", "707"))
		vouchers = [{"payment_doctype": "Payment Entry", "payment_name": 707, "amount": 12.5}]
		with self.assertRaises(frappe.PermissionError):
			tool.reconcile_vouchers("RAW-BT", vouchers)
		self.assertEqual(
			vouchers[0], {"payment_doctype": "Payment Entry", "payment_name": "707", "amount": 12.5}
		)
		self.assertEqual(
			self.events,
			[
				("permission", "Bank Transaction", "write", "RAW-BT", False),
				("hooks", "bank_reconciliation_doctypes"),
				("permission", "Payment Entry", "write", "707", False),
			],
		)

	def test_allowed_reconciliation_stops_at_actual_worker_document_read(self):
		self.hook_doctypes.append("Custom Voucher")
		for doctype in (*self.hook_doctypes, "Bank Transaction"):
			with self.subTest(doctype=doctype):
				self.reset()
				vouchers = [{"payment_doctype": doctype, "payment_name": 707, "amount": 12.5}]
				with self.assertRaises(DocumentReadBoundary):
					tool.reconcile_vouchers(901, vouchers, is_new_voucher=True)
				self.assertEqual(
					vouchers, [{"payment_doctype": doctype, "payment_name": "707", "amount": 12.5}]
				)
				self.assertEqual(
					self.events,
					[
						("permission", "Bank Transaction", "write", "901", False),
						("hooks", "bank_reconciliation_doctypes"),
						("permission", doctype, "write", "707", False),
						("document_read", ("Bank Transaction", "901"), {}),
					],
				)

	def test_empty_voucher_names_refuse_before_voucher_permissions_or_document_reads(self):
		with self.assertRaises(frappe.PermissionError):
			tool.update_clearance_date("Payment Entry", "", "RAW-ACCOUNT", None)
		self.assertEqual(
			self.events,
			[
				("permission", "Bank Clearance", "write", None, True),
				("hooks", "bank_reconciliation_doctypes"),
			],
		)
		for name in (None, ""):
			with self.subTest(name=name):
				self.reset()
				vouchers = [{"payment_doctype": "Payment Entry", "payment_name": name, "amount": 12.5}]
				with self.assertRaises(frappe.PermissionError):
					tool.reconcile_vouchers("RAW-BT", vouchers)
				self.assertEqual(vouchers[0]["payment_name"], "")
				self.assertEqual(
					self.events,
					[
						("permission", "Bank Transaction", "write", "RAW-BT", False),
						("hooks", "bank_reconciliation_doctypes"),
					],
				)

	def test_bank_transaction_is_reconciliation_only_by_default(self):
		self.assertNotIn("Bank Transaction", self.hook_doctypes)
		self.assertEqual(tool.get_reconcilable_doctypes(), [*self.hook_doctypes, "Bank Transaction"])
		with self.assertRaises(frappe.ValidationError) as caught:
			tool.update_clearance_date("Bank Transaction", "RAW-BT", "RAW-ACCOUNT", None)
		self.assertEqual(str(caught.exception), TARGETS[CLEARANCE].format(TARGETS["Bank Transaction"]))

	def test_english_diagnostics_keep_original_frames(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			tool.reconcile_vouchers("RAW-BT", [{"payment_doctype": "Sales Order", "payment_name": "RAW-REF"}])
		self.assertEqual(str(caught.exception), RECONCILIATION.format("Sales Order"))
