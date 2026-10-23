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

from erpnext.accounts.doctype.financial_report_template.financial_report_engine import FinancialReportEngine


class TestFinancialReportViewCaption(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"The selected view is currently unsupported in Custom Financial Report: {0}.": "La vista selezionata non è attualmente supportata nei report finanziari personalizzati: {0}.",
			"Margin View": "Vista dei margini",
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

	def filters(self, view):
		return {
			"report_template": "Customer",
			"filter_based_on": "Date Range",
			"period_start_date": "2026-01-01",
			"period_end_date": "2026-12-31",
			"selected_view": view,
		}

	def test_only_known_margin_uses_its_visible_caption(self):
		obj = FinancialReportEngine.__new__(FinancialReportEngine)
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for value in ("Margin", "Customer", "Unknown View"):
				with self.subTest(lang=lang, value=value):
					filters = self.filters(value)
					before = deepcopy(filters)
					frappe.local.message_log.clear()
					obj._validate_filters(filters)
					caption = (
						"Vista dei margini"
						if lang == "it" and value == "Margin"
						else "Margin View"
						if value == "Margin"
						else value
					)
					expected = (
						f"La vista selezionata non è attualmente supportata nei report finanziari personalizzati: {caption}."
						if lang == "it"
						else f"The selected view is currently unsupported in Custom Financial Report: {caption}."
					)
					self.assertEqual(frappe.local.message_log[-1]["message"], expected)
					self.assertEqual(frappe.local.message_log[-1]["indicator"], "orange")
					self.assertEqual(filters, before)
					self.assertEqual(self.events, [])

	def test_supported_and_empty_views_emit_no_warning(self):
		obj = FinancialReportEngine.__new__(FinancialReportEngine)
		for value in ("Report", "Growth", "", None):
			frappe.local.message_log.clear()
			obj._validate_filters(self.filters(value))
			self.assertEqual(frappe.local.message_log, [])

	def test_existing_required_filter_validation_still_precedes_view_warning(self):
		obj = FinancialReportEngine.__new__(FinancialReportEngine)
		value = self.filters("Margin")
		value.pop("report_template")
		self.assertIn("Missing required filter", self.error(lambda: obj._validate_filters(value)))
