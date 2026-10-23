import json
import re
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase
from frappe.utils.formatters import format_value


class TestPriceCostMetadataContexts(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		with Path(frappe.get_app_path("erpnext", "gettext", "contextual_messages.py")).open("rb") as file:
			self.markers = {
				(message, context)
				for _line, message, _comments, context in extract(
					"frappe.gettext.extractors.python.extract", file, keywords=PYTHON_KEYWORDS
				)
			}
		self.cases = [
			("Rate", "BOM Item", "Costo unitario", "rate"),
			("Rate", "BOM Creator Item", "Costo unitario", "rate"),
			("Rate", "BOM Explosion Item", "Costo unitario", "rate"),
			("Rate", "Work Order Item", "Costo unitario", "rate"),
			("Rate", "Work Order Additional Item", "Costo unitario", "rate"),
			("Rate", "Landed Cost Item", "Costo unitario", "rate"),
			("Rate", "Subcontracting Inward Order Received Item", "Costo unitario", "rate"),
			("Rate", "Subcontracting Order Item", "Costo unitario", "rate"),
			("Rate", "Subcontracting Order Supplied Item", "Costo unitario", "rate"),
			("Rate", "Subcontracting Receipt Item", "Costo unitario", "rate"),
			("Rate", "Purchase Receipt Item Supplied", "Costo unitario di valorizzazione", "rate"),
			("Rate", "Subcontracting Receipt Supplied Item", "Costo unitario di valorizzazione", "rate"),
			("Rate", "Asset Capitalization Service Item", "Costo unitario del servizio", "rate"),
			("Basic Rate (Company Currency)", "BOM Item", "Costo unitario (valuta aziendale)", "base_rate"),
			("Basic Rate", "BOM Item", "Costo unitario", "base_rate"),
			("Basic Rate", "Sales Order Item", "Prezzo unitario", "base_rate"),
			("Quantity and Rate", "BOM Item", "Quantità e costi", "quantity_and_rate"),
			("Quantity and Rate", "BOM Creator Item", "Quantità e costi", "quantity_and_rate_section"),
			(
				"Quantity and Rate",
				"Subcontracting Order Item",
				"Quantità e costi",
				"quantity_and_rate_section",
			),
			(
				"Quantity and Rate",
				"Serial and Batch Bundle",
				"Quantità e valorizzazione",
				"quantity_and_rate_section",
			),
			("Rates", "Stock Entry Detail", "Costi", "rates_section"),
			("Advance Paid", "Purchase Order", "Acconti versati", "advance_paid"),
		]
		self.translations = {
			"Rate": "Prezzo unitario",
			"Rates": "Aliquote",
			"Quantity and Rate": "Quantità e prezzo",
			"Basic Rate (Company Currency)": "Prezzo unitario (valuta aziendale)",
			"Advance Paid": "Acconti ricevuti",
		}
		for source, context, target, _field in self.cases:
			if (source, context) in self.markers:
				self.translations[f"{source}:{context}"] = target
		if ("Rate", "Pricing Rule") in self.markers:
			self.translations["Rate:Pricing Rule"] = "Prezzo"
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.translations if lang == "it" else {},
			)
		)

	def metadata(self, doctype):
		name = frappe.scrub(doctype)
		matches = list(Path(frappe.get_app_path("erpnext")).glob(f"*/doctype/{name}/{name}.json"))
		self.assertEqual(len(matches), 1)
		metadata = json.loads(matches[0].read_text())
		fields = {
			field["fieldname"]: frappe._dict(field, parent=metadata["name"]) for field in metadata["fields"]
		}
		meta = Meta.__new__(Meta)
		meta.__dict__.update(name=metadata["name"], _fields=fields)
		return meta, fields

	def test_registered_cost_and_quantity_labels_reach_actual_metadata_translator(self):
		self.assertEqual(frappe.session.user, "translator@example.com")
		for source, context, target, fieldname in self.cases:
			with self.subTest(source=source, context=context):
				meta, fields = self.metadata(context)
				before = dict(fields[fieldname])
				if source in ("Basic Rate", "Advance Paid"):
					label = re.sub(
						r"\s*\([^)]*currency[^)]*\)\s*$", "", meta.get_label(fieldname), flags=re.I
					)
					self.assertEqual(label, source)
					translated = frappe._(label, context=meta.name)
				else:
					self.assertEqual(meta.get_label(fieldname), source)
					translated = meta.get_translated_label(fieldname)
				self.assertEqual(translated, target)
				self.assertEqual(dict(fields[fieldname]), before)
				frappe.local.lang = "en"
				self.assertEqual(frappe._(source, context=meta.name), source)
				frappe.local.lang = "it"

	def test_commercial_metadata_keeps_global_fallback_and_existing_pricing_context(self):
		for doctype, expected in (
			("Sales Order Item", "Prezzo unitario"),
			("Purchase Order Item", "Prezzo unitario"),
			("Pricing Rule", "Prezzo"),
		):
			with self.subTest(doctype=doctype):
				meta, fields = self.metadata(doctype)
				self.assertEqual(meta.get_translated_label("rate"), expected)
				self.assertEqual(meta.get_label("rate"), "Rate")
				self.assertEqual(fields["rate"].fieldtype, "Currency")
		meta, fields = self.metadata("Sales Order Item")
		self.assertEqual(meta.get_translated_label("base_rate"), "Prezzo unitario (valuta aziendale)")
		self.assertEqual(fields["base_rate"].options, "Company:company:default_currency")
		fields["rate"].label = "Prezzo concordato con il cliente"
		self.assertEqual(meta.get_translated_label("rate"), "Prezzo concordato con il cliente")

	def test_select_prices_keep_canonical_values_and_pricing_rule_context(self):
		for doctype, fieldname, expected in (
			("Stock Settings", "update_price_list_based_on", "Prezzo unitario"),
			("Pricing Rule", "rate_or_discount", "Prezzo"),
		):
			with self.subTest(doctype=doctype):
				_meta, fields = self.metadata(doctype)
				field = fields[fieldname]
				before = dict(field)
				value = "Rate"
				self.assertIn(value, field.options.split("\n"))
				self.assertEqual(format_value(value, field, translated=True), expected)
				self.assertEqual(value, "Rate")
				self.assertEqual(dict(field), before)
				frappe.local.lang = "en"
				self.assertEqual(format_value(value, field, translated=True), value)
				frappe.local.lang = "it"
