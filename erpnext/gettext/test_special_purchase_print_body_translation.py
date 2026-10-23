# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy

import frappe
from frappe.printing.doctype.print_format.classic_converter import uses_beta_renderer
from frappe.printing.layout import iter_fields, iter_zones
from frappe.tests import UnitTestCase

from erpnext.gettext.test_auditing_print_body_translation import TRANSLATIONS, MemoryPrintFixture


class TestSpecialPurchasePrintBodyTranslation(MemoryPrintFixture, UnitTestCase):
	FORMAT_PATHS = (
		"erpnext/buying/print_format/drop_shipping_format/drop_shipping_format.json",
		"erpnext/stock/print_format/purchase_receipt_serial_and_batch_bundle_print/purchase_receipt_serial_and_batch_bundle_print.json",
	)

	def test_actual_custom_and_converted_templates_translate_purchase_captions(self):
		for index, path in enumerate(self.FORMAT_PATHS):
			for restricted in (True, False):
				with self.subTest(path=path, restricted=restricted):
					result = self.render(path, restricted=restricted)
					self.assertEqual(uses_beta_renderer(frappe._dict(result.definition)), bool(index))
					self.assertEqual(result.definition["disabled"], 0)
					self.assertIsNone(result.definition.get("default_print_language"))
					for key in (
						"Purchase Receipt" if index else "Purchase Order",
						"No.",
						"Item Name",
						"Qty",
						"Rate",
						"Amount",
					):
						self.assertIn(TRANSLATIONS[key], result.html)
					self.assertIn("12.5 Nos", result.html)
					self.assertIn("12.50", result.html)
					self.assertIn("156.25", result.html)
					self.assertIn("ITEM-DESCRIPTION<b>RAW</b>", result.html)
					self.assertEqual(
						(result.doc.items[0].qty, result.doc.items[0].rate, result.doc.items[0].amount),
						(12.5, 12.5, 156.25),
					)
					if index:
						self.assertIsNotNone(result.generator)
						fields = [
							field for _, zone in iter_zones(result.layout) for field in iter_fields(zone)
						]
						self.assertGreater(len(fields), 3)
						self.assertIn(TRANSLATIONS["Serial Nos"], result.html)
						self.assertIn("Lotti (quantità)", result.html)
					else:
						self.assertIsNone(result.generator)
						for key in (
							"Supplier Name",
							"Customer Name",
							"Customer Address",
							"Customer Contact",
							"Customer Mobile No",
							"Grand Total",
							"In Words",
						):
							self.assertIn(TRANSLATIONS[key], result.html)
						for raw in (
							"SUPPLIER-NAME<&>",
							"CUSTOMER-NAME<&>",
							"SHIP<b>RAW</b>",
							"CUSTOMER-CONTACT-RAW",
							"AMOUNT-IN-WORDS-RAW",
						):
							self.assertIn(raw, result.html)
						self.assertNotIn("SUPPLIER-ID-RAW", result.html)

	def test_bundle_query_raw_serial_and_batch_values_and_full_document_state(self):
		pairs = []
		for language in ("en", "it"):
			for restricted in (True, False):
				result = self.render(self.FORMAT_PATHS[1], language, restricted)
				requests = [read for read in result.reads if read[0] == "get_list"]
				self.assertEqual(len(requests), 1)
				args, kwargs = requests[0][1:]
				self.assertEqual(args, ("Serial and Batch Entry",))
				self.assertEqual(
					kwargs["fields"],
					["serial_no.serial_no as serial_no", "batch_no.batch_id as batch_no", "qty"],
				)
				self.assertEqual(kwargs["filters"], {"parent": "BUNDLE-RAW"})
				self.assertTrue(kwargs["ignore_permissions"])
				for raw in ("SERIAL<&>-RAW", "BATCH<&>-RAW : 12.5 Nos", "ITEM-NAME<&>-RAW", "ITEM<&>-RAW"):
					self.assertIn(raw, result.html)
				self.assertEqual(result.before, result.after)
				pairs.append((result.before, requests))
		self.assertTrue(all(pair == pairs[0] for pair in pairs))

	def test_empty_items_and_bundles_preserve_queries_and_existing_last_batch_value(self):
		for restricted in (True, False):
			with self.subTest(restricted=restricted):
				empty = self.render(self.FORMAT_PATHS[1], restricted=restricted, empty=True)
				self.assertFalse(any(read[0] == "get_list" for read in empty.reads))
				self.assertNotIn("SERIAL<&>-RAW", empty.html)
				self.db.bundle_rows = []
				no_values = self.render(self.FORMAT_PATHS[1], restricted=restricted)
				self.assertEqual(len([read for read in no_values.reads if read[0] == "get_list"]), 1)
				self.assertNotIn("BATCH<&>-RAW", no_values.html)
				self.db.bundle_rows = [
					frappe._dict(serial_no="SERIAL-FIRST", batch_no="SAME-BATCH", qty=7),
					frappe._dict(serial_no="SERIAL-LAST", batch_no="SAME-BATCH", qty=2),
				]
				repeated = self.render(self.FORMAT_PATHS[1], restricted=restricted)
				self.assertIn("SERIAL-FIRST,SERIAL-LAST", repeated.html)
				self.assertIn("SAME-BATCH : 2 Nos", repeated.html)
				self.assertNotIn("SAME-BATCH : 9", repeated.html)
				self.assertEqual(repeated.before, repeated.after)

	def test_optional_drop_shipping_blocks_and_item_code_branch_keep_actual_bindings(self):
		for restricted in (True, False):

			def no_contacts(doc):
				for field in (
					"address_display",
					"contact_display",
					"contact_mobile",
					"shipping_address_display",
					"customer_contact_display",
					"customer_contact_mobile",
				):
					setattr(doc, field, None)
				doc.items[0].item_code = doc.items[0].item_name

			result = self.render(self.FORMAT_PATHS[0], restricted=restricted, update=no_contacts)
			for key in (
				"Address",
				"Contact",
				"Customer Address",
				"Customer Contact",
				"Customer Mobile No",
				"Item Code",
			):
				self.assertNotIn(TRANSLATIONS[key], result.html)
			self.assertIn("SUPPLIER-NAME<&>", result.html)
			self.assertIn("CUSTOMER-NAME<&>", result.html)
			self.assertEqual(result.before, result.after)

	def test_distinct_authored_HTML_stays_authored_and_English_captions_stay_whole(self):
		for restricted in (True, False):
			for path in self.FORMAT_PATHS:
				english = self.render(path, "en", restricted)
				for caption in ("No.", "Item Name", "Qty", "Rate", "Amount"):
					self.assertIn(caption, english.html)
				self.assertNotIn("<th>Sr</th>", english.html)
			definition = deepcopy(self.render(self.FORMAT_PATHS[0], restricted=restricted).definition)
			definition["html"] = "<p>Authored Rate / Batch Nos (Qty): {{ doc.supplier_name }}</p>"
			self.db.custom_module = True
			from frappe.www.printview import get_print_format

			template = get_print_format("Purchase Order", frappe._dict(definition))
			self.assertEqual(template, definition["html"])
			html = (
				self.environment("it", restricted)
				.from_string(template)
				.render(doc=self.document("Purchase Order"))
			)
			self.assertEqual(html, "<p>Authored Rate / Batch Nos (Qty): SUPPLIER-NAME<&></p>")
			self.db.custom_module = False

	def test_real_configured_extraction_registers_bundle_caption_and_ignores_test_module(self):
		keys = {
			message
			for path in self.FORMAT_PATHS
			for _, message, _, _ in self.extracted(self.source_path(path))
		}
		self.assertIn("Batch Nos (Qty)", keys)
		self.assertIn("Purchase Receipt", keys)
		self.assertIn("No.", keys)
		self.assertNotIn("Sr", keys)
		self.assertFalse(
			self.extracted(
				self.source_path("erpnext/gettext/test_special_purchase_print_body_translation.py")
			)
		)
