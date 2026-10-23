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
from frappe.query_builder.builder import MariaDB
from frappe.tests import UnitTestCase

from erpnext.controllers import subcontracting_controller as producer

FRAMES = (
	"No materials are available to return against {0} {1}.",
	"Row {0}: Item {1} must be linked to a {2}.",
	"The Batch No {0} has not been supplied against the {1} {2}",
	"The Serial Nos {0} have not been supplied against the {1} {2}",
)
TARGETS = {
	"No materials are available to return against {0} {1}.": "Non sono disponibili materiali da restituire per {0} {1}.",
	"Row {0}: Item {1} must be linked to a {2}.": "Riga {0}: per l’articolo {1} è obbligatorio un riferimento a {2}.",
	"The Batch No {0} has not been supplied against the {1} {2}": "Lotti non forniti per {1} {2}: {0}.",
	"The Serial Nos {0} have not been supplied against the {1} {2}": "Numeri di serie non forniti per {1} {2}: {0}.",
	"Purchase Order Item": "Riga dell'ordine d'acquisto",
	"Sales Order Item": "Riga dell'ordine di vendita",
	"Subcontracting Order": "Ordine di conto lavoro",
	"Subcontracting Inward Order": "Ordine di conto lavoro attivo",
	"Purchase Order": "Ordine d'acquisto",
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

	def test_actual_missing_child_reference_both_directions(self):
		for dtype, child in (
			("Subcontracting Order", "Purchase Order Item"),
			("Subcontracting Inward Order", "Sales Order Item"),
		):
			with self.subTest(dtype=dtype):
				row = frappe._dict(
					idx=1,
					item_code="ITEM-RAW",
					item_name="BUSINESS ITEM",
					secondary_item_type="",
					valuation_type="",
					delivery_warehouse="DELIVERY-RAW",
					bom=None,
				)
				doc = producer.SubcontractingController.__new__(producer.SubcontractingController)
				doc.__dict__.update(
					doctype=dtype,
					items=[row],
					purchase_order="PO-RAW",
					sales_order="SO-RAW",
					customer_warehouse="CUSTOMER-WH",
				)
				before = deepcopy(doc.__dict__)
				reads = []
				self._database(get_value=lambda *a: reads.append(a) or (1, 1))
				with self.assertRaises(frappe.ValidationError) as error:
					doc.validate_items()
				self.assertEqual(
					str(error.exception), TARGETS[FRAMES[1]].format(1, "BUSINESS ITEM", TARGETS[child])
				)
				self.assertEqual(reads, [("Item", "ITEM-RAW", ["is_stock_item", "is_sub_contracted_item"])])
				self._state(doc, before)

	def test_batch_and_serial_physical_labels_canonical_order_link(self):
		for dtype, field in (
			("Subcontracting Order", "subcontracting_order"),
			("Subcontracting Inward Order", "subcontracting_inward_order"),
		):
			doc = producer.SubcontractingController.__new__(producer.SubcontractingController)
			doc.__dict__.update(subcontract_data=frappe._dict(order_doctype=dtype, order_field=field))
			key = ("ITEM-RAW", "FG-RAW", "ORDER-RAW")
			doc._SubcontractingController__transferred_items = {
				key: {"batch_no": {"BATCH-OK": 5}, "serial_no": ["SERIAL-OK"]}
			}
			row = frappe._dict(batch_no="BATCH-BAD", **{field: "ORDER-RAW"})
			before = deepcopy(doc.__dict__)
			with patch.object(frappe, "get_cached_value", return_value="PHYSICAL<&>BATCH"):
				with self.assertRaises(frappe.ValidationError) as error:
					doc._SubcontractingController__validate_batch_no(row, key, [])
			link = producer.get_link_to_form(dtype, "ORDER-RAW")
			self.assertEqual(
				str(error.exception),
				TARGETS[FRAMES[2]].format(frappe.bold("PHYSICAL&lt;&amp;&gt;BATCH"), TARGETS[dtype], link),
			)
			with patch.object(frappe, "get_all", return_value=[["SERIAL-BAD", "PHYSICAL<&>SERIAL"]]):
				with self.assertRaises(frappe.ValidationError) as error:
					doc._SubcontractingController__validate_serial_no(
						row, key, [frappe._dict(serial_no="SERIAL-BAD")]
					)
			self.assertEqual(
				str(error.exception),
				TARGETS[FRAMES[3]].format("PHYSICAL&lt;&amp;&gt;SERIAL", TARGETS[dtype], link),
			)
			self._state(doc, before)
			row.batch_no = "BATCH-OK"
			doc._SubcontractingController__validate_batch_no(row, key, [])
			doc._SubcontractingController__validate_serial_no(row, key, [frappe._dict(serial_no="SERIAL-OK")])

	def test_material_return_real_availability_to_mapper_boundary(self):
		class MapperBoundary(Exception):
			pass

		for dtype in ("Subcontracting Order", "Purchase Order"):
			for transferred in (False, True):
				with self.subTest(dtype=dtype, transferred=transferred):
					doc = producer.SubcontractingController.__new__(producer.SubcontractingController)
					doc.__dict__.update(
						doctype=dtype,
						name="ORDER-RAW",
						supplier_warehouse="SUPPLIER-WH",
						subcontract_data=frappe._dict(
							order_doctype="Subcontracting Order",
							order_field="subcontracting_order",
							rm_detail_field="sco_rm_detail",
							receipt_supplied_items_field="Subcontracting Receipt Supplied Item",
						),
					)
					args = frappe._dict(
						order_doctype=dtype, subcontract_order="ORDER-RAW", rm_details='["ROW-RAW"]'
					)
					reads = []

					def sql(query, values=None, **kw):
						reads.append((str(query), values, kw))
						self.assertTrue(str(query).lstrip().startswith("SELECT"))
						return (
							[
								frappe._dict(
									voucher_no=None,
									rm_item_code="ITEM-RAW",
									main_item_code="FG-RAW",
									subcontracting_order="ORDER-RAW",
									qty=5,
									batch_no=None,
									serial_no=None,
									sco_rm_detail="ROW-RAW",
								)
							]
							if transferred
							else []
						)

					self._database(
						sql=sql, get_single_value=lambda *a: "Material Transferred for Subcontract"
					)
					with patch.object(frappe, "qb", MariaDB), patch.object(
						frappe, "get_cached_doc", return_value=doc
					), patch.object(
						frappe, "get_cached_value", return_value="Material Transferred for Subcontract"
					), patch.object(frappe, "get_all", return_value=[]), patch.object(
						frappe, "flags", frappe._dict(args=args)
					), patch.object(producer, "get_mapped_doc", side_effect=MapperBoundary) as mapper:
						with self.assertRaises(
							MapperBoundary if transferred else frappe.ValidationError
						) as error:
							producer.get_materials_from_supplier("SOURCE-RAW")
					if transferred:
						self.assertEqual(mapper.call_args.args[:2], (dtype, "ORDER-RAW"))
						self.assertTrue(mapper.call_args.kwargs["ignore_child_tables"])
					else:
						self.assertEqual(
							str(error.exception), TARGETS[FRAMES[0]].format(TARGETS[dtype], "ORDER-RAW")
						)
					self.assertEqual(args.rm_details, '["ROW-RAW"]')
					self.assertEqual(doc.subcontract_orders, ["ORDER-RAW"])
					self.assertEqual(bool(doc.available_materials), transferred)
					self.assertTrue(reads)
