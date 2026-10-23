# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
import logging
import re
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.core.doctype.docfield.docfield import DocField
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.printing.layout import iter_fields, iter_zones
from frappe.tests import UnitTestCase
from frappe.utils.print_format_generator import PrintFormatGenerator

import erpnext

DOCUMENT_TYPES = (
	"Request for Quotation",
	"Purchase Order",
	"Quotation",
	"Sales Order",
	"Purchase Invoice",
	"POS Invoice",
	"Sales Invoice",
	"Delivery Note",
)
TRANSLATIONS = {
	"Request for Quotation": "Richiesta di offerta",
	"Purchase Order": "Ordine di acquisto",
	"Quotation": "Preventivo",
	"Sales Order": "Ordine di vendita",
	"Purchase Invoice": "Fattura di acquisto",
	"POS Invoice": "Fattura POS",
	"Sales Invoice": "Fattura di vendita",
	"Delivery Note": "Documento di trasporto",
	"Credit Note": "Nota di credito",
	"Debit Note": "Nota di debito",
	"Company": "Azienda",
	"Customer": "Cliente",
	"Supplier": "Fornitore",
	"Billed To": "Dati di fatturazione",
	"Bill To": "Dati di fatturazione",
	"Bill From": "Emittente",
	"Sub Total": "Subtotale",
	"No.": "Num.",
	"No": "No",
	"Rate": "Prezzo unitario",
	"Qty": "Quantità",
}


class NoDatabase:
	def __bool__(self):
		return False

	def __getattr__(self, name):
		def blocked(*args, **kwargs):
			raise AssertionError(f"Unexpected database operation: {name}")

		return blocked


class PrintDocument(Document):
	def get_formatted(self, fieldname, *args, **kwargs):
		return str(self.get(fieldname, ""))


class TextNodes(HTMLParser):
	def __init__(self, html):
		super().__init__()
		self.values = []
		self.feed(html)

	def handle_data(self, value):
		if value.strip():
			self.values.append(value.strip())


def standard_formats():
	formats = []
	for path in Path(erpnext.__file__).parent.glob("*/print_format/*/*.json"):
		data = json.loads(path.read_text())
		if data.get("doc_type") not in DOCUMENT_TYPES or not data.get("print_format_builder_beta"):
			continue
		if isinstance(json.loads(data.get("format_data") or "null"), dict):
			formats.append(data)
	return formats


def fields_in(layout):
	return [field for _, zone in iter_zones(layout) for field in iter_fields(zone)]


class TestStandardPrintFormatTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		self.addCleanup(setattr, frappe.local, "db", getattr(frappe.local, "db", None))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.db = NoDatabase()
		self.metas = {}
		self.enterContext(patch("frappe.get_meta", new=self.get_meta))
		self.enterContext(patch("frappe.get_hooks", return_value=[]))
		self.enterContext(patch("frappe.get_active_apps", return_value=["frappe"]))
		self.enterContext(patch("frappe.get_roles", return_value=["Translator"]))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TRANSLATIONS if lang == "it" else {},
			)
		)
		self.enterContext(
			patch(
				"frappe.utils.logger.get_logger", return_value=logging.getLogger("standard-print-format-test")
			)
		)
		for key in ("jenv_restricted", "jenv_unrestricted"):
			self.addCleanup(setattr, frappe.local, key, getattr(frappe.local, key, None))
			setattr(frappe.local, key, None)

	def get_meta(self, name, cached=True):
		if name in self.metas:
			return self.metas[name]
		package = Path(frappe.__file__).parent if name == "DocField" else Path(erpnext.__file__).parent
		candidates = package.glob(f"*/doctype/{frappe.scrub(name)}/{frappe.scrub(name)}.json")
		data = next(json.loads(path.read_text()) for path in candidates)
		meta = Meta.__new__(Meta)
		meta.__dict__.update(doctype="DocType", name=name, fields=[], istable=data.get("istable", 0))
		self.metas[name] = meta
		field_meta = meta if name == "DocField" else self.get_meta("DocField")
		for source in data["fields"]:
			field = DocField.__new__(DocField)
			field.__dict__.update(
				{
					item["fieldname"]: None
					for item in json.loads(
						(Path(frappe.__file__).parent / "core/doctype/docfield/docfield.json").read_text()
					)["fields"]
				}
			)
			field.__dict__.update(
				source, parent=name, doctype="DocField", meta=field_meta, flags=frappe._dict()
			)
			meta.fields.append(field)
		meta._fields = {field.fieldname: field for field in meta.fields}
		return meta

	def document(self, doctype, is_return=0):
		meta = self.get_meta(doctype)
		child = meta.get_field("items").options
		row = PrintDocument.__new__(PrintDocument)
		row.__dict__.update(
			doctype=child,
			name="MEMORY-ROW",
			parent="MEMORY-DOCUMENT",
			parenttype=doctype,
			meta=self.get_meta(child),
			flags=frappe._dict(),
			idx=1,
			item_name="Prodotto Example",
			item_code="ITEM-EXAMPLE",
			description="Descrizione Example",
			uom="Nos",
			stock_uom="Nos",
			qty=12.5,
			rate=12.5,
			amount=156.25,
			warehouse="Warehouse Example",
		)
		doc = PrintDocument.__new__(PrintDocument)
		doc.__dict__.update(
			doctype=doctype,
			name="MEMORY-DOCUMENT",
			meta=meta,
			flags=frappe._dict(),
			items=[row],
			total=156.25,
			grand_total=156.25,
			customer="Cliente Example",
			customer_name="Cliente Example",
			supplier="Fornitore Example",
			supplier_name="Fornitore Example",
			company="Azienda Example",
			address_display="Via Example",
			docstatus=1,
			transaction_date="2026-10-21",
			posting_date="2026-10-21",
			is_return=is_return,
			taxes=[],
			print_templates=None,
			child_print_templates=None,
		)
		return doc

	def generator(self, data, is_return=0):
		generator = PrintFormatGenerator.__new__(PrintFormatGenerator)
		generator.doc = self.document(data["doc_type"], is_return)
		generator.print_settings = frappe._dict(print_uom_after_quantity=0)
		generator._logged_conditions = set()
		generator.print_format = frappe._dict(deepcopy(data), flags=frappe._dict())
		generator.context = frappe._dict()
		generator.style_name = None
		return generator

	def render(self, field, doc, restricted=True, lang="it"):
		frappe.local.lang = lang
		for key in ("jenv_restricted", "jenv_unrestricted"):
			setattr(frappe.local, key, None)
		env = frappe.get_jenv(restrict_globals=restricted)
		return str(
			env.get_template("templates/print_format/macros.html").module.render_field(
				frappe._dict(field), doc
			)
		)

	def test_all_standard_html_headings_translate_without_changing_business_values(self):
		formats = standard_formats()
		self.assertEqual(len(formats), 32)
		rendered_fields = 0
		for data in formats:
			generator = self.generator(data)
			source = deepcopy(data)
			layout = generator.get_layout(generator.print_format)
			state = deepcopy(
				(
					generator.doc.name,
					generator.doc.company,
					generator.doc.items[0].qty,
					generator.doc.items[0].rate,
					generator.doc.items[0].amount,
				)
			)
			for field in fields_in(layout):
				if field.get("fieldtype") != "HTML":
					continue
				for restricted in (True, False):
					english = self.render(field, generator.doc, restricted, "en")
					italian = self.render(field, generator.doc, restricted)
					expected = []
					for value in TextNodes(english).values:
						word = value.rstrip(":")
						expected.append(TRANSLATIONS.get(word, word) + (":" if value.endswith(":") else ""))
					with self.subTest(
						format=data["name"], field=field.get("fieldname"), restricted=restricted
					):
						self.assertEqual(TextNodes(italian).values, expected)
						self.assertEqual(re.sub(r">[^<]*<", "><", italian), re.sub(r">[^<]*<", "><", english))
					rendered_fields += 1
			self.assertEqual(source, data)
			self.assertEqual(
				state,
				(
					generator.doc.name,
					generator.doc.company,
					generator.doc.items[0].qty,
					generator.doc.items[0].rate,
					generator.doc.items[0].amount,
				),
			)
		self.assertGreaterEqual(rendered_fields, 94)

	def test_all_standard_row_headers_mean_number_and_keep_quantity_and_subtotal(self):
		for data in standard_formats():
			generator = self.generator(data)
			layout = generator.get_layout(generator.print_format)
			table = next(field for field in fields_in(layout) if field.get("fieldtype") == "Table")
			for restricted in (True, False):
				with self.subTest(format=data["name"], restricted=restricted):
					html = self.render(table, generator.doc, restricted)
					first_header = re.search(r"<th\b[^>]*>(.*?)</th>", html, re.S).group(1)
					self.assertEqual(TextNodes(first_header).values, ["Num."])
					self.assertIn("12.5", html)
					if any(column.get("fieldname") == "amount" for column in table["table_columns"]):
						self.assertIn("156.25", html)
					self.assertEqual(generator.doc.items[0].amount, 156.25)
					subtotal = next(
						(field for field in fields_in(layout) if field.get("label") == "Sub Total"), None
					)
					if subtotal:
						rendered = self.render(subtotal, generator.doc, restricted)
						self.assertIn("Subtotale", rendered)
						self.assertIn("156.25", rendered)

	def test_six_conditional_invoice_titles_keep_both_return_branches(self):
		found = 0
		for data in standard_formats():
			generator = self.generator(data)
			fields = fields_in(generator.get_layout(generator.print_format))
			conditional = [field for field in fields if "doc.is_return" in (field.get("html") or "")]
			for field in conditional:
				found += 1
				for is_return in (0, 1):
					generator.doc.is_return = is_return
					english_title = (
						("Debit Note" if data["doc_type"] == "Purchase Invoice" else "Credit Note")
						if is_return
						else data["doc_type"]
					)
					for restricted in (True, False):
						with self.subTest(format=data["name"], is_return=is_return, restricted=restricted):
							html = self.render(field, generator.doc, restricted)
							self.assertIn(TRANSLATIONS[english_title], TextNodes(html).values)
							self.assertEqual(generator.doc.is_return, is_return)
		self.assertEqual(found, 6)

	def test_authored_html_custom_row_caption_and_false_checkbox_remain_distinct(self):
		generator = self.generator(
			next(data for data in standard_formats() if data["name"] == "Sales Order Classic")
		)
		fields = fields_in(generator.get_layout(generator.print_format))
		table = deepcopy(next(field for field in fields if field.get("fieldtype") == "Table"))
		table["table_columns"][0]["label"] = "No"
		custom = frappe._dict(fieldtype="HTML", renderer="HTML", html="<b>Billed To &amp; Company</b>")
		checkbox = frappe._dict(
			fieldname="is_return", fieldtype="Check", renderer="Check", label="Return", show_empty=1
		)
		for restricted in (True, False):
			html = self.render(custom, generator.doc, restricted)
			self.assertIn("<b>Billed To &amp; Company</b>", html)
			self.assertNotIn("Dati di fatturazione", html)
			html = self.render(table, generator.doc, restricted)
			first_header = re.search(r"<th\b[^>]*>(.*?)</th>", html, re.S).group(1)
			self.assertEqual(TextNodes(first_header).values, ["No"])
			self.assertIn(">No<", self.render(checkbox, generator.doc, restricted))
