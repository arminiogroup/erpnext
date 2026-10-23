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

from erpnext.stock.doctype.stock_reconciliation import stock_reconciliation as producer
from erpnext.stock.doctype.stock_reservation_entry import stock_reservation_entry

FRAMES = (
	"{0} units are reserved for Item {1} in Warehouse {2}. Release the reserved stock before proceeding with the Stock Reconciliation.",
	"Release the reserved stock for the following items and warehouses before proceeding with the Stock Reconciliation:<br /><br />{0}",
	"<li>{0} units of Item {1} in Warehouse {2}</li>",
)
TARGETS = {
	"{0} units are reserved for Item {1} in Warehouse {2}. Release the reserved stock before proceeding with the Stock Reconciliation.": "Per l’articolo {1} nel magazzino {2} sono prenotate {0} unità. Rilasciare la prenotazione prima di procedere con la riconciliazione di magazzino.",
	"Release the reserved stock for the following items and warehouses before proceeding with the Stock Reconciliation:<br /><br />{0}": "Prima di procedere con la riconciliazione di magazzino, rilasciare le prenotazioni per i seguenti articoli e magazzini:<br /><br />{0}",
	"<li>{0} units of Item {1} in Warehouse {2}</li>": "<li>{0} unità dell’articolo {1} nel magazzino {2}</li>",
	"ITEM-RAW": "DO NOT TRANSLATE ITEM",
	"WH-RAW": "DO NOT TRANSLATE WAREHOUSE",
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

	def test_actual_qty_selection_action_and_reservation_matrix(self):
		for purpose, items in (
			(
				"Stock Reconciliation",
				[frappe._dict(item_code="ITEM-RAW", warehouse="WH-RAW", qty=2, current_qty=1)],
			),
			(
				producer.ADJUSTMENT_ENTRY,
				[
					frappe._dict(item_code="ITEM-RAW", warehouse="WH-RAW", qty=2, current_qty=1),
					frappe._dict(item_code="ITEM-RAW", warehouse="WH-RAW", qty=1, current_qty=2),
				],
			),
		):
			for action in ("submit", "cancel", "save"):
				for count in (0, 1, 2):
					with self.subTest(purpose=purpose, action=action, count=count):
						doc = producer.StockReconciliation.__new__(producer.StockReconciliation)
						doc.__dict__.update(purpose=purpose, items=deepcopy(items), _action=action)
						before = deepcopy(doc.__dict__)
						reads = []

						def reserved(codes, warehouses):
							reads.append((codes, warehouses))
							return (
								{
									("ITEM-RAW", "WH-RAW"): 2,
									**({("SECOND-RAW", "WH2-RAW"): 3} if count == 2 else {}),
								}
								if count and codes
								else {}
							)

						with patch.object(
							stock_reservation_entry,
							"get_sre_reserved_qty_for_items_and_warehouses",
							side_effect=reserved,
						):
							if count and purpose != producer.ADJUSTMENT_ENTRY:
								with self.assertRaises(frappe.ValidationError) as error:
									doc.validate_reserved_stock()
								if count == 1:
									expected = TARGETS[FRAMES[0]].format(
										producer.bold(2), producer.bold("ITEM-RAW"), producer.bold("WH-RAW")
									)
								else:
									expected = TARGETS[FRAMES[1]].format(
										"".join(
											TARGETS[FRAMES[2]].format(
												producer.bold(q), producer.bold(i), producer.bold(w)
											)
											for i, w, q in [
												("ITEM-RAW", "WH-RAW", 2),
												("SECOND-RAW", "WH2-RAW", 3),
											]
										)
									)
								self.assertEqual(str(error.exception), expected)
							else:
								doc.validate_reserved_stock()
						self.assertEqual(
							reads,
							[
								([], [])
								if purpose == producer.ADJUSTMENT_ENTRY
								else (["ITEM-RAW"], ["WH-RAW"])
							],
						)
						self._state(doc, before)

	def test_english_and_raw_identifier_spoof_control(self):
		self.messages.clear()
		doc = producer.StockReconciliation.__new__(producer.StockReconciliation)
		doc.__dict__.update(
			purpose="Stock Reconciliation",
			items=[frappe._dict(item_code="ITEM-RAW", warehouse="WH-RAW", qty=2, current_qty=1)],
			_action="cancel",
		)
		with patch.object(
			stock_reservation_entry,
			"get_sre_reserved_qty_for_items_and_warehouses",
			return_value={("ITEM-RAW", "WH-RAW"): 2},
		):
			with self.assertRaises(frappe.ValidationError) as error:
				doc.validate_reserved_stock()
		self.assertEqual(
			str(error.exception),
			FRAMES[0].format(producer.bold(2), producer.bold("ITEM-RAW"), producer.bold("WH-RAW")),
		)
