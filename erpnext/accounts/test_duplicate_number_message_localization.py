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

from erpnext.accounts.utils import validate_field_number


class TestDuplicateNumberMessage(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"Number {0} is already used in {1} {2}.": "Il numero {0} è già utilizzato: {1} {2}.",
			"Cost Center": "Centro di costo",
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

	def test_duplicate_message_keeps_numbers_and_names_raw(self):
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for kind in ("Cost Center", "Custom Tree"):
				for company in ("Supplier", None):
					with self.subTest(lang=lang, kind=kind, company=company):
						self.state["value"] = "Customer"
						self.events.clear()
						expected = (
							f"Il numero Customer è già utilizzato: {self.messages.get(kind, kind)} Customer."
							if lang == "it"
							else f"Number Customer is already used in {kind} Customer."
						)
						self.assertEqual(
							self.error(
								lambda: validate_field_number(
									kind, "Supplier", "Customer", company, "cost_center_number"
								)
							),
							expected,
						)
						filters = {"cost_center_number": "Customer", "name": ["!=", "Supplier"]}
						if company:
							filters["company"] = company
						self.assertEqual(self.events, [("value", (kind, filters), {})])

	def test_unused_or_absent_numbers_keep_original_read_and_return_contract(self):
		for number in (None, "", 0):
			self.events.clear()
			self.assertIsNone(
				validate_field_number("Cost Center", "Supplier", number, "Company", "cost_center_number")
			)
			self.assertEqual(self.events, [])
		self.state["value"] = None
		self.assertIsNone(
			validate_field_number("Cost Center", "Supplier", "Customer", None, "cost_center_number")
		)
