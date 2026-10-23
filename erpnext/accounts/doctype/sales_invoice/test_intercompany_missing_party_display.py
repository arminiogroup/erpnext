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

from erpnext.accounts.doctype.sales_invoice.mapper import validate_inter_company_transaction


class TestIntercompanyMissingPartyDisplay(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"No {0} found for Inter Company Transactions.": "Nessun record di tipo {0} trovato per le transazioni tra aziende.",
			"Supplier": "Fornitore",
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

	def item(self, kind):
		item = self.document(
			doctype=kind,
			company="Supplier",
			customer="Customer",
			supplier="Supplier",
			selling_price_list="Customer",
			buying_price_list="Customer",
		)
		item.is_internal_transfer = lambda: False
		return item

	def test_only_malformed_provider_boundary_reaches_late_display_guard(self):
		self.state.update(db_rows=[frappe._dict(name=None)], value="Customer", cached="Supplier")
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for kind in (
				"Sales Invoice",
				"Sales Order",
				"Purchase Invoice",
				"Purchase Order",
				"Delivery Note",
			):
				with self.subTest(lang=lang, kind=kind):
					self.events.clear()
					item = self.item(kind)
					expected_type = "Supplier" if kind in ("Sales Invoice", "Sales Order") else "Customer"
					expected = (
						f"Nessun record di tipo {self.messages[expected_type]} trovato per le transazioni tra aziende."
						if lang == "it"
						else f"No {expected_type} found for Inter Company Transactions."
					)
					self.assertEqual(
						self.error(lambda: validate_inter_company_transaction(item, kind)), expected
					)
					self.assertEqual([x[0] for x in self.events], ["db_rows", "cached", "value"])
					self.assertEqual(item.company, "Supplier")
					self.assertEqual(item.customer, "Customer")
					self.assertEqual(item.supplier, "Supplier")

	def test_valid_rows_and_earlier_missing_rows_keep_actual_flow(self):
		self.state.update(value="Customer", cached="Supplier")
		for kind in ("Sales Invoice", "Sales Order", "Purchase Invoice", "Purchase Order", "Delivery Note"):
			with self.subTest(kind=kind):
				self.state["db_rows"] = [frappe._dict(name="Customer")]
				self.assertIsNone(validate_inter_company_transaction(self.item(kind), kind))
				self.state["db_rows"] = []
				self.events.clear()
				expected_type = (
					"Supplier" if kind in ("Sales Invoice", "Sales Order", "Delivery Note") else "Customer"
				)
				self.assertEqual(
					self.error(lambda: validate_inter_company_transaction(self.item(kind), kind)),
					f"No {expected_type} found for Inter Company Transactions which represents company <strong>Supplier</strong>",
				)
				self.assertEqual([x[0] for x in self.events], ["db_rows", "cached"])
