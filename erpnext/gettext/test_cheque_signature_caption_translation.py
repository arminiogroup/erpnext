# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import re
from copy import deepcopy

import frappe
from frappe.tests import UnitTestCase
from frappe.www.printview import get_print_format

from erpnext.gettext.test_auditing_print_body_translation import TRANSLATIONS, MemoryPrintFixture


class TestChequeSignatureCaptionTranslation(MemoryPrintFixture, UnitTestCase):
	FORMAT_PATHS = ("erpnext/accounts/print_format/cheque_printing_format/cheque_printing_format.json",)
	SIGNATURES = ("Prepared By", "Authorised Signatory", "Received Payment as Above")
	POSITIONS = (
		("14", "0"),
		("14", "5.5"),
		("14", "11"),
		("16.4", "5.9"),
		("16.7", "6"),
		("16.7", "5.9"),
		("16.9", "12"),
		("17.9", "1"),
		("18.6", "1"),
		("19.7", "12"),
	)

	def test_actual_custom_HTML_selection_translates_three_whole_signature_captions(self):
		for language in ("en", "it"):
			for restricted in (True, False):
				with self.subTest(language=language, restricted=restricted):
					result = self.render(self.FORMAT_PATHS[0], language, restricted)
					self.assertEqual(result.definition["custom_format"], 1)
					self.assertIsNone(result.definition.get("default_print_language"))
					self.assertIsNone(result.generator)
					for caption in self.SIGNATURES:
						self.assertIn(TRANSLATIONS[caption] if language == "it" else caption, result.html)
					for raw in (
						"12.50",
						"TWELVE-RAW",
						"PAYEE-RAW",
						"CHEQUE-RAW",
						"2026-10-21",
						"REMARKS-RAW",
					):
						self.assertIn(raw, result.html)
					self.assertEqual(result.doc.total_amount, 12.5)
					self.assertEqual(result.doc.pay_to_recd_from, "PAYEE-RAW")
					self.assertEqual(result.doc.cheque_date, "2026-10-21")

	def test_all_existing_physical_coordinates_and_unresolved_payee_clause_stay_exact(self):
		for restricted in (True, False):
			result = self.render(self.FORMAT_PATHS[0], restricted=restricted)
			self.assertEqual(
				tuple(re.findall(r"position: absolute; top: ([\d.]+)cm; left: ([\d.]+)cm;", result.html)),
				self.POSITIONS,
			)
			self.assertIn("width: 7cm;", result.html)
			self.assertIn("<strong>A/C Payee</strong>", result.html)
			self.assertEqual(result.html.count("<strong>_____________</strong>"), 2)
			# This pre-existing template field is absent from Journal Entry. Do not
			# substitute another date or imply this caption fix repairs that gap.
			self.assertIsNone(result.doc.meta.get_field("voucher_date"))
			self.assertNotIn("voucher_date", result.doc.__dict__)
			self.assertNotIn("2026-10-20", result.html)

	def test_complete_raw_document_state_and_read_requests_preserve_existing_heading_cache(self):
		pairs = []
		for language in ("en", "it"):
			for restricted in (True, False):
				result = self.render(self.FORMAT_PATHS[0], language, restricted)
				before, after = deepcopy(result.before), deepcopy(result.after)
				after.pop("select_print_heading")
				before.pop("select_print_heading", None)
				self.assertEqual(set(after) - set(before), {"_table_fieldnames"})
				self.assertIs(result.doc.__dict__["_table_fieldnames"], result.doc.meta._table_doctypes)
				self.assertEqual(
					after.pop("_table_fieldnames"),
					{field.fieldname: field.options for field in result.doc.meta._table_fields},
				)
				self.assertEqual(after, before)
				self.assertFalse(any(read[0] == "get_list" for read in result.reads))
				pairs.append((result.before, result.reads))
		self.assertTrue(all(pair == pairs[0] for pair in pairs))

	def test_existing_amount_words_optional_branch_and_authored_signature_text_are_preserved(self):
		for restricted in (True, False):

			def no_words(doc):
				doc.total_amount_in_words = None

			result = self.render(self.FORMAT_PATHS[0], restricted=restricted, update=no_words)
			self.assertIn("<strong>12.50</strong><br><br>", result.html)
			self.assertNotIn("TWELVE-RAW", result.html)
			definition = deepcopy(result.definition)
			definition["html"] = "<p>Prepared By: {{ doc.pay_to_recd_from }}</p>"
			self.db.custom_module = True
			template = get_print_format("Journal Entry", frappe._dict(definition))
			self.assertEqual(template, definition["html"])
			html = (
				self.environment("it", restricted)
				.from_string(template)
				.render(doc=self.document("Journal Entry"))
			)
			self.assertEqual(html, "<p>Prepared By: PAYEE-RAW</p>")
			self.db.custom_module = False

	def test_actual_configured_extraction_registers_only_whole_signature_keys(self):
		keys = {message for _, message, _, _ in self.extracted(self.source_path(self.FORMAT_PATHS[0]))}
		for caption in self.SIGNATURES:
			self.assertIn(caption, keys)
		self.assertNotIn("A/C Payee", keys)
		self.assertFalse(
			self.extracted(self.source_path("erpnext/gettext/test_cheque_signature_caption_translation.py"))
		)
