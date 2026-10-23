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

from erpnext.regional.italy.setup import get_custom_fields
from erpnext.regional.italy.utils import validate_address


class TestItalyAddressFieldLabels(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"Please set {0} for address {1}": "Compilare {0} nell'indirizzo {1}.",
			"Postal Code": "Codice postale",
			"City/Town": "Città/località",
			"Country Code": "Codice del paese",
			"Customer": "Cliente",
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

	def address_meta(self):
		payload = json.loads(
			(Path(frappe.__file__).parent / "contacts/doctype/address/address.json").read_text()
		)
		fields = payload["fields"] + get_custom_fields()["Address"]
		meta = Meta.__new__(Meta)
		meta.__dict__.update(
			name="Address", _fields={x["fieldname"]: frappe._dict(deepcopy(x)) for x in fields}
		)
		return meta

	def test_each_required_field_uses_its_owning_meta_caption(self):
		meta = self.address_meta()
		self.enterContext(
			patch("frappe.get_meta", side_effect=lambda kind: self.read("meta", (kind,), {}, meta))
		)
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for field in ("pincode", "city", "country_code"):
				with self.subTest(lang=lang, field=field):
					values = dict.fromkeys(("pincode", "city", "country_code"), "Customer")
					values[field] = ""
					self.state["cached"] = values
					self.events.clear()
					caption = (
						self.messages.get(meta.get_label(field), meta.get_label(field))
						if lang == "it"
						else meta.get_label(field)
					)
					expected = (
						f"Compilare {caption} nell'indirizzo Customer."
						if lang == "it"
						else f"Please set {caption} for address Customer"
					)
					self.assertEqual(self.error(lambda: validate_address("Customer")), expected)
					self.assertEqual(
						self.events[0],
						(
							"cached",
							("Address", "Customer", ["pincode", "city", "country_code"]),
							{"as_dict": 1},
						),
					)
					self.assertEqual(self.events[1][:2], ("meta", ("Address",)))
					self.assertEqual(self.state["cached"], values)

	def test_complete_address_never_loads_meta_or_mutates_values(self):
		values = dict.fromkeys(("pincode", "city", "country_code"), "Customer")
		self.state["cached"] = values
		with patch("frappe.get_meta", side_effect=AssertionError("Unexpected Meta read")):
			self.assertIsNone(validate_address("Customer"))
		self.assertEqual(self.state["cached"], values)
