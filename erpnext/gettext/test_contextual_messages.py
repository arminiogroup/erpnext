import json
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.tests import UnitTestCase
from frappe.utils.formatters import format_value

from erpnext.stock.report.stock_ageing.stock_ageing import get_columns


class TestContextualMessages(UnitTestCase):
	def test_ambiguous_metadata_values_are_extractable_in_their_display_context(self):
		cases = [
			("accounts", "share_transfer", "transfer_type", "Issue", "Emissione"),
			("assets", "asset_movement", "purpose", "Issue", "Assegnazione"),
			("assets", "asset", "status", "Issue", "Assegnato"),
			("stock", "serial_no", "asset_status", "Issue", "Assegnato"),
			("stock", "material_request", "status", "Issued", "Materiale scaricato"),
			("stock", "shipment", "tracking_status", "Lost", "Smarrita"),
		]
		messages = self.get_contextual_messages()
		translations = {"Issue": "Segnalazione", "Issued": "Emesso", "Lost": "Perso"}
		for module, doctype, fieldname, value, target in cases:
			metadata = self.get_metadata(module, doctype)
			field = next(field for field in metadata["fields"] if field["fieldname"] == fieldname)
			self.assertIn(value, field["options"].split("\n"))
			context = metadata["name"]
			self.assertIn((value, context), messages)
			translations[f"{value}:{context}"] = target
			with patch("frappe.translate.get_all_translations", return_value=translations):
				self.assertEqual(format_value(value, frappe._dict(field, parent=context)), target)

	def test_product_bundle_references_have_distinct_label_contexts(self):
		messages = self.get_contextual_messages()
		for module, doctype, fieldname in [
			("manufacturing", "production_plan_item", "product_bundle_item"),
			("manufacturing", "work_order", "product_bundle_item"),
			("stock", "pick_list_item", "product_bundle_item"),
		]:
			metadata = self.get_metadata(module, doctype)
			field = next(field for field in metadata["fields"] if field["fieldname"] == fieldname)
			self.assertIn((field["label"], metadata["name"]), messages)
			self.assertEqual(field["label"], "Product Bundle Item")

	def test_latest_stock_age_is_not_the_latest_quotation_revision(self):
		translations = {"Latest": "Ultima revisione", "Latest:Stock Ageing": "Anzianità minima"}
		with patch("frappe.translate.get_all_translations", return_value=translations):
			columns = get_columns(frappe._dict(ranges=[]))
		latest = next(column for column in columns if column["fieldname"] == "latest")
		self.assertEqual(latest["label"], "Anzianità minima")
		self.assertEqual(latest["fieldtype"], "Int")

	def get_contextual_messages(self):
		with Path(__file__).with_name("contextual_messages.py").open("rb") as source:
			return {
				(message, context)
				for _line, message, _comments, context in extract(
					"frappe.gettext.extractors.python.extract", source, keywords=PYTHON_KEYWORDS
				)
			}

	def get_metadata(self, module, doctype):
		path = Path(frappe.get_app_path("erpnext", module, "doctype", doctype, f"{doctype}.json"))
		return json.loads(path.read_text())
