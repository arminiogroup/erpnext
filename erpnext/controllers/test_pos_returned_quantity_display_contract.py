# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.controllers.sales_and_purchase_return import get_invoice_item_returned_qty


class TestPosReturnedQuantityDisplayContract(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "en"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch("frappe.translate.get_all_translations", return_value={}))

	def execute(self, doctype, quantity, *, allowed=True, is_return=0, docstatus=1):
		events = []
		row = frappe._dict(qty=quantity, stock_qty=quantity)

		def permission(kind, **kwargs):
			events.append(("permission", kind, kwargs))
			self.assertEqual(kwargs, {"doc": "INV-RAW", "throw": True})
			if not allowed:
				raise frappe.PermissionError("MEMORY-DENIAL")
			return True

		def value(kind, name, fields):
			events.append(("get_value", kind, name, fields))
			self.assertEqual((kind, name, fields), (doctype, "INV-RAW", ["is_return", "docstatus"]))
			return is_return, docstatus

		def aggregate(kind, **kwargs):
			events.append(("get_all", kind, kwargs))
			child = doctype + " Item"
			self.assertEqual(kind, doctype)
			self.assertEqual(
				kwargs["fields"],
				[
					{"SUM": [{"ABS": f"`tab{child}`.qty"}], "as": "qty"},
					{"SUM": [{"ABS": f"`tab{child}`.stock_qty"}], "as": "stock_qty"},
				],
			)
			self.assertEqual(
				kwargs["filters"],
				[
					[doctype, "return_against", "=", "INV-RAW"],
					[doctype, "customer", "=", "CUSTOMER-RAW"],
					[doctype, "docstatus", "=", 1],
					[doctype, "is_return", "=", 1],
					[child, frappe.scrub(child), "=", "ROW-RAW"],
				],
			)
			return [row]

		with patch.object(frappe, "has_permission", side_effect=permission), patch.object(
			frappe, "db", SimpleNamespace(get_value=value)
		), patch.object(frappe, "get_all", side_effect=aggregate):
			try:
				result = get_invoice_item_returned_qty(doctype, "INV-RAW", "CUSTOMER-RAW", "ROW-RAW")
			except frappe.PermissionError as exc:
				return exc, events, row
		return result, events, row

	def test_actual_guard_and_aggregate_query_preserve_raw_numeric_result_and_read_order(self):
		for doctype in ("POS Invoice", "Sales Invoice"):
			for quantity in (0, 1, 2, Decimal("1.5")):
				with self.subTest(doctype=doctype, quantity=quantity):
					result, events, row = self.execute(doctype, quantity)
					self.assertIs(result, row)
					self.assertEqual(result.qty, quantity)
					self.assertEqual([event[0] for event in events], ["permission", "get_value", "get_all"])

	def test_permission_and_type_denials_precede_all_invoice_and_return_reads(self):
		result, events, _ = self.execute("POS Invoice", 2, allowed=False)
		self.assertIsInstance(result, frappe.PermissionError)
		self.assertEqual([event[0] for event in events], ["permission"])
		result, events, _ = self.execute("Purchase Receipt", 2)
		self.assertIsInstance(result, frappe.PermissionError)
		self.assertEqual(events, [])

	def test_return_draft_and_cancelled_invoice_controls_do_not_query_return_rows(self):
		for is_return, docstatus in ((1, 1), (0, 0), (0, 2)):
			with self.subTest(is_return=is_return, docstatus=docstatus):
				result, events, _ = self.execute("POS Invoice", 2, is_return=is_return, docstatus=docstatus)
				self.assertIsNone(result)
				self.assertEqual([event[0] for event in events], ["permission", "get_value"])
