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

from erpnext.accounts.report.profit_and_loss_statement.profit_and_loss_statement import (
	execute_snapshot_report as profit_snapshot,
)
from erpnext.accounts.report.trial_balance.trial_balance import execute_snapshot_report as trial_snapshot


class TestSnapshotSyncCaption(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"Profit and Loss Statement requires {0} to be synced to DuckDB": "Per il conto economico è necessario sincronizzare in DuckDB i dati di tipo {0}.",
			"Trial Balance requires {0} to be synced to DuckDB": "Per il bilancio di verifica è necessario sincronizzare in DuckDB i dati di tipo {0}.",
			"GL Entry": "Registrazione contabile",
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

	def test_missing_snapshot_translates_only_the_dataset_caption(self):
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for fn, source, target in [
				(profit_snapshot, "Profit and Loss Statement", "conto economico"),
				(trial_snapshot, "Trial Balance", "bilancio di verifica"),
			]:
				with self.subTest(lang=lang, report=source):
					with patch("frappe.database.duckdb.database.get_latest_sync", return_value=None) as read:
						actual = self.error(lambda: fn(frappe._dict()))
					expected = (
						f"Per il {target} è necessario sincronizzare in DuckDB i dati di tipo <strong>Registrazione contabile</strong>."
						if lang == "it"
						else f"{source} requires <strong>GL Entry</strong> to be synced to DuckDB"
					)
					self.assertEqual(actual, expected)
					read.assert_called_once_with("GL Entry")
					self.assertEqual(self.events, [])

	def test_missing_dictionary_target_preserves_source_fallback(self):
		self.messages.pop("GL Entry")
		with patch("frappe.database.duckdb.database.get_latest_sync", return_value=None):
			self.assertIn("<strong>GL Entry</strong>", self.error(lambda: trial_snapshot(frappe._dict())))
