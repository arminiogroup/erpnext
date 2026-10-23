# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.document import Document
from frappe.model.meta import Meta

from erpnext.buying.doctype.supplier_quotation.supplier_quotation import SupplierQuotation


class TestRFQMissingSupplierCaption(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"Supplier {0} not found in {1}": "Il fornitore {0} non è presente in {1}.",
			"Request for Quotation": "Richiesta di offerta",
			"Customer": "Cliente",
			"Supplier": "Fornitore",
		}
		for name, value in {
			"session": frappe._dict(user="translator@example.com", data=frappe._dict()),
			"lang": "it",
			"response": frappe._dict(),
			"message_log": [],
		}.items():
			self.enterContext(patch.object(frappe.local, name, value, create=True))
		self.enterContext(
			patch.object(frappe.local, "conf", frappe._dict({**frappe.local.conf, "db_type": "mariadb"}))
		)
		self.enterContext(
			patch.object(
				frappe, "flags", frappe._dict(mute_messages=False, print_messages=False, in_migrate=False)
			)
		)
		self.enterContext(patch.object(frappe, "in_test", False, create=True))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.messages if lang == "it" else {},
			)
		)
		db = SimpleNamespace(
			db_type="mariadb", sql=self.sql, get_value=self.value, get_all=self.db_rows, exists=self.exists
		)
		self.enterContext(patch.object(frappe, "db", db))
		self.enterContext(patch.object(frappe.local, "db", db, create=True))
		self.enterContext(patch("frappe.get_all", side_effect=self.rows))
		self.enterContext(patch("frappe.get_cached_value", side_effect=self.cached))
		self.enterContext(
			patch(
				"frappe.get_system_settings",
				side_effect=lambda field: "Banker's Rounding" if field == "rounding_method" else None,
			)
		)
		self.enterContext(
			patch(
				"frappe.utils.data.get_url",
				side_effect=lambda uri=None, *args, **kwargs: "http://memory.invalid" + (uri or ""),
			)
		)
		for name in ("get_doc", "get_cached_doc", "new_doc", "sendmail", "enqueue", "delete_doc"):
			self.enterContext(
				patch("frappe." + name, side_effect=AssertionError("Forbidden boundary " + name))
			)

	def read(self, kind, args, kwargs, result):
		self.events.append((kind, deepcopy(args), deepcopy(kwargs)))
		return deepcopy(result)

	def sql(self, *args, **kwargs):
		return self.read("sql", args, kwargs, self.state.get("sql_rows", []))

	def value(self, *args, **kwargs):
		return self.read("value", args, kwargs, self.state.get("value"))

	def db_rows(self, *args, **kwargs):
		return self.read("db_rows", args, kwargs, self.state.get("db_rows", []))

	def rows(self, *args, **kwargs):
		return self.read("rows", args, kwargs, self.state.get("rows", []))

	def cached(self, *args, **kwargs):
		return self.read("cached", args, kwargs, self.state.get("cached"))

	def exists(self, *args, **kwargs):
		return self.read("exists", args, kwargs, self.state.get("exists", False))

	def document(self, **fields):
		doc = Document.__new__(Document)
		doc.__dict__.update(fields)
		return doc

	def error(self, callback, exception=frappe.ValidationError):
		with self.assertRaises(exception) as raised:
			callback()
		return str(raised.exception)

	def item(self):
		obj = SupplierQuotation.__new__(SupplierQuotation)
		obj.__dict__.update(
			doctype="Supplier Quotation",
			name="Supplier",
			supplier="Customer",
			items=[frappe._dict(request_for_quotation="Supplier", request_for_quotation_item="RAW-ITEM")],
		)
		return obj

	def test_only_link_caption_changes_with_canonical_rfq_and_supplier(self):
		self.enterContext(
			patch(
				"frappe.get_doc",
				side_effect=lambda kind, name: self.read(
					"get_doc", (kind, name), {}, SimpleNamespace(name=name, items=[])
				),
			)
		)
		for lang in ("it", "en"):
			frappe.local.lang = lang
			obj = self.item()
			before = deepcopy(obj.__dict__)
			caption = "Richiesta di offerta" if lang == "it" else "Request for Quotation"
			link = f"<a href='desk/app/Form/Request for Quotation/Supplier'> {caption} Supplier </a>"
			expected = (
				"Il fornitore Customer non è presente in " + link + "."
				if lang == "it"
				else "Supplier Customer not found in " + link
			)
			self.assertEqual(self.error(lambda: obj.update_rfq_supplier_status(False)), expected)
			self.assertEqual(obj.__dict__, before)
			self.assertEqual(
				self.events[-2:],
				[
					("get_doc", ("Request for Quotation", "Supplier"), {}),
					(
						"rows",
						("Request for Quotation Supplier",),
						{
							"filters": {"parent": "Supplier", "supplier": "Customer"},
							"fields": ["name", "quote_status"],
						},
					),
				],
			)

	def test_matching_supplier_without_items_performs_no_write(self):
		self.enterContext(patch("frappe.get_doc", return_value=SimpleNamespace(name="Supplier", items=[])))
		self.state["rows"] = [frappe._dict(name="RAW-SUPPLIER-ROW", quote_status="Received")]
		self.assertIsNone(self.item().update_rfq_supplier_status(False))
