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

from erpnext.controllers import buying_controller as producer

FRAMES = (
	"Item {0} is not enabled for this transaction. Enable {1} in its Item master.",
	"Items {0} are not enabled for this transaction. Enable {1} in their Item masters.",
)
TARGETS = {
	"Item {0} is not enabled for this transaction. Enable {1} in its Item master.": "L’articolo {0} non è abilitato per questa transazione. Attivare {1} nella relativa anagrafica articolo.",
	"Items {0} are not enabled for this transaction. Enable {1} in their Item masters.": "Gli articoli {0} non sono abilitati per questa transazione. Attivare {1} nelle relative anagrafiche articolo.",
	"Allow Purchase": "Consenti acquisto",
	"Allow Sales": "Consenti vendita",
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

	def test_actual_role_count_query_limit_and_keyword_api_matrix(self):
		for field, role in (("is_purchase_item", "purchase"), ("is_sales_item", "sales")):
			for count in (0, 1, 2, 25):
				with self.subTest(field=field, count=count):
					invalid = ["ITEM-" + str(i) for i in range(count)]
					doc = SimpleNamespace(
						items=[
							frappe._dict(item_code="RAW-A"),
							frappe._dict(item_code="RAW-B"),
							frappe._dict(item_code=None),
						]
					)
					before = deepcopy(doc)
					reads = []

					def rows(dt, **kw):
						reads.append((dt, kw))
						return invalid

					with patch.object(frappe, "get_all", side_effect=rows):
						if count:
							with self.assertRaises(frappe.ValidationError) as error:
								producer.validate_item_type(doc, fieldname=field, message=role)
							frame = FRAMES[0] if count == 1 else FRAMES[1]
							self.assertEqual(
								str(error.exception),
								TARGETS[frame].format(
									", ".join(invalid),
									TARGETS["Allow Purchase" if role == "purchase" else "Allow Sales"],
								),
							)
						else:
							producer.validate_item_type(doc, field, role)
					self.assertEqual(
						reads,
						[
							(
								"Item",
								{
									"filters": {"name": ["in", ["RAW-A", "RAW-B"]], field: 0},
									"pluck": "item_code",
									"limit_page_length": 0,
								},
							)
						],
					)
					self.assertEqual(doc, before)
		self.assertIsNone(producer.validate_item_type(SimpleNamespace(items=[]), "is_sales_item", "sales"))

	def test_actual_metadata_override_and_english_fallback(self):
		self._meta("Item").get_field("is_purchase_item").label = "Custom purchase caption"
		self.messages["Custom purchase caption:Item"] = "Etichetta acquisti"
		doc = SimpleNamespace(items=[frappe._dict(item_code="ITEM-RAW")])
		with patch.object(frappe, "get_all", return_value=["ITEM-RAW"]):
			with self.assertRaises(frappe.ValidationError) as error:
				producer.validate_item_type(doc, "is_purchase_item", "legacy compatibility text")
		self.assertEqual(str(error.exception), TARGETS[FRAMES[0]].format("ITEM-RAW", "Etichetta acquisti"))
		self.messages.clear()
		with patch.object(frappe, "get_all", return_value=["ITEM-RAW"]):
			with self.assertRaises(frappe.ValidationError) as error:
				producer.validate_item_type(doc, "is_purchase_item", "purchase")
		self.assertEqual(str(error.exception), FRAMES[0].format("ITEM-RAW", "Custom purchase caption"))
