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

import erpnext
from erpnext.accounts.doctype.repost_accounting_ledger.repost_accounting_ledger import (
	validate_docs_for_voucher_types,
)


class TestRepostDisallowedTypeMessage(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"Reposting is not allowed for: {0}. To allow it, add the document types to {1} in {2}.": "Ricontabilizzazione non consentita per: {0}. Per abilitarla, aggiungere i tipi di documento alla tabella {1} in {2}.",
			"Sales Invoice": "Fattura di vendita",
			"Purchase Invoice": "Fattura di acquisto",
			"Allowed DocTypes": "Tipi di documento consentiti",
			"Accounts Settings": "Impostazioni di contabilità",
			"and": "e",
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

	def settings_meta(self):
		payload = json.loads(
			(
				Path(erpnext.__file__).parent / "accounts/doctype/accounts_settings/accounts_settings.json"
			).read_text()
		)
		meta = Meta.__new__(Meta)
		meta.__dict__.update(
			name="Accounts Settings", _fields={x["fieldname"]: frappe._dict(x) for x in payload["fields"]}
		)
		return meta

	def test_single_and_multiple_types_use_whole_frame_and_actual_table_label(self):
		meta = self.settings_meta()
		self.enterContext(
			patch("frappe.get_meta", side_effect=lambda kind: self.read("meta", (kind,), {}, meta))
		)
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for types in (
				["Sales Invoice"],
				["Sales Invoice", "Purchase Invoice"],
				["Custom Voucher", "Sales Invoice"],
				["Sales Invoice", "Sales Invoice"],
			):
				with self.subTest(lang=lang, types=types):
					before = deepcopy(types)
					self.events.clear()
					actual = self.error(lambda: validate_docs_for_voucher_types(types))
					from frappe.utils.data import comma_and

					display = [self.messages.get(kind, kind) if lang == "it" else kind for kind in set(types)]
					caption = "Tipi di documento consentiti" if lang == "it" else "Allowed DocTypes"
					link = frappe.utils.get_link_to_form("Accounts Settings")
					expected = (
						f"Ricontabilizzazione non consentita per: {frappe.bold(comma_and(display))}. Per abilitarla, aggiungere i tipi di documento alla tabella {frappe.bold(caption)} in {link}."
						if lang == "it"
						else f"Reposting is not allowed for: {frappe.bold(comma_and(display))}. To allow it, add the document types to {frappe.bold(caption)} in {link}."
					)
					self.assertEqual(actual, expected)
					self.assertEqual(types, before)
					self.assertEqual(
						self.events[0],
						(
							"db_rows",
							("Repost Allowed Types",),
							{"fields": ["document_type"], "distinct": True},
						),
					)
					self.assertEqual(self.events[1][:2], ("meta", ("Accounts Settings",)))

	def test_empty_and_allowed_types_do_not_load_meta_or_start_work(self):
		self.state["db_rows"] = [frappe._dict(document_type="Sales Invoice")]
		with patch("frappe.get_meta", side_effect=AssertionError("Unexpected Meta read")):
			self.assertIsNone(validate_docs_for_voucher_types([]))
			self.assertIsNone(validate_docs_for_voucher_types(["Sales Invoice"]))
