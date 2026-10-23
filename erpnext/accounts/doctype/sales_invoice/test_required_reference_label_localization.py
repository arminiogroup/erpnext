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

from erpnext.accounts.doctype.sales_invoice import sales_invoice as producer

FRAME = "{0} is mandatory for Item {1}"
FRAMES = (FRAME,)
TARGETS = {
	FRAME: "{0}: riferimento obbligatorio per l’articolo {1}.",
	"Sales Order": "Ordine di vendita",
	"Delivery Note": "Documento di trasporto",
	"ITEM-RAW": "DO NOT TRANSLATE RECORDS",
}


class TestSalesInvoiceReferenceTypeLabel(UnitTestCase):
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

	def test_both_reference_guards_preserve_raw_keys_and_order(self):
		for key, setting, flag in (
			("Sales Order", "so_required", "is_pos"),
			("Delivery Note", "dn_required", "update_stock"),
		):
			for scenario in ("missing", "linked", "exempt", "flag", "return", "No", "no-code"):
				with self.subTest(key=key, scenario=scenario):
					reads = []
					row = frappe._dict(
						item_code="" if scenario == "no-code" else "ITEM-RAW",
						sales_order="",
						delivery_note="",
					)
					row[frappe.scrub(key)] = "REF-RAW" if scenario == "linked" else ""
					doc = producer.SalesInvoice.__new__(producer.SalesInvoice)
					doc.__dict__.update(
						doctype="Sales Invoice",
						customer="CUSTOMER-RAW",
						is_return=scenario == "return",
						is_pos=0,
						update_stock=0,
						items=[row],
					)
					setattr(doc, flag, int(scenario == "flag"))
					before = deepcopy(doc.__dict__)

					def value(dt, name, field, **kw):
						reads.append((dt, name, field))
						self.assertEqual((dt, name, field), ("Customer", "CUSTOMER-RAW", setting))
						return scenario == "exempt"

					self._database(get_value=value)
					settings = frappe._dict(
						so_required="Yes" if setting == "so_required" and scenario != "No" else "No",
						dn_required="Yes" if setting == "dn_required" and scenario != "No" else "No",
					)
					with patch.object(frappe, "cache", SimpleNamespace(get_value=lambda cache_key: settings)):
						if scenario == "missing":
							with self.assertRaises(frappe.ValidationError) as caught:
								doc.so_dn_required()
							self.assertEqual(
								str(caught.exception), TARGETS[FRAME].format(TARGETS[key], "ITEM-RAW")
							)
						else:
							self.assertIsNone(doc.so_dn_required())
					self._state(doc, before)
					self.assertEqual(self.meta_reads, [])
					self.assertEqual(len(reads), 0 if scenario in ("return", "No") else 1)

	def test_empty_dictionary_keeps_raw_reference_and_record(self):
		self.messages.clear()
		doc = producer.SalesInvoice.__new__(producer.SalesInvoice)
		doc.__dict__.update(
			is_return=0,
			customer="C-RAW",
			is_pos=0,
			update_stock=0,
			items=[frappe._dict(item_code="ITEM-RAW", sales_order="")],
		)
		self._database(get_value=lambda *a, **kw: 0)
		with patch.object(
			frappe,
			"cache",
			SimpleNamespace(get_value=lambda key: frappe._dict(so_required="Yes", dn_required="No")),
		):
			with self.assertRaises(frappe.ValidationError) as caught:
				doc.so_dn_required()
		self.assertEqual(str(caught.exception), FRAME.format("Sales Order", "ITEM-RAW"))
