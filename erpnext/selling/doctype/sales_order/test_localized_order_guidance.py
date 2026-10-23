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

from erpnext.selling.doctype.sales_order import sales_order as producer

FRAMES = (
	"Quotation {0} not of type {1}",
	"Sales Order {0} already exists against Customer's Purchase Order {1}. To allow multiple Sales Orders, Enable {2} in {3}",
)
TARGETS = {
	"Quotation {0} not of type {1}": "Il preventivo {0} non è di tipo {1}.",
	"Sales Order {0} already exists against Customer's Purchase Order {1}. To allow multiple Sales Orders, Enable {2} in {3}": "Esiste già l’ordine di vendita {0} per l’ordine d’acquisto del cliente {1}. Per consentire più ordini di vendita, attivare {2} in {3}.",
	"Sales": "Vendite",
	"Maintenance": "Manutenzione",
	"Shopping Cart": "Carrello della spesa",
	"Selling Settings": "Impostazioni delle vendite",
	"'Allow Multiple Sales Orders Against a Customer's Purchase Order'": "'Consenti più ordini di vendita per un ordine di acquisto del cliente'",
}

SETTING_LABEL_SOURCE = "'Allow Multiple Sales Orders Against a Customer's Purchase Order'"


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

	def test_quotation_type_notice_and_raw_query_matrix(self):
		for kind in ("Sales", "Maintenance", "Shopping Cart"):
			for valid in (False, True):
				with self.subTest(kind=kind, valid=valid):
					doc = producer.SalesOrder.__new__(producer.SalesOrder)
					doc.__dict__.update(
						doctype="Sales Order",
						order_type=kind,
						items=[frappe._dict(prevdoc_docname="QUOTE-RAW")],
					)
					before = deepcopy(doc.__dict__)
					reads, notices = [], []

					def rows(dt, **kw):
						reads.append((dt, kw))
						return ["QUOTE-RAW"] if valid else []

					with patch.object(frappe, "get_all", side_effect=rows), patch.object(
						frappe, "msgprint", side_effect=notices.append
					):
						self.assertIsNone(doc.validate_sales_mntc_quotation())
					self.assertEqual(
						reads,
						[
							(
								"Quotation",
								{
									"filters": {"name": ["in", ["QUOTE-RAW"]], "order_type": kind},
									"pluck": "name",
								},
							)
						],
					)
					self.assertEqual(
						notices, [] if valid else [TARGETS[FRAMES[0]].format("QUOTE-RAW", TARGETS[kind])]
					)
					self._state(doc, before, "Sales Order" if not valid else None)
		doc = producer.SalesOrder.__new__(producer.SalesOrder)
		doc.__dict__.update(items=[frappe._dict(prevdoc_docname=None)])
		self.assertIsNone(doc.validate_sales_mntc_quotation())

	def test_customer_po_guidance_setting_link_and_canonical_controls(self):
		for allows, exists, skip in (
			(False, True, False),
			(True, True, False),
			(False, False, False),
			(False, True, True),
		):
			with self.subTest(allows=allows, exists=exists, skip=skip):
				doc = producer.SalesOrder.__new__(producer.SalesOrder)
				doc.__dict__.update(
					doctype="Sales Order",
					name="SELF-RAW",
					po_no="PO-RAW",
					po_date=None,
					customer="CUSTOMER-RAW",
					order_type="Sales",
					skip_delivery_note=int(skip),
					items=[frappe._dict(is_stock_item=1, delivery_date=None)],
				)
				before, reads, notices = deepcopy(doc.__dict__), [], []

				def value(dt, **kw):
					reads.append((dt, kw))
					return "OTHER-SO-RAW" if exists else None

				self._database(get_value=value)
				with patch.object(frappe, "get_single_value", return_value=allows), patch.object(
					frappe, "msgprint", side_effect=lambda *a, **kw: notices.append((a, kw))
				):
					if exists and not allows and not skip:
						with self.assertRaises(frappe.ValidationError) as error:
							doc.validate_po()
						expected = TARGETS[FRAMES[1]].format(
							frappe.bold("OTHER-SO-RAW"),
							frappe.bold("PO-RAW"),
							frappe.bold(frappe._(SETTING_LABEL_SOURCE)),
							producer.get_link_to_form(
								"Selling Settings", "Selling Settings", TARGETS["Selling Settings"]
							),
						)
						self.assertEqual(str(error.exception), expected)
					else:
						doc.validate_po()
				self.assertEqual(bool(reads), not skip)
				if reads:
					self.assertEqual(
						reads[0],
						(
							"Sales Order",
							{
								"filters": {
									"po_no": "PO-RAW",
									"name": ["!=", "SELF-RAW"],
									"docstatus": ["<", 2],
									"customer": "CUSTOMER-RAW",
								},
								"fieldname": "name",
							},
						),
					)
				self.assertEqual(bool(notices), exists and allows and not skip)
				self._state(doc, before)

	def test_empty_dictionary_and_custom_select_context(self):
		self.messages.clear()
		doc = producer.SalesOrder.__new__(producer.SalesOrder)
		doc.__dict__.update(
			doctype="Sales Order", order_type="Maintenance", items=[frappe._dict(prevdoc_docname="QUOTE-RAW")]
		)
		with patch.object(frappe, "get_all", return_value=[]), patch.object(frappe, "msgprint") as notice:
			doc.validate_sales_mntc_quotation()
			self.assertEqual(notice.call_args.args[0], FRAMES[0].format("QUOTE-RAW", "Maintenance"))
		self.messages.update(TARGETS)
		self.messages["Maintenance:Sales Order.order_type"] = "Tipo personalizzato"
		with patch.object(frappe, "get_all", return_value=[]), patch.object(frappe, "msgprint") as notice:
			doc.validate_sales_mntc_quotation()
			self.assertIn("Tipo personalizzato", notice.call_args.args[0])
