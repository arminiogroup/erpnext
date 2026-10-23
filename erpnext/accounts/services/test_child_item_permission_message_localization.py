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

from erpnext.accounts.services import child_item_update as producer

CREATE = "{0}: you do not have permission to add items."
WRITE = "{0}: you do not have permission to update items."
FRAMES = (CREATE, WRITE)
TARGETS = {
	CREATE: "{0}: non si dispone dell’autorizzazione per aggiungere articoli.",
	WRITE: "{0}: non si dispone dell’autorizzazione per aggiornare gli articoli.",
	"Sales Order": "Ordine di vendita",
	"Purchase Order": "Ordine d'acquisto",
	"Quotation": "Preventivo",
	"Supplier Quotation": "Preventivo del fornitore",
	"Insufficient Permissions": "Autorizzazioni insufficienti",
}


class TestItemPermissionActionCompleteMessages(UnitTestCase):
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

	def test_actual_permission_handler_matrix_and_raw_call(self):
		for dtype in ("Sales Order", "Purchase Order", "Quotation", "Supplier Quotation"):
			for action in ("write", "create"):
				with self.subTest(doctype=dtype, action=action):
					calls = []
					updater = producer.ChildItemUpdater.__new__(producer.ChildItemUpdater)
					updater.parent_doctype = dtype

					def permission(kind):
						calls.append(kind)
						raise frappe.PermissionError

					updater.parent = SimpleNamespace(check_permission=permission)
					before = deepcopy(updater.__dict__)
					with self.assertRaises(frappe.ValidationError) as caught:
						updater._check_permissions(action)
					self.assertEqual(
						str(caught.exception),
						TARGETS[WRITE if action == "write" else CREATE].format(TARGETS[dtype]),
					)
					self.assertEqual(calls, [action])
					self._state(updater, before)
					self.assertEqual(self.meta_reads, [])
					self.assertEqual(
						frappe.local.message_log[-1]["title"], TARGETS["Insufficient Permissions"]
					)

	def test_allowed_unknown_permission_and_other_exception_contracts(self):
		updater = producer.ChildItemUpdater.__new__(producer.ChildItemUpdater)
		updater.parent_doctype = "Sales Order"
		calls = []
		updater.parent = SimpleNamespace(check_permission=lambda kind: calls.append(kind))
		self.assertIsNone(updater._check_permissions("write"))
		self.assertEqual(calls, ["write"])

		def denied(kind):
			raise frappe.PermissionError

		updater.parent.check_permission = denied
		with self.assertRaises(KeyError):
			updater._check_permissions("custom")

		def other(kind):
			raise ValueError("external failure")

		updater.parent.check_permission = other
		with self.assertRaisesRegex(ValueError, "external failure"):
			updater._check_permissions("write")

	def test_empty_dictionary_keeps_complete_action_template(self):
		self.messages.clear()
		updater = producer.ChildItemUpdater.__new__(producer.ChildItemUpdater)
		updater.parent_doctype = "Sales Order"

		def denied(kind):
			raise frappe.PermissionError

		updater.parent = SimpleNamespace(check_permission=denied)
		with self.assertRaises(frappe.ValidationError) as caught:
			updater._check_permissions("create")
		self.assertEqual(str(caught.exception), CREATE.format("Sales Order"))
