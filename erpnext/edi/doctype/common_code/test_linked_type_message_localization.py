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

from erpnext.edi.doctype.common_code.common_code import CommonCode


class TestCommonCodeLinkedTypeDisplay(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"{0} {1} is already linked to Common Code {2}.": "Il record {1} di tipo {0} è già collegato al codice comune {2}.",
			"Item": "Articolo",
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

	def test_duplicate_type_translates_and_both_record_identities_stay_raw(self):
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for kind in ("Item", "Custom Document"):
				with self.subTest(lang=lang, kind=kind):
					obj = CommonCode.__new__(CommonCode)
					obj.__dict__.update(
						name="Supplier",
						code_list="RAW-CODE",
						applies_to=[frappe._dict(link_doctype=kind, link_name="Customer")],
					)
					before = deepcopy(obj.__dict__)
					self.state["rows"] = [{"name": "Customer", "common_code": "Supplier"}]
					expected = (
						f'Il record Customer di tipo {self.messages.get(kind,kind)} è già collegato al codice comune <a href="http://memory.invalid/desk/common-code/Customer">Supplier</a>.'
						if lang == "it"
						else f'{kind} Customer is already linked to Common Code <a href="http://memory.invalid/desk/common-code/Customer">Supplier</a>.'
					)
					self.assertEqual(self.error(obj.validate_distinct_references), expected)
					self.assertEqual(obj.__dict__, before)
					self.assertEqual(self.events[-1][1][0], "Common Code")
					self.assertIn(kind, str(self.events[-1]))
					self.assertIn("Customer", str(self.events[-1]))

	def test_distinct_reference_is_accepted_without_mutation(self):
		obj = CommonCode.__new__(CommonCode)
		obj.__dict__.update(
			name="Supplier",
			code_list="RAW-CODE",
			applies_to=[frappe._dict(link_doctype="Item", link_name="Customer")],
		)
		before = deepcopy(obj.__dict__)
		self.assertIsNone(obj.validate_distinct_references())
		self.assertEqual(obj.__dict__, before)
