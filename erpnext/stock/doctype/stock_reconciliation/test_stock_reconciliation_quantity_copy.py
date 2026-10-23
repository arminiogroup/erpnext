# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.stock.doctype.stock_reconciliation import stock_reconciliation as stock


def forbidden(*args, **kwargs):
	raise AssertionError(
		"Unapproved boundary reached: no persistence, job, user, settings or mail action is allowed"
	)


def meta_from_packaged(doctype):
	candidates = list(
		Path(__file__)
		.resolve()
		.parents[3]
		.glob(f"**/doctype/{frappe.scrub(doctype)}/{frappe.scrub(doctype)}.json")
	)
	assert len(candidates) == 1, (doctype, candidates)
	data = json.loads(candidates[0].read_text())
	meta = Meta.__new__(Meta)
	meta.__dict__.update(data)
	meta.__dict__.setdefault("default_print_format", None)
	meta.fields = [frappe._dict(field) for field in data["fields"]]
	return meta


SOURCE = "Row #{0}: Current Qty for Item {1} must be set and nonzero to save the current serial and batch bundle details."
ITALIAN = "Riga n. {0}: per salvare i dati del gruppo attuale di numeri di serie e lotti dell'articolo {1}, la quantità attuale deve essere impostata e diversa da zero."


class TestStockReconciliationQuantityCopy(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "en"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict(in_test=True)))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: {SOURCE: ITALIAN, "Item": "Articolo"} if lang == "it" else {},
			)
		)
		self.enterContext(patch.object(frappe, "cache", SimpleNamespace(hget=lambda *args, **kwargs: {})))
		self.enterContext(
			patch.object(
				frappe,
				"get_hooks",
				side_effect=lambda name=None, default=None, **kwargs: default if default is not None else [],
			)
		)
		self.enterContext(patch.object(frappe, "get_active_apps", return_value=["frappe", "erpnext"]))
		self.enterContext(patch.object(frappe, "get_installed_apps", return_value=["frappe", "erpnext"]))
		self.enterContext(
			patch.object(
				frappe,
				"client_cache",
				SimpleNamespace(
					get_doc=lambda doctype, name: frappe._dict(
						date_format="yyyy-mm-dd", time_format="HH:mm:ss", number_format="#,###.##"
					)
					if doctype == "Language"
					else forbidden()
				),
			)
		)

	def run_current_bundle(self, current, save):
		reads, writes = [], []
		row = frappe._dict(
			name="ROW-RAW",
			idx=2,
			item_code="Item",
			warehouse="WAREHOUSE-RAW",
			use_serial_batch_fields=0,
			reconcile_all_serial_batch=0,
			serial_and_batch_bundle="NEW-BUNDLE-RAW",
			current_serial_and_batch_bundle="CURRENT-BUNDLE-RAW",
			current_qty=current,
			current_valuation_rate=10,
			valuation_rate=10,
		)
		row.db_set = lambda values: writes.append(deepcopy(values))
		before = deepcopy(row)
		document = SimpleNamespace(
			items=[row],
			company="COMPANY-RAW",
			docstatus=0,
			name="RECO-RAW",
			posting_date="2026-10-21",
			posting_time="12:00:00",
		)
		document.get_bundle_for_specific_serial_batch = MethodType(
			stock.StockReconciliation.get_bundle_for_specific_serial_batch, document
		)
		document.has_change_in_serial_batch = MethodType(
			stock.StockReconciliation.has_change_in_serial_batch, document
		)

		def cached(doctype, name, field, **kwargs):
			reads.append(("cached", doctype, name, field))
			self.assertEqual((doctype, name), ("Item", "Item"))
			if field == "valuation_method":
				return "FIFO"
			self.assertEqual(field, ["has_serial_no", "has_batch_no"])
			return frappe._dict(has_serial_no=1, has_batch_no=1)

		def get_all(doctype, **kwargs):
			self.assertEqual(doctype, "Serial and Batch Entry")
			self.assertEqual(
				kwargs,
				{
					"fields": ["serial_no", "batch_no", "parent"],
					"filters": {"parent": ("in", ["NEW-BUNDLE-RAW", "CURRENT-BUNDLE-RAW"])},
					"order_by": "idx",
				},
			)
			reads.append(("get_all", doctype, deepcopy(kwargs)))
			return [
				frappe._dict(parent=parent, serial_no="SERIAL-RAW", batch_no="BATCH-RAW")
				for parent in ("NEW-BUNDLE-RAW", "CURRENT-BUNDLE-RAW")
			]

		error = None
		with ExitStack() as stack:
			stack.enter_context(patch.object(frappe, "db", SimpleNamespace(get_value=forbidden)))
			stack.enter_context(patch.object(frappe, "get_cached_value", side_effect=cached))
			stack.enter_context(patch.object(frappe, "get_all", side_effect=get_all))
			stack.enter_context(patch.object(frappe, "get_doc", side_effect=forbidden))
			stack.enter_context(patch.object(frappe, "delete_doc", side_effect=forbidden))
			try:
				result = stock.StockReconciliation.set_current_serial_and_batch_bundle(document, save=save)
				self.assertIsNone(result)
			except frappe.ValidationError as exc:
				error = str(exc)
		self.assertEqual(row, before)
		self.assertEqual(reads[-1][0:2], ("get_all", "Serial and Batch Entry"))
		return error, writes

	def test_italian_rejection_explains_current_quantity_without_translating_item_code(self):
		frappe.local.lang = "it"
		for current in (0, None, ""):
			with self.subTest(current=current):
				error, writes = self.run_current_bundle(current, True)
				self.assertEqual(error, ITALIAN.format(2, "Item"))
				self.assertEqual(writes, [])

	def test_english_fallback_keeps_the_whole_prerequisite_and_raw_item_code(self):
		frappe.local.lang = "en"
		for current in (0, None, ""):
			with self.subTest(current=current):
				error, writes = self.run_current_bundle(current, True)
				self.assertEqual(error, SOURCE.format(2, "Item"))
				self.assertEqual(writes, [])

	def test_retained_library_save_flag_and_raw_truthiness_compatibility(self):
		for current, save in ((5, True), (-3, True), ("0", True), (0, False), (None, False)):
			with self.subTest(current=current, save=save):
				error, writes = self.run_current_bundle(current, save)
				self.assertIsNone(error)
				self.assertEqual(
					writes,
					[
						{
							"current_serial_and_batch_bundle": "CURRENT-BUNDLE-RAW",
							"current_qty": current,
							"current_valuation_rate": 10,
						}
					]
					if save
					else [],
				)

	def test_current_quantity_is_derived_and_distinct_from_the_editable_quantity(self):
		meta = meta_from_packaged("Stock Reconciliation Item")
		fields = {field.fieldname: field for field in meta.fields}
		self.assertEqual(fields["current_qty"].label, "Current Qty")
		self.assertEqual(fields["current_qty"].fieldtype, "Float")
		self.assertEqual(fields["current_qty"].read_only, 1)
		self.assertEqual(fields["qty"].fieldtype, "Float")
		self.assertFalse(fields["qty"].get("read_only"))
