# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils.formatters import format_value

from erpnext.accounts.doctype.account.account import Account

FRAME = "Only Parent can be of type {0}"
TARGETS = {
	FRAME: "Il conto non può avere lo stesso tipo del conto padre: {0}.",
	"Direct Income": "Ricavi diretti",
	"Indirect Income": "Ricavi indiretti",
	"Current Asset": "Attivo circolante",
	"Current Liability": "Passività correnti",
	"Direct Expense": "Costo diretto",
	"Indirect Expense": "Costo indiretto",
}
RESTRICTED_TYPES = [
	"Direct Income",
	"Indirect Income",
	"Current Asset",
	"Current Liability",
	"Direct Expense",
	"Indirect Expense",
]


class TestParentAccountTypeTranslation(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TARGETS if lang == "it" else {},
			)
		)
		self.events = []
		self.parent_type = None
		self.enterContext(patch.object(frappe, "db", SimpleNamespace(get_value=self.get_value)))
		for boundary in ("get_doc", "get_all", "get_cached_value", "new_doc", "sendmail", "enqueue"):
			self.enterContext(
				patch("frappe." + boundary, side_effect=AssertionError("Unexpected I/O: " + boundary))
			)
		data = json.loads(Path(__file__).with_name("account.json").read_text())
		self.field = frappe._dict(
			next(field for field in data["fields"] if field["fieldname"] == "account_type"),
			parent=data["name"],
		)

	def get_value(self, *args, **kwargs):
		self.events.append((args, kwargs))
		return self.parent_type

	def document(self, account_type, parent="Current Asset"):
		doc = Account.__new__(Account)
		doc.__dict__.update(doctype="Account", parent_account=parent, account_type=account_type)
		return doc

	def test_restricted_equal_parent_categories_match_existing_select_display(self):
		for account_type in RESTRICTED_TYPES:
			with self.subTest(account_type=account_type):
				self.events.clear()
				self.parent_type = account_type
				doc = self.document(account_type)
				before = deepcopy(doc.__dict__)
				with self.assertRaises(frappe.ValidationError) as caught:
					doc.validate_parent_child_account_type()
				self.assertEqual(str(caught.exception), TARGETS[FRAME].format(TARGETS[account_type]))
				self.assertEqual(
					format_value(account_type, self.field, translated=True), TARGETS[account_type]
				)
				self.assertIn(account_type, self.field.options.split("\n"))
				self.assertEqual(self.events, [(("Account", "Current Asset", ["account_type"]), {})])
				self.assertEqual(doc.__dict__, before)

	def test_account_context_precedes_conflicting_global_label(self):
		with patch.dict(
			TARGETS,
			{"Current Asset:Account": "Attività correnti (conto)", "Current Asset": "Etichetta globale"},
		):
			self.parent_type = "Current Asset"
			with self.assertRaises(frappe.ValidationError) as caught:
				self.document("Current Asset").validate_parent_child_account_type()
			self.assertEqual(str(caught.exception), TARGETS[FRAME].format("Attività correnti (conto)"))
			self.assertEqual(
				format_value("Current Asset", self.field, translated=True), "Attività correnti (conto)"
			)

	def test_different_or_absent_parent_types_preserve_reads_and_acceptance(self):
		for account_type in RESTRICTED_TYPES:
			for parent_type in ("Bank", None):
				self.events.clear()
				self.parent_type = parent_type
				doc = self.document(account_type)
				before = deepcopy(doc.__dict__)
				self.assertIsNone(doc.validate_parent_child_account_type())
				self.assertEqual(doc.__dict__, before)
				self.assertEqual(self.events, [(("Account", "Current Asset", ["account_type"]), {})])

	def test_no_parent_and_unrestricted_values_do_not_read_or_change_state(self):
		for account_type, parent in [(value, None) for value in RESTRICTED_TYPES] + [
			("Bank", "Current Asset"),
			("Custom Type", "Current Asset"),
			(None, "Current Asset"),
		]:
			self.events.clear()
			doc = self.document(account_type, parent)
			before = deepcopy(doc.__dict__)
			self.assertIsNone(doc.validate_parent_child_account_type())
			self.assertEqual(doc.__dict__, before)
			self.assertEqual(self.events, [])

	def test_english_fallback_keeps_canonical_account_category(self):
		frappe.local.lang = "en"
		self.parent_type = "Direct Income"
		with self.assertRaises(frappe.ValidationError) as caught:
			self.document("Direct Income").validate_parent_child_account_type()
		self.assertEqual(str(caught.exception), FRAME.format("Direct Income"))
