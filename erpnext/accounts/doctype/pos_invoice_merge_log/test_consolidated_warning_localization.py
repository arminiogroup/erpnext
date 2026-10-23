# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.document import Document
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import POSInvoiceMergeLog

FRAME = "Row #{0}: POS Invoice {1} is already consolidated."
NOT_SUBMITTED = "Row #{0}: POS Invoice {1} is not submitted yet"
MESSAGES = {
	FRAME: "Riga #{0}: la fattura POS {1} è già consolidata.",
	NOT_SUBMITTED: "Riga #{0}: la fattura POS {1} non è ancora stata confermata",
	"Company": "Azienda",
	"Consolidated": "Consolidata",
}


class TestConsolidatedWarningLocalization(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.messages = dict(MESSAGES)
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.messages if lang == "it" else {},
			)
		)
		self.reads = []
		self.responses = []
		db = SimpleNamespace(get_value=self._get_value)
		self.enterContext(patch.object(frappe.local, "db", db, create=True))
		for name in ("get_doc", "new_doc", "delete_doc", "sendmail", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))
		for name in ("save", "submit", "cancel", "delete"):
			self.enterContext(
				patch.object(Document, name, side_effect=AssertionError("Unexpected write: " + name))
			)

	def _get_value(self, *args, **kwargs):
		self.assertEqual(kwargs, {})
		self.reads.append(args)
		self.assertTrue(self.responses, "Unexpected database read")
		return self.responses.pop(0)

	def _validate(self, rows):
		before = deepcopy(rows)
		try:
			POSInvoiceMergeLog.validate_pos_invoice_status(SimpleNamespace(pos_invoices=rows))
		except frappe.ValidationError as error:
			return str(error)
		finally:
			self.assertEqual(rows, before)
		return None

	def _row(self, name="Company", idx=7):
		return frappe._dict(idx=idx, pos_invoice=name)

	def _primary_read(self, name):
		return ("POS Invoice", name, ["status", "docstatus", "is_return", "return_against"])

	def test_submitted_consolidated_uses_complete_sentence_and_raw_invoice_name(self):
		self.responses = [("Consolidated", 1, 0, None)]
		self.assertEqual(
			self._validate([self._row()]),
			"Riga #7: la fattura POS <strong>Company</strong> è già consolidata.",
		)
		self.assertEqual(self.reads, [self._primary_read("Company")])
		self.assertEqual(self.responses, [])

	def test_hostile_business_name_retains_existing_raw_bold_bytes(self):
		name = 'INV-<tag> & "quoted"'
		self.responses = [("Consolidated", 1, 0, None)]
		self.assertEqual(
			self._validate([self._row(name, 2)]),
			f"Riga #2: la fattura POS <strong>{name}</strong> è già consolidata.",
		)
		self.assertEqual(self.reads, [self._primary_read(name)])

	def test_draft_and_cancelled_hit_not_submitted_guard_first(self):
		for docstatus in (0, 2):
			with self.subTest(docstatus=docstatus):
				self.reads.clear()
				self.responses = [("Consolidated", docstatus, 1, "ORIGINAL")]
				self.assertEqual(
					self._validate([self._row()]),
					"Riga #7: la fattura POS <strong>Company</strong> non è ancora stata confermata",
				)
				self.assertEqual(self.reads, [self._primary_read("Company")])

	def test_nonconsolidated_submitted_and_empty_rows_do_not_throw(self):
		for status in ("Paid", "Unpaid", "", None):
			with self.subTest(status=status):
				self.reads.clear()
				self.responses = [(status, 1, 0, None)]
				self.assertIsNone(self._validate([self._row()]))
				self.assertEqual(self.reads, [self._primary_read("Company")])
		self.reads.clear()
		self.assertIsNone(self._validate([]))
		self.assertEqual(self.reads, [])

	def test_return_against_branch_keeps_ordered_read_and_original_guidance(self):
		self.messages.clear()
		for original_status in ("Paid", "Consolidated"):
			with self.subTest(original_status=original_status):
				self.reads.clear()
				self.responses = [("Paid", 1, 1, "ORIGINAL"), original_status]
				message = self._validate([self._row("RETURN", 3)])
				if original_status == "Consolidated":
					self.assertIsNone(message)
				else:
					self.assertEqual(
						message,
						"Row #3: The original Invoice <strong>ORIGINAL</strong> of return invoice "
						"<strong>RETURN</strong> is not consolidated. "
						"The original invoice should be consolidated before or along with the return invoice."
						"<br><br>You can add the original invoice <strong>ORIGINAL</strong> manually to proceed.",
					)
				self.assertEqual(
					self.reads,
					[self._primary_read("RETURN"), ("POS Invoice", "ORIGINAL", "status")],
				)

	def test_included_original_avoids_extra_lookup_and_retains_row_order(self):
		self.responses = [("Paid", 1, 0, None), ("Paid", 1, 1, "ORIGINAL")]
		self.assertIsNone(self._validate([self._row("ORIGINAL", 1), self._row("RETURN", 2)]))
		self.assertEqual(self.reads, [self._primary_read("ORIGINAL"), self._primary_read("RETURN")])
		self.assertEqual(self.responses, [])

	def test_english_and_empty_complete_target_fall_back_without_status_fragment(self):
		for provider in ({}, {FRAME: ""}):
			with self.subTest(provider=provider):
				self.messages = provider
				self.responses = [("Consolidated", 1, 0, None)]
				self.assertEqual(
					self._validate([self._row()]),
					"Row #7: POS Invoice <strong>Company</strong> is already consolidated.",
				)
