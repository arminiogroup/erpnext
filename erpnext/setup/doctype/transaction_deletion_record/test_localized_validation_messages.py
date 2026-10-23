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

from erpnext.setup.doctype.transaction_deletion_record import transaction_deletion_record as producer

FRAMES = (
	"Duplicate entry: {0}.",
	"Duplicate entry: {0} with company field {1}.",
	"Transaction Deletion Record {0} is active for {1}. Cannot save documents of this type until deletion completes.",
)
TARGETS = {
	"Duplicate entry: {0}.": "Voce duplicata: {0}.",
	"Duplicate entry: {0} with company field {1}.": "Voce duplicata: {0}, campo azienda {1}.",
	"Transaction Deletion Record {0} is active for {1}. Cannot save documents of this type until deletion completes.": "Il processo di eliminazione delle transazioni {0} è attivo per {1}. Non è possibile salvare documenti di questo tipo finché l’eliminazione non è completata.",
	"Sales Invoice": "Fattura di vendita",
	"company": "DO NOT TRANSLATE TECHNICAL FIELD",
	"DELETE-RAW": "DO NOT TRANSLATE RECORD",
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

	def test_duplicate_composite_key_whole_messages_and_technical_identifiers(self):
		for fields in ((None, None), ("company", "company"), ("company", "another_company")):
			doc = producer.TransactionDeletionRecord.__new__(producer.TransactionDeletionRecord)
			doc.__dict__.update(
				doctypes_to_delete=[
					frappe._dict(doctype_name="Sales Invoice", company_field=f) for f in fields
				]
			)
			before = deepcopy(doc.__dict__)
			reads = []
			self._database(
				exists=lambda *a: reads.append(("exists", a)) or True,
				get_value=lambda *a: reads.append(("value", a)) or 0,
			)
			with patch.object(
				frappe,
				"get_all",
				side_effect=lambda dt, **kw: [] if dt == "DocType" else ["company", "another_company"],
			):
				if fields[0] == fields[1]:
					with self.assertRaises(frappe.ValidationError) as error:
						doc.validate_to_delete_list()
					expected = (
						TARGETS[FRAMES[1]].format(TARGETS["Sales Invoice"], "company")
						if fields[0]
						else TARGETS[FRAMES[0]].format(TARGETS["Sales Invoice"])
					)
					self.assertEqual(str(error.exception), expected)
				else:
					doc.validate_to_delete_list()
			self._state(doc, before)

	def test_actual_lock_guard_cache_identity_core_and_ledger_bypasses(self):
		for dtype, active in (
			("Sales Invoice", True),
			("Sales Invoice", False),
			("GL Entry", True),
			("Company", True),
		):
			reads = []
			doc = frappe._dict(doctype=dtype)
			before = deepcopy(doc)
			with patch.object(
				frappe,
				"cache",
				SimpleNamespace(
					get_value=lambda key: reads.append(key) or ("DELETE-RAW" if active else None)
				),
			):
				if dtype == "Sales Invoice" and active:
					with self.assertRaises(frappe.ValidationError) as error:
						producer.check_for_running_deletion_job(doc)
					self.assertEqual(
						str(error.exception),
						TARGETS[FRAMES[2]].format(
							producer.get_link_to_form("Transaction Deletion Record", "DELETE-RAW"),
							frappe.bold(TARGETS[dtype]),
						),
					)
				else:
					producer.check_for_running_deletion_job(doc)
			self.assertEqual(
				reads, ["deletion_running_doctype:Sales Invoice"] if dtype == "Sales Invoice" else []
			)
			self.assertEqual(doc, before)

	def test_english_duplicate_frame_and_empty_company_normalization(self):
		self.messages.clear()
		doc = producer.TransactionDeletionRecord.__new__(producer.TransactionDeletionRecord)
		doc.__dict__.update(
			doctypes_to_delete=[
				frappe._dict(doctype_name="Sales Invoice", company_field=f) for f in (None, "")
			]
		)
		self._database(exists=lambda *a: True, get_value=lambda *a: 0)
		with patch.object(frappe, "get_all", return_value=[]):
			with self.assertRaises(frappe.ValidationError) as error:
				doc.validate_to_delete_list()
		self.assertEqual(str(error.exception), FRAMES[0].format("Sales Invoice"))
