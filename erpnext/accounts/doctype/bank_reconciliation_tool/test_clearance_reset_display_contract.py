# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import clear_clearing_date


class TestClearanceResetDisplayContract(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("translator@example.com")

	def run_reset(self, allowed, voucher_type, voucher_name):
		events = []
		document = SimpleNamespace(clearance_date="2026-10-20")

		def permission(kind):
			events.append(("permission", kind))
			return allowed

		def store(field, value):
			events.append(("db_set", field, value))
			setattr(document, field, value)

		def read(doctype, name):
			events.append(("get_doc", doctype, name))
			self.assertEqual((doctype, name), (voucher_type, voucher_name))
			return document

		document.has_permission = permission
		document.db_set = store
		with patch.object(frappe, "get_doc", side_effect=read):
			self.assertIsNone(clear_clearing_date(voucher_type, voucher_name))
		self.assertEqual(events[:2], [("get_doc", voucher_type, voucher_name), ("permission", "write")])
		return document, events

	def test_permitted_request_reaches_only_the_memory_date_mutation(self):
		for kind in ("Payment Entry", "Journal Entry", "Custom Voucher"):
			with self.subTest(kind=kind):
				document, events = self.run_reset(True, kind, "VOUCHER-RAW")
				self.assertIsNone(document.clearance_date)
				self.assertEqual(events[2:], [("db_set", "clearance_date", None)])

	def test_permission_noop_returns_the_same_success_shape_without_removing_the_date(self):
		for kind in ("Payment Entry", "Journal Entry", "Custom Voucher"):
			with self.subTest(kind=kind):
				document, events = self.run_reset(False, kind, "VOUCHER-RAW")
				self.assertEqual(document.clearance_date, "2026-10-20")
				self.assertEqual(len(events), 2)
