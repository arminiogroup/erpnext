# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import itertools
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry

FRAME = "Row #{0}: Duplicate entry in References {1} {2}"
TARGETS = {
	FRAME: "Riga {0}: riferimento duplicato in Riferimenti: {1} {2}.",
	"Sales Invoice": "Fattura di vendita",
	"Purchase Invoice": "Fattura di acquisto",
	"Sales Order": "Ordine di vendita",
	"Purchase Order": "Ordine d’acquisto",
	"Journal Entry": "Registrazione contabile",
}


class TestDuplicateReferenceLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(frappe, "db", SimpleNamespace(db_type="mariadb")))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TARGETS if lang == "it" else {},
			)
		)
		for name in ("get_all", "get_doc", "get_meta", "get_cached_value", "new_doc", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))

	def row(self, **values):
		row = frappe._dict(
			reference_doctype="Sales Invoice",
			reference_name="Sales Invoice",
			payment_term="TERM-RAW",
			payment_request="PR-RAW",
			idx=1,
			allocated_amount=12.5,
			outstanding_amount=100,
			exchange_rate=1,
		)
		row.update(values)
		return row

	def document(self, rows):
		doc = PaymentEntry.__new__(PaymentEntry)
		doc.__dict__.update(doctype="Payment Entry", name="PE-RAW", references=rows, flags=frappe._dict())
		return doc

	def assert_duplicate(self, rows, language="it"):
		doc = self.document(rows)
		before = deepcopy(doc.__dict__)
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.validate_duplicate_entry()
		row = rows[-1]
		type_label = (
			TARGETS.get(row.reference_doctype, row.reference_doctype)
			if language == "it"
			else row.reference_doctype
		)
		self.assertEqual(
			str(caught.exception),
			(TARGETS[FRAME] if language == "it" else FRAME).format(row.idx, type_label, row.reference_name),
		)
		self.assertEqual(doc.__dict__, before)

	def test_duplicate_types_translate_while_record_name_and_amounts_stay_raw(self):
		for doctype in (
			"Sales Invoice",
			"Purchase Invoice",
			"Sales Order",
			"Purchase Order",
			"Journal Entry",
			"Custom Invoice",
		):
			with self.subTest(doctype=doctype):
				self.assert_duplicate(
					[
						self.row(reference_doctype=doctype),
						self.row(
							reference_doctype=doctype,
							idx=7,
							allocated_amount=99.125,
							outstanding_amount=-1,
							exchange_rate=2,
						),
					]
				)

	def test_every_identity_component_remains_part_of_duplicate_comparison(self):
		for changed in ("reference_doctype", "reference_name", "payment_term", "payment_request"):
			with self.subTest(changed=changed):
				other = self.row(idx=2)
				other[changed] = "OTHER-RAW"
				doc = self.document([self.row(), other])
				before = deepcopy(doc.__dict__)
				self.assertIsNone(doc.validate_duplicate_entry())
				self.assertEqual(doc.__dict__, before)

	def test_optional_term_and_request_values_preserve_exact_tuple_equality(self):
		for term, request in itertools.product((None, "", "TERM-RAW"), (None, "", "PR-RAW")):
			with self.subTest(term=term, request=request):
				self.assert_duplicate(
					[
						self.row(payment_term=term, payment_request=request),
						self.row(payment_term=term, payment_request=request, idx=2),
					]
				)

	def test_empty_one_row_and_late_duplicate_preserve_order(self):
		for rows in ([], [self.row()], [self.row(), self.row(reference_name="OTHER-RAW", idx=2)]):
			with self.subTest(rows=rows):
				doc = self.document(rows)
				before = deepcopy(doc.__dict__)
				self.assertIsNone(doc.validate_duplicate_entry())
				self.assertEqual(doc.__dict__, before)
		self.assert_duplicate([self.row(), self.row(reference_name="OTHER-RAW", idx=2), self.row(idx=3)])

	def test_english_and_unknown_type_fallbacks_preserve_raw_labels(self):
		frappe.local.lang = "en"
		self.assert_duplicate([self.row(), self.row(idx=2)], language="en")
		self.assert_duplicate(
			[
				self.row(reference_doctype="Custom Invoice"),
				self.row(reference_doctype="Custom Invoice", idx=2),
			],
			language="en",
		)
