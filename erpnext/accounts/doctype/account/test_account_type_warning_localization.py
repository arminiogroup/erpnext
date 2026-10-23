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

from erpnext.accounts.doctype.account import account as producer

FRAME = "Ledger entries exist for this account. Changing {0} from {1} can produce incorrect results in the {2} report."
FRAMES = (FRAME, "Accounts Receivable", "Accounts Payable")
TARGETS = {
	FRAME: "Esistono registrazioni contabili per questo conto. La modifica di {0} da {1} può produrre risultati errati nel report {2}.",
	"Account Type": "Tipo di conto",
	"Receivable": "Crediti",
	"Payable": "Debiti",
	"Accounts Receivable": "Crediti verso clienti",
	"Accounts Payable": "Debiti verso fornitori",
}


class TestAccountTypeReportWarning(UnitTestCase):
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

	def test_warning_and_comment_same_message_with_actual_meta_select(self):
		for old, new, rows in (
			("Receivable", "Payable", True),
			("Payable", "Bank", True),
			("Receivable", "Receivable", True),
			("Bank", "Payable", True),
			("Receivable", "Payable", False),
			(None, "Bank", True),
		):
			with self.subTest(old=old, new=new, rows=rows):
				self.meta_reads.clear()
				doc = producer.Account.__new__(producer.Account)
				doc.__dict__.update(
					doctype="Account",
					name="ACCOUNT-RAW",
					account_type=new,
					_doc_before_save=frappe._dict(account_type=old) if old else None,
				)
				before = deepcopy(doc.__dict__)
				events = []

				def get_all(dt, **kw):
					events.append(("query", dt, kw))
					self.assertEqual(
						(dt, kw),
						("GL Entry", {"filters": {"account": "ACCOUNT-RAW", "is_cancelled": 0}, "limit": 1}),
					)
					return [{"name": "GLE-RAW"}] if rows else []

				self._database(get_all=get_all)
				with patch.object(
					frappe, "msgprint", side_effect=lambda message: events.append(("notice", message))
				), patch.object(
					producer.Account,
					"add_comment",
					lambda obj, *args: events.append(("memory-comment", *args)),
				):
					self.assertIsNone(doc.validate_receivable_payable_account_type())
				warns = old in ("Receivable", "Payable") and old != new and rows
				if warns:
					report = "Accounts Receivable" if old == "Receivable" else "Accounts Payable"
					expected = TARGETS[FRAME].format(
						"<strong>Tipo di conto</strong>", TARGETS[old], TARGETS[report]
					)
					self.assertEqual(
						events[-2:], [("notice", expected), ("memory-comment", "Comment", expected)]
					)
					self.assertEqual(self.meta_reads, ["Account"])
				else:
					self.assertFalse(any(event[0] in ("notice", "memory-comment") for event in events))
					self.assertEqual(self.meta_reads, [])
				self._state(doc, before, "Account")

	def test_custom_field_context_and_select_context_are_authoritative(self):
		meta = self._meta("Account")
		meta.get_field("account_type").label = "Custom category"
		self.messages.update(
			{
				"Custom category:Account": "Categoria personalizzata",
				"Receivable:Account": "Credito contestuale",
			}
		)
		doc = producer.Account.__new__(producer.Account)
		doc.__dict__.update(
			doctype="Account",
			name="RAW",
			account_type="Bank",
			_doc_before_save=frappe._dict(account_type="Receivable"),
		)
		self._database(get_all=lambda *a, **kw: [{}])
		events = []
		with patch.object(frappe, "msgprint", side_effect=events.append), patch.object(
			producer.Account, "add_comment", lambda *args: None
		):
			doc.validate_receivable_payable_account_type()
		self.assertEqual(
			events,
			[
				TARGETS[FRAME].format(
					"<strong>Categoria personalizzata</strong>",
					"Credito contestuale",
					"Crediti verso clienti",
				)
			],
		)

	def test_empty_dictionary_uses_complete_report_name(self):
		self.messages.clear()
		doc = producer.Account.__new__(producer.Account)
		doc.__dict__.update(
			doctype="Account",
			name="RAW",
			account_type="Bank",
			_doc_before_save=frappe._dict(account_type="Payable"),
		)
		self._database(get_all=lambda *a, **kw: [{}])
		events = []
		with patch.object(frappe, "msgprint", side_effect=events.append), patch.object(
			producer.Account, "add_comment", lambda *args: None
		):
			doc.validate_receivable_payable_account_type()
		self.assertEqual(
			events, [FRAME.format("<strong>Account Type</strong>", "Payable", "Accounts Payable")]
		)
