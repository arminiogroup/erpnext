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

from erpnext.accounts.services.taxes import TaxService


class TestDisabledTaxTemplateDisplay(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"{0} '{1}' is disabled": "Il record '{1}' di tipo {0} è disabilitato.",
			"Sales Taxes and Charges Template": "Modello di imposte e oneri di vendita",
			"Purchase Taxes and Charges Template": "Modello di imposte e oneri di acquisto",
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

	def test_disabled_types_translate_but_template_names_and_reads_stay_raw(self):
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for kind in (
				"Sales Taxes and Charges Template",
				"Purchase Taxes and Charges Template",
				"Custom Tax Template",
			):
				with self.subTest(lang=lang, kind=kind):
					doc = self.document(
						doctype="Sales Invoice",
						taxes_and_charges="Customer",
						meta=SimpleNamespace(get_options=lambda field: kind),
					)
					self.state["cached"] = 1
					self.events.clear()
					expected = (
						("Il record 'Customer' di tipo " + self.messages.get(kind, kind) + " è disabilitato.")
						if lang == "it"
						else f"{kind} 'Customer' is disabled"
					)
					self.assertEqual(
						self.error(lambda: TaxService(doc).validate_enabled_taxes_and_charges()), expected
					)
					self.assertEqual(self.events, [("cached", (kind, "Customer", "disabled"), {})])
					self.assertEqual(doc.taxes_and_charges, "Customer")

	def test_enabled_or_empty_template_never_rejects(self):
		doc = self.document(
			taxes_and_charges="Customer",
			meta=SimpleNamespace(get_options=lambda field: "Sales Taxes and Charges Template"),
		)
		self.state["cached"] = 0
		TaxService(doc).validate_enabled_taxes_and_charges()
		doc.taxes_and_charges = ""
		self.events.clear()
		TaxService(doc).validate_enabled_taxes_and_charges()
		self.assertEqual(self.events, [])
