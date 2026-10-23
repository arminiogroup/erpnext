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

from erpnext.accounts.report.payment_period_based_on_invoice_date import (
	payment_period_based_on_invoice_date as producer,
)

FRAME = "{0} payment entries can not be filtered by {1}"
FRAMES = (FRAME,)
TARGETS = {
	FRAME: "Pagamenti ({0}): impossibile filtrare per {1}.",
	"Incoming": "In entrata",
	"Outgoing": "In uscita",
	"Customer": "Cliente",
	"Supplier": "Fornitore",
	"In entrata": "DO NOT RETRANSLATE INPUT",
}


class TestPaymentPeriodPartyFilterLabel(UnitTestCase):
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

	def test_actual_localized_direction_raw_party_matrix(self):
		for direction, party in (
			("Incoming", "Supplier"),
			("Outgoing", "Customer"),
			("Incoming", "Customer"),
			("Outgoing", "Supplier"),
		):
			with self.subTest(direction=direction, party=party):
				filters = frappe._dict(payment_type=TARGETS[direction], party_type=party)
				before = deepcopy(filters)
				if (direction, party) in (("Incoming", "Supplier"), ("Outgoing", "Customer")):
					with self.assertRaises(frappe.ValidationError) as caught:
						producer.validate_filters(filters)
					self.assertEqual(
						str(caught.exception), TARGETS[FRAME].format(TARGETS[direction], TARGETS[party])
					)
				else:
					self.assertIsNone(producer.validate_filters(filters))
				self.assertEqual(filters, before)
				self.assertEqual(self.meta_reads, [])

	def test_actual_client_select_payload_contract(self):
		source = Path(producer.__file__).with_suffix(".js").read_text()
		self.assertIn(r'options: __("Incoming") + "\n" + __("Outgoing")', source)
		self.assertIn('options: "DocType"', source)

	def test_empty_dictionary_keeps_english_and_missing_filters(self):
		self.messages.clear()
		with self.assertRaises(frappe.ValidationError) as caught:
			producer.validate_filters(frappe._dict(payment_type="Incoming", party_type="Supplier"))
		self.assertEqual(str(caught.exception), FRAME.format("Incoming", "Supplier"))
		self.assertIsNone(producer.validate_filters(frappe._dict()))
