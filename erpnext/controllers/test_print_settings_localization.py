import unittest
from pathlib import Path
from unittest.mock import patch

import frappe
from jinja2 import Environment, FileSystemLoader

from erpnext.controllers.print_settings import set_print_templates_for_item_table


class TestItemQuantityLocalization(unittest.TestCase):
	def setUp(self):
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.lang = "it"
		self.template_path = "templates/print_formats/includes/item_table_qty.html"
		self.env = Environment(loader=FileSystemLoader(Path(frappe.get_app_path("erpnext"))))
		self.env.globals["_"] = frappe._

	def render(self, *, qty_first, uom="Set", stock_uom="Minute", hidden=(), translations=None):
		formatted_calls = []
		doc = frappe._dict(uom=uom, stock_uom=stock_uom, qty=2.5, flags=frappe._dict())
		doc.get_formatted = lambda field, row: formatted_calls.append((field, row)) or "2,500"
		doc.is_print_hide = lambda field: field in hidden
		settings = frappe._dict(print_uom_after_quantity=qty_first, compact_item_print=0)
		set_print_templates_for_item_table(doc, settings)
		self.assertEqual(doc.child_print_templates["items"]["qty"], self.template_path)
		with patch("frappe.translate.get_all_translations", return_value=translations or {}):
			html = self.env.get_template(doc.child_print_templates["items"]["qty"]).render(
				doc=doc, print_settings=settings
			)
		self.assertEqual(len(formatted_calls), 1)
		self.assertEqual(formatted_calls[0][0], "qty")
		self.assertIs(formatted_calls[0][1], doc)
		self.assertEqual((doc.uom, doc.stock_uom, doc.qty), (uom, stock_uom, 2.5))
		return html

	def test_primary_and_stock_unit_use_uom_context_in_both_quantity_orders(self):
		translations = {"Set": "Imposta", "Set:UOM": "Insieme", "Minute": "Verbale", "Minute:UOM": "Minuto"}
		for qty_first in (False, True):
			for uom, hidden, expected in (
				("Set", (), "Insieme"),
				("", (), "Minuto"),
				("Set", ("uom",), "Minuto"),
			):
				with self.subTest(qty_first=qty_first, uom=uom, hidden=hidden):
					html = self.render(qty_first=qty_first, uom=uom, hidden=hidden, translations=translations)
					self.assertIn(expected, html)
					self.assertNotIn("Imposta", html)
					self.assertNotIn("Verbale", html)
					if qty_first:
						self.assertLess(html.index("2,500"), html.index(expected))
						self.assertNotIn("<small", html)
					else:
						self.assertLess(html.index(expected), html.index("2,500"))
						self.assertIn(f'<small class="pull-left">{expected}</small>', html)

	def test_hidden_units_stay_hidden_and_do_not_change_quantity(self):
		for qty_first in (False, True):
			with self.subTest(qty_first=qty_first):
				html = self.render(qty_first=qty_first, hidden=("uom", "stock_uom"))
				self.assertEqual(html.strip(), "2,500")
				self.assertNotIn("<small", html)

	def test_units_retain_global_and_source_fallbacks(self):
		for qty_first in (False, True):
			for unit, translations, expected in (
				("Global unit", {"Global unit": "Unità globale"}, "Unità globale"),
				("Supplier unit", {}, "Supplier unit"),
			):
				with self.subTest(qty_first=qty_first, unit=unit):
					html = self.render(qty_first=qty_first, uom=unit, translations=translations)
					self.assertIn(expected, html)
