# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.controllers import trends

FRAME = "{0} can be either {1} or {2}."
MESSAGES = {
	FRAME: "{0} può essere {1} o {2}.",
	"Period based On": "Periodo basato su",
	"Posting Date": "Data di registrazione",
	"Billing Date": "Data della fattura",
	"Invalid Filter": "Filtro non valido",
}


class TestTrendFilterLabels(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.messages = dict(MESSAGES)
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.messages if lang == "it" else {},
			)
		)
		self.reads = []
		self.exists = True
		self.enterContext(patch.object(frappe.local, "db", SimpleNamespace(exists=self._exists), create=True))
		for name in ("get_doc", "get_all", "new_doc", "delete_doc", "sendmail", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))

	def _exists(self, *args, **kwargs):
		self.assertEqual(kwargs, {})
		self.reads.append(args)
		self.assertEqual(args, ("Fiscal Year", "2026"))
		return self.exists

	def _filters(self, **values):
		return frappe._dict(
			{
				"fiscal_year": "2026",
				"company": "COMPANY-EXAMPLE",
				"based_on": "Item",
				"group_by": "Supplier",
				"period": "Monthly",
				"period_based_on": "invalid",
				**values,
			}
		)

	def _validate(self, filters):
		before = deepcopy(filters)
		try:
			trends.validate_filters(filters)
		except frappe.ValidationError as error:
			return str(error)
		finally:
			self.assertEqual(filters, before)
		return None

	def test_invalid_period_uses_actual_report_filter_captions_and_title(self):
		self.assertEqual(
			self._validate(self._filters()),
			"<strong>Periodo basato su</strong> può essere <strong>Data di registrazione</strong> "
			"o <strong>Data della fattura</strong>.",
		)
		self.assertEqual(self.reads, [("Fiscal Year", "2026")])
		self.assertEqual(frappe.local.message_log[-1]["title"], "Filtro non valido")
		path = (
			Path(frappe.get_app_path("erpnext"))
			/ "accounts/report/purchase_invoice_trends/purchase_invoice_trends.js"
		)
		javascript = path.read_text()
		for label in ("Period based On", "Posting Date", "Billing Date"):
			self.assertIn(f'__("{label}")', javascript)
		self.assertIn('value: "posting_date"', javascript)
		self.assertIn('value: "bill_date"', javascript)

	def test_valid_canonical_values_and_absent_period_do_not_read_more_data(self):
		for value in ("posting_date", "bill_date", "", None):
			with self.subTest(value=value):
				self.reads.clear()
				self.assertIsNone(self._validate(self._filters(period_based_on=value)))
				self.assertEqual(self.reads, [("Fiscal Year", "2026")])

	def test_unknown_fiscal_year_keeps_first_error_and_read_order(self):
		self.messages.clear()
		self.exists = False
		self.assertEqual(self._validate(self._filters()), "Fiscal Year 2026 does not exist")
		self.assertEqual(self.reads, [("Fiscal Year", "2026")])

	def test_missing_fiscal_year_after_default_lookup_keeps_mandatory_guard(self):
		self.messages.clear()
		with (
			patch.object(trends, "today", return_value="2026-10-21") as clock,
			patch.object(trends, "get_fiscal_year", return_value=("",)) as fiscal_year,
		):
			self.assertEqual(self._validate(self._filters(fiscal_year="")), "Fiscal Year is mandatory")
			clock.assert_called_once_with()
			fiscal_year.assert_called_once_with("2026-10-21")
		self.assertEqual(self.reads, [])

	def test_missing_mandatory_field_keeps_priority_without_database_read(self):
		self.messages.clear()
		self.assertEqual(self._validate(self._filters(based_on="")), "Based On is mandatory")
		self.assertEqual(self.reads, [])

	def test_matching_based_on_and_group_by_keeps_priority(self):
		self.messages.clear()
		self.assertEqual(
			self._validate(self._filters(group_by="Item")),
			"'Based On' and 'Group By' can not be the same",
		)
		self.assertEqual(self.reads, [("Fiscal Year", "2026")])

	def test_english_and_empty_caption_targets_keep_original_literals(self):
		for provider in ({}, {"Period based On": "", "Posting Date": "", "Billing Date": ""}):
			with self.subTest(provider=provider):
				self.messages = provider
				self.assertEqual(
					self._validate(self._filters()),
					"<strong>Period based On</strong> can be either <strong>Posting Date</strong> "
					"or <strong>Billing Date</strong>.",
				)
