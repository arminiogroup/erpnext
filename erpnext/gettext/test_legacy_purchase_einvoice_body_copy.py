# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy

import frappe
from frappe.tests import UnitTestCase
from frappe.www.printview import get_print_format

from erpnext.gettext.test_auditing_print_body_translation import MemoryPrintFixture
from erpnext.regional.italy.setup import get_custom_fields


class TestLegacyPurchaseEInvoiceBodyCopy(MemoryPrintFixture, UnitTestCase):
	FORMAT_PATHS = ("erpnext/regional/print_format/purchase_einvoice/purchase_einvoice.json",)
	CORRECTIONS = (
		("Identificstivo", "Identificativo"),
		("Indrizo", "Indirizzo"),
		("Commune", "Comune"),
		("Cap:", "CAP:"),
		("Tipologla", "Tipologia"),
		("Quantita", "Quantità"),
		("Importo bolio", "Importo bollo"),
		("Sconto/Magglorazione", "Sconto/Maggiorazione"),
		("Modalita pagamento", "Modalità di pagamento"),
		("Instituto", "Istituto"),
	)

	def setUp(self):
		super().setUp()
		# This factory returns ordinary definitions only. Do not call setup,
		# create_custom_fields, migrate or any persistent fixture constructor.
		self.regional_fields = get_custom_fields()

	def regional_values(self, doc):
		doc.document_type = "TD01-RAW"
		doc.destination_code = "DEST-RAW"
		doc.bill_no = "BILL-RAW"
		doc.bill_date = "2026-10-21"
		doc.items[0].tax_rate = 22

	def test_real_disabled_guard_still_rejects_standard_format(self):
		definition = frappe._dict(json.loads(self.source_path(self.FORMAT_PATHS[0]).read_text()))
		for restricted in (True, False):
			self.environment("it", restricted)
			before = deepcopy(dict(definition))
			with self.assertRaises(frappe.DoesNotExistError):
				get_print_format("Purchase Invoice", definition)
			self.assertEqual(dict(definition), before)
			self.assertEqual(definition.disabled, 1)
			self.assertEqual(definition.custom_format, 0)
			self.assertIsNone(definition.get("default_print_language"))

	def test_inert_actual_converter_and_both_factories_render_only_approved_copy(self):
		for language in ("en", "it"):
			for restricted in (True, False):
				with self.subTest(language=language, restricted=restricted):
					result = self.render(
						self.FORMAT_PATHS[0], language, restricted, update=self.regional_values
					)
					self.assertIsNotNone(result.generator)
					self.assertEqual(result.definition["disabled"], 1)
					for old, new in self.CORRECTIONS:
						self.assertIn(new, result.html)
						self.assertNotIn(old, result.html)
					self.assertIn(
						"Fattura di acquisto" if language == "it" else "Purchase Invoice", result.html
					)
					for raw in (
						"TD01-RAW",
						"DEST-RAW",
						"BILL-RAW",
						"2026-10-21",
						"ITEM-DESCRIPTION<b>RAW</b>",
						"MP05-RAW",
						"IBAN-RAW",
						"2026-11-01",
						"12.50",
						"156.25",
						"165.75",
						"Nos",
					):
						self.assertIn(raw, result.html)
					self.assertEqual(result.before, result.after)
					self.assertEqual(
						(
							result.doc.items[0].qty,
							result.doc.items[0].rate,
							result.doc.items[0].amount,
							result.doc.items[0].tax_rate,
						),
						(12.5, 12.5, 156.25, 22),
					)
					self.assertEqual(result.doc.meta.get_field("document_type").parent, "Purchase Invoice")
					self.assertEqual(
						result.doc.items[0].meta.get_field("tax_rate").parent, "Purchase Invoice Item"
					)
					self.assertEqual(
						result.doc.payment_schedule[0].meta.get_field("bank_account_iban").parent,
						"Payment Schedule",
					)

	def test_exact_raw_supplier_company_address_reads_and_full_state_stay_canonical(self):
		expected = [
			("Supplier", "SUPPLIER-ID-RAW", field)
			for field in ("tax_id", "fiscal_code", "supplier_name", "fiscal_regime")
		]
		expected += [
			("Address", "SUPPLIER-ADDRESS-RAW", field)
			for field in ("address_line1", "city", "state_code", "pincode", "country")
		]
		expected += [("Company", "COMPANY-RAW", field) for field in ("tax_id", "fiscal_code")]
		expected += [
			("Address", "SHIP-ADDRESS-RAW", field)
			for field in ("address_line1", "city", "state_code", "pincode", "country")
		]
		pairs = []
		for language in ("en", "it"):
			for restricted in (True, False):
				result = self.render(self.FORMAT_PATHS[0], language, restricted, update=self.regional_values)
				reads = [
					read
					for read in result.reads
					if read[0] == "get_value" and read[1] in ("Supplier", "Company", "Address")
				]
				self.assertEqual([tuple(read[1:4]) for read in reads], expected)
				# The actual HTML macro calls render_template with its default
				# restricted environment even when the outer factory is unrestricted.
				self.assertEqual([read[4] for read in reads], [{"run": True}] * 16)
				self.assertEqual(result.before, result.after)
				pairs.append((result.before, [read[:4] for read in reads]))
		self.assertTrue(all(pair == pairs[0] for pair in pairs))

	def test_blank_fixed_fiscal_claims_and_existing_description_branch_are_not_certified(self):
		for restricted in (True, False):
			result = self.render(self.FORMAT_PATHS[0], restricted=restricted, update=self.regional_values)
			# Arbitrary description and existing fixed/blank fiscal cells are
			# deliberately retained. Orthographic cleanup is not fiscal validation.
			for text in (
				"esigibilita immediata / riferimenti normativi",
				"Art. 73",
				"TAX-DESCRIPTION-RAW",
				"0,00",
			):
				self.assertIn(text, result.html)

			def missing_description(doc):
				doc.taxes[0].description = "None"

			hidden = self.render(self.FORMAT_PATHS[0], restricted=restricted, update=missing_description)
			self.assertNotIn("TAX-DESCRIPTION-RAW", hidden.html)
			empty = self.render(self.FORMAT_PATHS[0], restricted=restricted, empty=True)
			self.assertNotIn("ITEM-DESCRIPTION", empty.html)
			self.assertNotIn("MP05-RAW", empty.html)
			self.assertIn("165.75", empty.html)
			self.assertEqual(empty.before, empty.after)

	def test_unmarked_authored_Italian_is_not_globally_autocorrected(self):
		for restricted in (True, False):
			definition = frappe._dict(
				name="Authored test format",
				module="Custom",
				custom_format=1,
				disabled=0,
				html="<p>Quantita / Instituto: {{ doc.bill_no }}</p>",
			)
			self.db.custom_module = True
			template = get_print_format("Purchase Invoice", definition)
			self.assertEqual(template, definition.html)
			doc = self.document("Purchase Invoice")
			doc.bill_no = "BILL-RAW"
			self.assertEqual(
				self.environment("it", restricted).from_string(template).render(doc=doc),
				"<p>Quantita / Instituto: BILL-RAW</p>",
			)
			self.db.custom_module = False

	def test_real_configured_extraction_marks_heading_without_extracting_test_or_fiscal_claims(self):
		keys = {message for _, message, _, _ in self.extracted(self.source_path(self.FORMAT_PATHS[0]))}
		self.assertIn("Purchase Invoice", keys)
		self.assertNotIn("esigibilita immediata / riferimenti normativi", keys)
		self.assertFalse(
			self.extracted(self.source_path("erpnext/gettext/test_legacy_purchase_einvoice_body_copy.py"))
		)
