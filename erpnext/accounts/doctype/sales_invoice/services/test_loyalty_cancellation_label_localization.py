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

from erpnext.accounts.doctype.sales_invoice.services import loyalty as producer

FRAME = (
	"{0} cannot be cancelled since the Loyalty Points earned has been redeemed. First cancel the {1} No {2}"
)
FRAMES = (FRAME,)
TARGETS = {
	FRAME: "{0}: impossibile annullare il documento perché i punti fedeltà maturati sono già stati utilizzati. Annullare prima {1}: {2}.",
	"Sales Invoice": "Fattura di vendita",
	"POS Invoice": "Fattura POS",
	"REDEEM-RAW": "DO NOT TRANSLATE RECORD",
}


class TestLoyaltyCancellationDocumentLabels(UnitTestCase):
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

	def test_actual_rejection_reads_and_raw_redemption_order(self):
		for dtype in ("Sales Invoice", "POS Invoice"):
			with self.subTest(doctype=dtype):
				reads = []
				doc = frappe._dict(doctype=dtype, name="EARNED-RAW")
				before = deepcopy(doc)

				def rows(dt, **kw):
					reads.append((dt, kw))
					return (
						[frappe._dict(name="LP-RAW")]
						if len(reads) == 1
						else [frappe._dict(invoice="REDEEM-RAW"), frappe._dict(invoice="SECOND-RAW")]
					)

				self._database(get_all=rows)
				with self.assertRaises(frappe.ValidationError) as caught:
					producer.LoyaltyService(doc).delete_loyalty_point_entry()
				self.assertEqual(
					str(caught.exception),
					TARGETS[FRAME].format(TARGETS[dtype], TARGETS[dtype], "REDEEM-RAW, SECOND-RAW"),
				)
				self.assertEqual(doc, before)
				self.assertEqual(
					reads,
					[
						(
							"Loyalty Point Entry",
							{
								"filters": {"invoice": "EARNED-RAW", "loyalty_points": (">", 0)},
								"fields": ["name"],
							},
						),
						(
							"Loyalty Point Entry",
							{"filters": {"redeem_against": "LP-RAW"}, "fields": ["name", "invoice"]},
						),
					],
				)
				self.assertEqual(self.meta_reads, [])

	def test_no_earned_entry_returns_without_mutation(self):
		self._database(get_all=lambda *a, **kw: [])
		self.assertIsNone(producer.LoyaltyService(frappe._dict(name="RAW")).delete_loyalty_point_entry())

	def test_empty_dictionary_falls_back_without_extra_reads(self):
		self.messages.clear()
		calls = []

		def rows(*args, **kw):
			calls.append(kw)
			return [frappe._dict(name="LP")] if len(calls) == 1 else [frappe._dict(invoice="RAW")]

		self._database(get_all=rows)
		with self.assertRaises(frappe.ValidationError) as caught:
			producer.LoyaltyService(
				frappe._dict(doctype="POS Invoice", name="RAW")
			).delete_loyalty_point_entry()
		self.assertEqual(str(caught.exception), FRAME.format("POS Invoice", "POS Invoice", "RAW"))
