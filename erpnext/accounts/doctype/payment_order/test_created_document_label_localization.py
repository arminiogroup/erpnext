# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

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

from erpnext.accounts.doctype.payment_order import payment_order as producer

FRAME = "{0} {1} created"
FRAMES = (FRAME,)
TARGETS = {
	FRAME: "Documento creato: {0} {1}.",
	"Journal Entry": "Registrazione contabile",
	"JRN-RAW": "DO NOT TRANSLATE RECORD",
}


class TestPaymentOrderCreatedDocumentLabel(UnitTestCase):
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
		return self.db

	def _meta(self, doctype):
		if doctype not in self.metadata:
			slug = frappe.scrub(doctype)
			paths = list(Path(frappe.get_app_path("erpnext")).rglob(slug + ".json"))
			path = next(p for p in paths if p.parent.name == slug)
			data = json.loads(path.read_text())
			meta = Meta.__new__(Meta)
			meta.__dict__.update(data)
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

	def test_configured_production_keys(self):
		path = Path(producer.__file__)
		relative = str(path.relative_to(Path(frappe.get_app_path("erpnext")).parent))
		method = next(
			m
			for pattern, m in get_method_map("erpnext") + get_method_map("frappe")
			if pathmatch(pattern, relative)
		)
		keys = {
			(message, context)
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
		}
		for frame in FRAMES:
			self.assertIn((frame, None), keys)

	def test_real_journal_calculation_then_memory_save_and_notice(self):
		for mode in (None, "Cash", "Bank"):
			with self.subTest(mode=mode):
				self._journal_case(mode)

	def _journal_case(self, mode, fail_save=False):
		events = []
		rows = [
			frappe._dict(
				supplier="SUPPLIER-RAW",
				mode_of_payment="Cash",
				amount=12.5,
				reference_doctype="Purchase Invoice",
				reference_name="INV-RAW-1",
			),
			frappe._dict(
				supplier="SUPPLIER-RAW",
				mode_of_payment="Bank",
				amount=3.25,
				reference_doctype="Purchase Invoice",
				reference_name="INV-RAW-2",
			),
			frappe._dict(
				supplier="OTHER-RAW",
				mode_of_payment="Cash",
				amount=999,
				reference_doctype="Purchase Invoice",
				reference_name="OTHER-RAW",
			),
		]
		doc = frappe._dict(
			name="PAYMENT-ORDER-RAW", company="COMPANY-RAW", account="BANK-ACCOUNT-RAW", references=rows
		)
		before = deepcopy(doc)

		class MemoryJournal:
			def __init__(self):
				self.doctype = "Journal Entry"
				self.name = "JRN-RAW"
				self.accounts = []
				self.flags = frappe._dict()

			def append(self, field, row):
				self_outer.assertEqual(field, "accounts")
				self.accounts.append(row)

			def save(self):
				events.append(("memory-save", deepcopy(self.__dict__)))
				if fail_save:
					raise ValueError("memory save rejected")

		self_outer = self
		journal = MemoryJournal()

		def account(*args):
			events.append(("account", args))
			self.assertEqual(args, ("Supplier", "SUPPLIER-RAW", "COMPANY-RAW"))
			return "SUPPLIER-ACCOUNT-RAW"

		with patch.object(
			frappe, "new_doc", side_effect=lambda dtype: journal if dtype == "Journal Entry" else None
		), patch.object(frappe, "get_all", return_value=[["Cash", "Cash"], ["Bank", "Bank"]]), patch.object(
			producer, "get_party_account", side_effect=account
		), patch.object(frappe, "msgprint", side_effect=lambda message: events.append(("notice", message))):
			if fail_save:
				with self.assertRaisesRegex(ValueError, "memory save rejected"):
					producer.make_journal_entry(doc, "SUPPLIER-RAW", mode)
			else:
				producer.make_journal_entry(doc, "SUPPLIER-RAW", mode)
		self.assertEqual(doc, before)
		self.assertEqual(journal.voucher_type, "Cash Entry" if mode == "Cash" else "Bank Entry")
		self.assertEqual(journal.payment_order, "PAYMENT-ORDER-RAW")
		self.assertTrue(journal.flags.ignore_mandatory)
		included = [
			row
			for row in rows
			if row.supplier == "SUPPLIER-RAW" and (not mode or row.mode_of_payment == mode)
		]
		self.assertEqual(
			journal.accounts[-1],
			{
				"account": "BANK-ACCOUNT-RAW",
				"credit_in_account_currency": sum(row.amount for row in included),
			},
		)
		self.assertEqual(
			[row["reference_name"] for row in journal.accounts[:-1]], [row.reference_name for row in included]
		)
		self.assertEqual(sum(event[0] == "notice" for event in events), int(not fail_save))
		if not fail_save:
			self.assertEqual(
				events[-1],
				(
					"notice",
					(self.messages.get(FRAME, FRAME)).format(
						self.messages.get("Journal Entry", "Journal Entry"), "JRN-RAW"
					),
				),
			)
		self.assertEqual(self.meta_reads, [])

	def test_failed_persistence_delivery_has_no_notice(self):
		self._journal_case("Cash", True)

	def test_empty_dictionary_does_not_translate_name(self):
		self.messages.clear()
		self._journal_case(None)
