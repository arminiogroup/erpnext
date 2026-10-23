# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import itertools
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.model.meta import Meta
from frappe.query_builder.builder import MariaDB
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.exchange_rate_revaluation import exchange_rate_revaluation as revaluation

FRAME = "Party Type and Party is mandatory for {0} account"
TARGETS = {
	FRAME: "Per i conti di tipo {0} sono obbligatori i campi Tipo di controparte e Controparte.",
	"Receivable": "Crediti",
	"Payable": "Debiti",
}


class ReadResults:
	db_type = "mariadb"

	def __init__(self, events):
		self.events = events
		self.rows = []

	def get_value(self, *args, **kwargs):
		assert (args, kwargs) == (("Company", "Company", "default_currency"), {"cache": True})
		self.events.append(("company_currency", args, kwargs))
		return "EUR"

	def get_default(self, key):
		assert key == "currency_precision"
		self.events.append(("default", key))
		return "2"

	def sql(self, query, parameters, *args, **kwargs):
		assert query.startswith("SELECT ") and "`tabGL Entry`" in query
		assert all(word not in query for word in ("INSERT ", "UPDATE ", "DELETE "))
		assert args == () and kwargs == {"as_dict": True}
		self.events.append(("sql", query, deepcopy(parameters), kwargs))
		return deepcopy(self.rows)


class TestPartyRequirementLabelTranslation(UnitTestCase):
	def setUp(self):
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
		self.db = ReadResults(self.events)
		self.enterContext(patch.object(frappe, "db", self.db))
		self.enterContext(patch.object(frappe.local, "db", self.db, create=True))
		self.enterContext(patch.object(revaluation, "qb", MariaDB))
		self.enterContext(patch("frappe.get_cached_value", side_effect=self.cached))
		self.enterContext(patch("frappe.has_permission", side_effect=self.permission))
		self.enterContext(patch("frappe.get_meta", side_effect=self.get_meta))
		for name in ("get_all", "get_doc", "new_doc", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))
		data = json.loads(
			Path(frappe.get_app_path("erpnext", "accounts/doctype/account/account.json")).read_text()
		)
		fields = [frappe._dict(parent=data["name"], **field) for field in data["fields"]]
		self.meta = Meta.__new__(Meta)
		self.meta.__dict__.update(
			name="Account",
			doctype="DocType",
			fields=fields,
			_fields={field.fieldname: field for field in fields},
		)
		self.account_type = "Receivable"
		self.deny = False

	def permission(self, *args, **kwargs):
		self.assertEqual((args, kwargs), (("Account",), {"doc": "Asset", "throw": True}))
		self.events.append(("permission", args, kwargs))
		if self.deny:
			raise frappe.PermissionError("Permission result")
		return True

	def cached(self, *args, **kwargs):
		self.assertEqual((args, kwargs), (("Account", "Asset", ["account_currency", "account_type"]), {}))
		self.events.append(("cached", args, kwargs))
		return ["USD", self.account_type]

	def get_meta(self, doctype, *args, **kwargs):
		self.assertEqual((doctype, args, kwargs), ("Account", (), {}))
		self.events.append(("meta", doctype))
		return self.meta

	def call(self, **values):
		arguments = dict(
			company="Company",
			posting_date="2026-10-21",
			account="Asset",
			party_type=None,
			party=None,
			rounding_loss_allowance=0.01,
		)
		arguments.update(values)
		before = deepcopy(arguments)
		try:
			return revaluation.get_account_details(**arguments)
		finally:
			self.assertEqual(arguments, before)

	def test_missing_components_translate_actual_account_select_after_original_reads(self):
		for account_type, (party_type, party) in itertools.product(
			("Receivable", "Payable"), ((None, None), ("Customer", None), (None, "Project"))
		):
			with self.subTest(account_type=account_type, party_type=party_type, party=party):
				self.events.clear()
				self.account_type = account_type
				with self.assertRaises(frappe.ValidationError) as caught:
					self.call(party_type=party_type, party=party)
				self.assertEqual(str(caught.exception), TARGETS[FRAME].format(TARGETS[account_type]))
				self.assertEqual([event[0] for event in self.events], ["permission", "cached", "meta"])
				self.assertEqual(
					frappe.format_value(account_type, self.meta.get_field("account_type"), translated=True),
					TARGETS[account_type],
				)

	def test_permission_and_company_date_guards_stay_before_metadata(self):
		self.deny = True
		with self.assertRaises(frappe.PermissionError):
			self.call(company=None, posting_date=None)
		self.assertEqual([event[0] for event in self.events], ["permission"])
		self.deny = False
		for company, posting_date in ((None, "2026-10-21"), ("Company", None), ("", "")):
			with self.subTest(company=company, posting_date=posting_date):
				self.events.clear()
				with self.assertRaises(frappe.ValidationError) as caught:
					self.call(company=company, posting_date=posting_date)
				self.assertEqual(str(caught.exception), "Company and Posting Date is mandatory")
				self.assertEqual([event[0] for event in self.events], ["permission"])

	def test_valid_party_or_non_party_accounts_preserve_actual_queries_and_skip_display_meta(self):
		for account_type, party_type, party in (
			("Receivable", "Customer", "Project"),
			("Payable", "Supplier", "Project"),
			("Bank", None, None),
			("Custom Type", None, None),
			(None, None, None),
		):
			with self.subTest(account_type=account_type):
				self.events.clear()
				frappe.flags.company_currency = {}
				self.account_type = account_type
				self.assertEqual(self.call(party_type=party_type, party=party), {"account_currency": "USD"})
				self.assertEqual(
					[event[0] for event in self.events],
					["permission", "cached", "company_currency", "sql", "default"],
				)
				query = self.events[3]
				self.assertIn("HAVING", query[1])
				self.assertIn("GROUP BY", query[1])
				self.assertIn("Asset", query[2].values())
				self.assertIn("2026-10-21", query[2].values())
				if party_type:
					self.assertIn(party_type, query[2].values())
					self.assertIn(party, query[2].values())
				self.assertNotIn("Cespite", query[2].values())

	def test_existing_zero_balance_guard_does_not_revalue_or_resolve_display_meta(self):
		self.account_type = "Bank"
		self.db.rows = [
			frappe._dict(
				account="Asset",
				account_currency="USD",
				party_type=None,
				party=None,
				balance=0,
				balance_in_account_currency=0,
			)
		]
		before = deepcopy(self.db.rows)
		self.assertEqual(self.call(), {"account_currency": "USD"})
		self.assertEqual(self.db.rows, before)
		self.assertNotIn("meta", [event[0] for event in self.events])

	def test_english_fallback_preserves_canonical_enum(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			self.call()
		self.assertEqual(str(caught.exception), FRAME.format("Receivable"))

	def test_actual_select_owner_context_can_override_global_display(self):
		with patch.dict(TARGETS, {"Receivable:Account": "Crediti del conto"}):
			with self.assertRaises(frappe.ValidationError) as caught:
				self.call()
			self.assertEqual(str(caught.exception), TARGETS[FRAME].format("Crediti del conto"))
