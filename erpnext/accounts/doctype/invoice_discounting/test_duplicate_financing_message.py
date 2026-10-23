# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.invoice_discounting.invoice_discounting import InvoiceDiscounting

FRAME = "Row #{0}: Sales Invoice {1} is already included in a submitted Invoice Discounting document."
TARGET = (
	"Riga {0}: la fattura di vendita {1} è già inclusa in un documento di anticipo su fatture confermato."
)


class TestDuplicateFinancingMessage(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: {FRAME: TARGET, "Sales Invoice": "Fattura di vendita"}
				if lang == "it"
				else {},
			)
		)
		self.events = []
		self.submitted = []
		self.outstanding = 100
		self.enterContext(patch("frappe.get_all", side_effect=self.get_all))
		self.enterContext(patch.object(frappe, "db", SimpleNamespace(get_value=self.get_value)))
		for name in ("get_doc", "get_meta", "get_cached_value", "new_doc", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))

	def get_all(self, doctype, **kwargs):
		self.assertEqual(
			(doctype, kwargs),
			("Discounted Invoice", {"fields": ["sales_invoice"], "filters": {"docstatus": 1}}),
		)
		self.events.append(("get_all", doctype, deepcopy(kwargs)))
		return [frappe._dict(sales_invoice=name) for name in self.submitted]

	def get_value(self, doctype, name, field):
		self.assertEqual((doctype, field), ("Sales Invoice", "outstanding_amount"))
		self.events.append(("get_value", doctype, name, field))
		return self.outstanding

	def document(self, invoices, parent="CURRENT-FINANCING", status="Draft", docstatus=0):
		doc = InvoiceDiscounting.__new__(InvoiceDiscounting)
		doc.__dict__.update(
			doctype="Invoice Discounting",
			name=parent,
			status=status,
			docstatus=docstatus,
			invoices=[
				frappe._dict(idx=index, parent=parent, sales_invoice=name, outstanding_amount=amount)
				for index, (name, amount) in enumerate(invoices, 1)
			],
		)
		return doc

	def assert_duplicate(self, doc, row=0, language="it"):
		before = deepcopy(doc.__dict__)
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.validate_invoices()
		record = doc.invoices[row]
		self.assertEqual(
			str(caught.exception),
			(TARGET if language == "it" else FRAME).format(record.idx, frappe.bold(record.sales_invoice)),
		)
		self.assertEqual(doc.__dict__, before)
		return str(caught.exception)

	def test_submitted_match_never_claims_an_unsupported_current_parent(self):
		self.submitted = ["Sales Invoice"]
		messages = []
		for parent in ("CURRENT-FINANCING", "OTHER-FINANCING", "Fattura di vendita", None):
			with self.subTest(parent=parent):
				self.events.clear()
				messages.append(self.assert_duplicate(self.document([("Sales Invoice", 12.5)], parent)))
				self.assertEqual(len(self.events), 1)
		self.assertEqual(len(set(messages)), 1)
		self.assertIn("<strong>Sales Invoice</strong>", messages[0])

	def test_self_match_and_all_submitted_statuses_keep_same_decision(self):
		self.submitted = ["SI-RAW"]
		for status in ("Sanctioned", "Disbursed", "Settled"):
			with self.subTest(status=status):
				self.events.clear()
				doc = self.document([("SI-RAW", 12.5)], status=status, docstatus=1)
				self.assert_duplicate(doc)
				self.assertEqual(len(self.events), 1)

	def test_later_row_preserves_prior_outstanding_read_and_original_index(self):
		self.submitted = ["SI-SECOND"]
		doc = self.document([("SI-FIRST", 100), ("SI-SECOND", 25)])
		self.assert_duplicate(doc, row=1)
		self.assertEqual(self.events[1:], [("get_value", "Sales Invoice", "SI-FIRST", "outstanding_amount")])

	def test_empty_single_and_multiple_unmatched_rows_keep_raw_reads(self):
		for invoices in ([], [("SI-RAW", 12.5)], [("SI-1", 0), ("SI-2", 100)]):
			with self.subTest(invoices=invoices):
				self.events.clear()
				doc = self.document(invoices)
				before = deepcopy(doc.__dict__)
				self.assertIsNone(doc.validate_invoices())
				self.assertEqual(doc.__dict__, before)
				self.assertEqual(
					self.events[1:],
					[("get_value", "Sales Invoice", name, "outstanding_amount") for name, _ in invoices],
				)

	def test_outstanding_equality_and_excess_guards_are_unchanged(self):
		for outstanding, amount in (
			(0, 0),
			(12.5, 12.49),
			(12.5, 12.5),
			(12.5, 12.501),
			(100, 101),
			(-1, -1),
		):
			with self.subTest(outstanding=outstanding, amount=amount):
				self.events.clear()
				self.outstanding = outstanding
				doc = self.document([("SI-RAW", amount)])
				before = deepcopy(doc.__dict__)
				if amount > outstanding:
					with self.assertRaises(frappe.ValidationError) as caught:
						doc.validate_invoices()
					self.assertEqual(
						str(caught.exception),
						"Row({}): Outstanding Amount cannot be greater than actual Outstanding Amount {} in {}".format(
							1, frappe.bold(outstanding), frappe.bold("SI-RAW")
						),
					)
				else:
					self.assertIsNone(doc.validate_invoices())
				self.assertEqual(doc.__dict__, before)
				self.assertEqual(
					self.events[1:], [("get_value", "Sales Invoice", "SI-RAW", "outstanding_amount")]
				)

	def test_english_fallback_is_a_complete_truthful_message(self):
		frappe.local.lang = "en"
		self.submitted = ["SI-RAW"]
		self.assert_duplicate(self.document([("SI-RAW", 12.5)]), language="en")
