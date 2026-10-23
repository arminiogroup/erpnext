# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import ast
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.setup.doctype.company import company as producer

FRAMES = (
	"{0} currency must be same as company's default currency. Please select another account.",
	"{0}: {1} is a group account.",
)
TARGETS = {
	"{0} currency must be same as company's default currency. Please select another account.": "La valuta di {0} deve coincidere con la valuta predefinita dell’azienda. Selezionare un altro conto.",
	"{0}: {1} is a group account.": "{0}: {1} è un conto di raggruppamento.",
	"ACCOUNT-RAW": "DO NOT TRANSLATE ACCOUNT",
}


class TestLocalizedMessages(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.messages = dict(TARGETS)
		self.metadata = {}
		self.meta_reads = []
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(frappe.local, "conf", frappe._dict(host_name="http://memory.invalid")))
		self.enterContext(
			patch.object(
				frappe.local,
				"system_settings",
				frappe._dict(rounding_method="Banker's Rounding"),
				create=True,
			)
		)
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.messages if lang == "it" else {},
			)
		)
		self.enterContext(patch.object(frappe, "get_meta", side_effect=self._get_meta))
		for name in ("get_all", "get_doc", "get_cached_value", "new_doc", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))
		self.addCleanup(setattr, frappe.local, "db", getattr(frappe.local, "db", None))
		self.db = SimpleNamespace(db_type="mariadb")
		self.enterContext(patch.object(frappe, "db", self.db))
		frappe.local.db = self.db

	def _database(self, **providers):
		self.db.__dict__.clear()
		self.db.__dict__.update(db_type="mariadb", **providers)

	def _meta(self, doctype):
		if doctype not in self.metadata:
			slug = frappe.scrub(doctype)
			path = next(
				p for p in Path(frappe.get_app_path("erpnext")).rglob(slug + ".json") if p.parent.name == slug
			)
			data = json.loads(path.read_text())
			meta = Meta.__new__(Meta)
			meta.__dict__.update(data)
			meta.__dict__.setdefault("istable", 0)
			meta.__dict__.setdefault("is_virtual", 0)
			meta.fields = [
				frappe._dict({**df, "parent": doctype, "parenttype": "DocType", "parentfield": "fields"})
				for df in data["fields"]
			]
			self.metadata[doctype] = meta
		return self.metadata[doctype]

	def _get_meta(self, doctype, *args, **kwargs):
		self.assertEqual((args, kwargs), ((), {}))
		self.meta_reads.append(doctype)
		return self._meta(doctype)

	def _state(self, doc, before, owner=None):
		expected = deepcopy(before)
		if owner and "meta" not in before and "meta" in doc.__dict__:
			self.assertIs(doc.__dict__["meta"], self.metadata[owner])
			self.assertEqual(doc.__dict__["meta"].name, owner)
			expected["meta"] = self.metadata[owner]
		self.assertEqual(doc.__dict__, expected)

	def _keys(self, path):
		relative = str(path.relative_to(Path(frappe.get_app_path("erpnext")).parent))
		method = next(
			m
			for pattern, m in get_method_map("erpnext") + get_method_map("frappe")
			if pathmatch(pattern, relative)
		)
		return {
			(message, context)
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
		}

	def test_configured_production_keys(self):
		keys = self._keys(Path(producer.__file__))
		for frame in FRAMES:
			self.assertIn((frame, None), keys)

	def test_all_23_field_bindings_and_ordered_guard_matrix(self):
		fields = (
			"default_bank_account",
			"default_cash_account",
			"default_receivable_account",
			"default_payable_account",
			"default_expense_account",
			"default_income_account",
			"stock_received_but_not_billed",
			"stock_delivered_but_not_billed",
			"stock_adjustment_account",
			"write_off_account",
			"bank_charges_account",
			"default_discount_account",
			"unrealized_profit_loss_account",
			"exchange_gain_loss_account",
			"exchange_gain_account",
			"exchange_loss_account",
			"unrealized_exchange_gain_loss_account",
			"round_off_account",
			"default_deferred_revenue_account",
			"default_deferred_expense_account",
			"accumulated_depreciation_account",
			"depreciation_expense_account",
			"disposal_account",
		)
		for field in fields:
			self._meta("Company").get_field(field).label = "Custom " + field
			self.messages["Custom " + field + ":Company"] = "Campo " + field
			for scenario in ("group", "currency", "disabled", "company", "valid"):
				with self.subTest(field=field, scenario=scenario):
					doc = producer.Company.__new__(producer.Company)
					doc.__dict__.update(
						doctype="Company",
						name="COMPANY-RAW",
						default_currency="EUR",
						**{field: "ACCOUNT-RAW"},
					)
					before = deepcopy(doc.__dict__)
					reads = []
					self.meta_reads.clear()
					self._database(
						get_value=lambda *a: reads.append(a)
						or (
							"OTHER-COMPANY" if scenario == "company" else "COMPANY-RAW",
							scenario == "group",
							scenario == "disabled",
						)
					)
					with patch.object(
						producer,
						"get_account_currency",
						return_value="USD" if scenario == "currency" else "EUR",
					) as currency:
						if scenario == "valid":
							doc.validate_default_accounts()
						else:
							with self.assertRaises(frappe.ValidationError) as error:
								doc.validate_default_accounts()
							if scenario == "group":
								self.assertEqual(
									str(error.exception),
									TARGETS[FRAMES[1]].format(
										frappe.bold("Campo " + field), frappe.bold("ACCOUNT-RAW")
									),
								)
							if scenario == "currency":
								self.assertEqual(
									str(error.exception),
									TARGETS[FRAMES[0]].format(frappe.bold("Campo " + field)),
								)
					self.assertEqual(reads, [("Account", "ACCOUNT-RAW", ["company", "is_group", "disabled"])])
					self.assertEqual(currency.call_count, int(scenario in ("currency", "valid")))
					self.assertEqual(
						self.meta_reads, ["Company"] if scenario in ("group", "currency") else []
					)
					self._state(doc, before, "Company" if scenario in ("group", "currency") else None)

	def test_multiple_accounts_keep_first_failure_and_record_identity(self):
		doc = producer.Company.__new__(producer.Company)
		doc.__dict__.update(
			doctype="Company",
			name="COMPANY-RAW",
			default_currency="EUR",
			default_bank_account="FIRST-RAW",
			disposal_account="SECOND-RAW",
		)
		reads = []
		self._database(get_value=lambda dt, name, fields: reads.append(name) or ("COMPANY-RAW", 1, 0))
		with self.assertRaises(frappe.ValidationError):
			doc.validate_default_accounts()
		self.assertEqual(reads, ["FIRST-RAW"])
