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

from erpnext.accounts.services.billing_validation import BillingValidationService


class TestZeroBillingReferenceMessage(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.state = {}
		self.messages = {
			"The overbilling check is skipped for Item {0} because the reference value in {1} is zero.": "Il controllo della sovrafatturazione viene saltato per l'articolo {0}: il valore di riferimento ricavato da {1} è zero.",
			"Purchase Receipt": "Ricevimento merci",
			"Customer": "Cliente",
			"Warning": "Avviso",
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

	def reference_rows(self, *args, **kwargs):
		row = frappe._dict(
			name="RAW-REF",
			qty=self.state["reference"],
			amount=self.state["reference"],
			conversion_factor=self.state.get("reference_factor", 1),
		)
		return self.read("rows", args, kwargs, [row])

	def invoice(self, basis, reference, current):
		self.state["reference"] = reference
		row = frappe._dict(
			doctype="Purchase Invoice Item",
			idx=1,
			item_code="Customer",
			reference="RAW-REF",
			amount=current,
			qty=current,
			conversion_factor=1,
			uom="Kg",
		)
		item = self.document(doctype="Purchase Invoice", name="Supplier", items=[row])
		item.precision = lambda field, parent: 2
		return item

	def test_amount_and_quantity_zero_references_have_truthful_type_aware_warning(self):
		self.enterContext(patch("frappe.get_all", side_effect=self.reference_rows))
		reads = []
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for basis in ("amount", "qty"):
				with self.subTest(lang=lang, basis=basis):
					self.events.clear()
					frappe.local.message_log.clear()
					item = self.invoice(basis, 0, 2)
					before = deepcopy(item.items)
					result = BillingValidationService(item).get_reference_wise_billed_amt(
						"Purchase Receipt", "reference", basis, basis, None
					)
					reads.append(deepcopy(self.events))
					expected = (
						"Il controllo della sovrafatturazione viene saltato per l'articolo Customer: il valore di riferimento ricavato da Ricevimento merci è zero."
						if lang == "it"
						else "The overbilling check is skipped for Item Customer because the reference value in Purchase Receipt is zero."
					)
					self.assertEqual(frappe.local.message_log[-1]["message"], expected)
					self.assertEqual(frappe.local.message_log[-1]["indicator"], "orange")
					self.assertEqual(result, {})
					self.assertEqual(item.items, before)
					self.assertEqual(self.events[0][1][0], "Purchase Receipt Item")
		self.assertEqual(reads[0], reads[2])
		self.assertEqual(reads[1], reads[3])

	def test_nonzero_and_zero_current_controls_keep_reference_math(self):
		self.enterContext(patch("frappe.get_all", side_effect=self.reference_rows))
		for basis in ("amount", "qty"):
			frappe.local.message_log.clear()
			item = self.invoice(basis, 10, 2)
			result = BillingValidationService(item).get_reference_wise_billed_amt(
				"Purchase Receipt", "reference", basis, basis, None
			)
			self.assertEqual(result["RAW-REF"]["ref_amt"], 10)
			self.assertEqual(result["RAW-REF"]["billed_amt"], 2)
			self.assertEqual(result["RAW-REF"]["rows"], [1])
			self.assertEqual(result["RAW-REF"]["uom"], "Kg")
			item = self.invoice(basis, 0, 0)
			self.assertEqual(
				BillingValidationService(item).get_reference_wise_billed_amt(
					"Purchase Receipt", "reference", basis, basis, None
				),
				{},
			)
			self.assertEqual(frappe.local.message_log, [])

	def test_quantity_conversion_and_prior_billing_keep_existing_numeric_contract(self):
		self.enterContext(patch("frappe.get_all", side_effect=self.reference_rows))
		item = self.invoice("qty", 10.123, 2)
		item.items[0].conversion_factor = 4
		self.state.update(reference_factor=2, sql_rows=[("RAW-REF", 6)])
		before = deepcopy(item.items)
		result = BillingValidationService(item).get_reference_wise_billed_amt(
			"Purchase Receipt", "reference", "qty", "qty", None
		)
		self.assertEqual(result["RAW-REF"]["ref_amt"], 10.12)
		self.assertEqual(result["RAW-REF"]["billed_amt"], 7)
		self.assertEqual(item.items, before)
		self.assertEqual(frappe.local.message_log, [])
