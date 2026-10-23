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

from erpnext.regional.report.vat_audit_report.vat_audit_report import VATAuditReport


class TestVATSettingsLinkCaption(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"Please set VAT Accounts in {0}": "Configurare i conti IVA in {0}.",
			"South Africa VAT Settings": "Impostazioni IVA Sudafrica",
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

	def test_missing_configuration_has_translated_link_caption_same_route(self):
		reads = []
		for lang in ("it", "en"):
			frappe.local.lang = lang
			self.events.clear()
			obj = VATAuditReport({"company": "Customer"})
			actual = self.error(obj.get_sa_vat_accounts)
			self.assertIn('href="http://memory.invalid/desk/south-africa-vat-settings', actual)
			self.assertIn(
				"Impostazioni IVA Sudafrica" if lang == "it" else ">South Africa VAT Settings</a>", actual
			)
			self.assertTrue(
				actual.startswith(
					"Configurare i conti IVA in " if lang == "it" else "Please set VAT Accounts in "
				)
			)
			reads.append(deepcopy(self.events))
		self.assertEqual(reads[0], reads[1])
		self.assertEqual(
			reads[0],
			[
				(
					"rows",
					("South Africa VAT Account",),
					{"filters": {"parent": "Customer"}, "pluck": "account"},
				)
			],
		)

	def test_accounts_and_test_migration_suppression_keep_existing_contract(self):
		obj = VATAuditReport({"company": "Customer"})
		self.state["rows"] = ["Customer"]
		obj.get_sa_vat_accounts()
		self.assertEqual(obj.sa_vat_accounts, ["Customer"])
		self.state["rows"] = []
		with patch.object(frappe, "in_test", True):
			obj.get_sa_vat_accounts()
		frappe.flags.in_migrate = True
		obj.get_sa_vat_accounts()
		self.assertEqual(obj.sa_vat_accounts, [])
