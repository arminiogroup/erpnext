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

from erpnext.accounts.doctype.accounting_period.accounting_period import (
	ClosedAccountingPeriod,
	validate_accounting_period_on_doc_save,
)


class TestAccountingPeriodSaveMessage(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"You cannot save a document of type {0} within the closed Accounting Period {1}.": "Non è possibile salvare documenti di tipo {0} nel periodo contabile chiuso {1}.",
			"Sales Invoice": "Fattura di vendita",
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

	def test_existing_document_save_rejects_with_translated_type_raw_period(self):
		item = self.document(
			doctype="Sales Invoice", name="Customer", company="Supplier", posting_date="2026-10-21"
		)
		self.state["sql_rows"] = [frappe._dict(name="Customer", exempted_role=None)]
		before = deepcopy(item.__dict__)
		reads = []
		for lang in ("it", "en"):
			frappe.local.lang = lang
			self.events.clear()
			expected = (
				"Non è possibile salvare documenti di tipo Fattura di vendita nel periodo contabile chiuso <strong>Customer</strong>."
				if lang == "it"
				else "You cannot save a document of type Sales Invoice within the closed Accounting Period <strong>Customer</strong>."
			)
			self.assertEqual(
				self.error(lambda: validate_accounting_period_on_doc_save(item), ClosedAccountingPeriod),
				expected,
			)
			self.assertEqual(item.__dict__, before)
			reads.append(deepcopy(self.events))
		self.assertEqual(reads[0], reads[1])
		self.assertIn("Sales Invoice", str(reads[0]))
		self.assertIn("2026-10-21", str(reads[0]))

	def test_open_period_and_exempt_role_keep_original_acceptance(self):
		item = self.document(doctype="Sales Invoice", company="Supplier", posting_date="2026-10-21")
		self.assertIsNone(validate_accounting_period_on_doc_save(item))
		self.state["sql_rows"] = [frappe._dict(name="Customer", exempted_role="Accounts Manager")]
		with patch("frappe.get_roles", return_value=["Accounts Manager"]):
			self.assertIsNone(validate_accounting_period_on_doc_save(item))

	def test_date_special_cases_and_early_exemptions_are_preserved(self):
		for fields in ({"doctype": "Bank Clearance"}, {"doctype": "Asset", "asset_type": "Existing Asset"}):
			self.events.clear()
			self.assertIsNone(validate_accounting_period_on_doc_save(self.document(**fields)))
			self.assertEqual(self.events, [])
		for kind, field in (
			("Asset", "available_for_use_date"),
			("Asset Repair", "completion_date"),
			("Period Closing Voucher", "period_end_date"),
		):
			self.events.clear()
			self.assertIsNone(
				validate_accounting_period_on_doc_save(
					self.document(
						doctype=kind, asset_type="New Asset", company="Supplier", **{field: "2026-10-21"}
					)
				)
			)
			self.assertIn("2026-10-21", str(self.events))
