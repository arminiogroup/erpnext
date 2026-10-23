# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils.data import comma_and

from erpnext.accounts.doctype.unreconcile_payment.unreconcile_payment import (
	UNRECONCILABLE_DOCTYPES,
	UnreconcilePayment,
	create_unreconcile_doc_for_selection,
)

FRAME = "Only {0} are supported"
TARGETS = {
	FRAME: "Sono supportati solo questi tipi di documento: {0}.",
	"Payment Entry": "Registrazione di pagamento",
	"Journal Entry": "Registrazione contabile",
}
RAW_TYPES = ("Payment Entry", "Journal Entry")


class NewDocumentBoundary(Exception):
	pass


class TestUnreconcilePaymentTypeLabels(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe.local, "request", SimpleNamespace(method="POST"), create=True))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(frappe, "db", SimpleNamespace()))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TARGETS if lang == "it" else {},
			)
		)
		self.enterContext(
			patch(
				"frappe.get_hooks",
				side_effect=lambda hook, *args, **kwargs: []
				if hook == "require_type_annotated_api_methods"
				else self.fail("Unexpected hook " + hook),
			)
		)
		self.permission_allowed = True
		self.events = []
		self.enterContext(patch("frappe.has_permission", side_effect=self.permission))
		self.enterContext(patch("frappe.new_doc", side_effect=self.new_doc))
		for boundary in ("get_doc", "get_all", "get_cached_doc", "sendmail", "enqueue"):
			self.enterContext(
				patch("frappe." + boundary, side_effect=AssertionError("Unexpected I/O: " + boundary))
			)

	def permission(self, *args, **kwargs):
		self.events.append(("permission", args, kwargs))
		return self.permission_allowed

	def new_doc(self, *args, **kwargs):
		self.events.append(("new_doc_boundary", args, kwargs))
		raise NewDocumentBoundary

	def expected(self):
		return TARGETS[FRAME].format(comma_and([TARGETS[value] for value in RAW_TYPES]))

	def test_controller_rejects_unknown_none_and_blank_with_translated_choices(self):
		for doctype in ("Sales Invoice", "Custom Type", None, ""):
			with self.subTest(doctype=doctype):
				doc = UnreconcilePayment.__new__(UnreconcilePayment)
				doc.__dict__.update(
					doctype="Unreconcile Payment", voucher_type=doctype, voucher_no="Payment Entry"
				)
				before = deepcopy(doc.__dict__)
				with self.assertRaises(frappe.ValidationError) as caught:
					doc.validate()
				self.assertEqual(str(caught.exception), self.expected())
				self.assertEqual(doc.__dict__, {**before, "supported_types": list(RAW_TYPES)})
				self.assertEqual(self.events, [])

	def test_actual_request_adapter_rejects_list_and_json_before_permissions(self):
		for doctype in ("Sales Invoice", "Custom Type", None, ""):
			for encoded in (False, True):
				with self.subTest(doctype=doctype, encoded=encoded):
					selection = [{"voucher_type": doctype, "voucher_no": 707, "company": "Payment Entry"}]
					before = deepcopy(selection)
					with self.assertRaises(frappe.ValidationError) as caught:
						create_unreconcile_doc_for_selection(json.dumps(selection) if encoded else selection)
					self.assertEqual(str(caught.exception), self.expected())
					self.assertEqual(selection, before)
					self.assertEqual(self.events, [])

	def test_supported_controller_types_stay_raw(self):
		for doctype in RAW_TYPES:
			doc = UnreconcilePayment.__new__(UnreconcilePayment)
			doc.__dict__.update(voucher_type=doctype)
			doc.validate()
			self.assertEqual(doc.supported_types, list(RAW_TYPES))
			self.assertEqual(doc.voucher_type, doctype)
		self.assertEqual(UNRECONCILABLE_DOCTYPES, RAW_TYPES)

	def test_valid_selection_uses_raw_permission_values_and_stops_before_construction(self):
		for doctype in RAW_TYPES:
			for encoded in (False, True):
				self.events.clear()
				selection = [{"voucher_type": doctype, "voucher_no": 707, "company": "Payment Entry"}]
				with self.assertRaises(NewDocumentBoundary):
					create_unreconcile_doc_for_selection(json.dumps(selection) if encoded else selection)
				self.assertEqual(
					self.events,
					[
						("permission", (doctype, "write"), {"doc": "707"}),
						("new_doc_boundary", ("Unreconcile Payment",), {}),
					],
				)
				self.assertEqual(selection[0]["voucher_no"], 707 if encoded else "707")

	def test_all_selection_preflight_keeps_denial_and_conversion_order(self):
		for invalid in ("Sales Invoice", None):
			self.events.clear()
			selection = [
				{"voucher_type": RAW_TYPES[0], "voucher_no": 707},
				{"voucher_type": invalid, "voucher_no": 909},
			]
			with self.assertRaises(frappe.ValidationError) as caught:
				create_unreconcile_doc_for_selection(selection)
			self.assertEqual(str(caught.exception), self.expected())
			self.assertEqual(self.events, [("permission", (RAW_TYPES[0], "write"), {"doc": "707"})])
			self.assertEqual(selection[0]["voucher_no"], "707")
			self.assertEqual(selection[1]["voucher_no"], 909)
		self.permission_allowed = False
		for doctype in RAW_TYPES:
			self.events.clear()
			with self.assertRaises(frappe.PermissionError):
				create_unreconcile_doc_for_selection([{"voucher_type": doctype, "voucher_no": 707}])
			self.assertEqual(self.events, [("permission", (doctype, "write"), {"doc": "707"})])
		self.events.clear()
		with self.assertRaises(frappe.PermissionError):
			create_unreconcile_doc_for_selection([{"voucher_type": RAW_TYPES[0], "voucher_no": None}])
		self.assertEqual(self.events, [])

	def test_empty_and_invalid_request_types_preserve_contract(self):
		for value in (None, [], "", "[]"):
			self.assertIsNone(create_unreconcile_doc_for_selection(value))
		for value in (0, {}):
			with self.assertRaises(frappe.FrappeTypeError):
				create_unreconcile_doc_for_selection(value)
		self.assertEqual(self.events, [])

	def test_english_choices_keep_original_order_and_helper_punctuation(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			create_unreconcile_doc_for_selection([{"voucher_type": "Custom Type"}])
		self.assertEqual(str(caught.exception), FRAME.format(comma_and(list(RAW_TYPES))))
