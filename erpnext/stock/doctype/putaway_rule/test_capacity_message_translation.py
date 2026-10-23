# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.tests import UnitTestCase
from frappe.utils import flt

from erpnext.stock.doctype.putaway_rule import putaway_rule

SOURCE = "Warehouse Capacity for Item '{0}' must be at least the existing stock level of {1} {2}."
TARGET = (
	"La capacità di stoccaggio per l’articolo '{0}' deve essere almeno pari alla giacenza attuale di {1} {2}."
)


class CapacityReads:
	db_type = "mariadb"

	def __init__(self, balance=0, factor=10, warehouse=("COMPANY-RAW", 0, 0), duplicate=None, stock_item=1):
		self.balance, self.factor, self.warehouse = balance, factor, warehouse
		self.duplicate, self.stock_item = duplicate, stock_item
		self.reads = []

	def exists(self, doctype, filters):
		assert (doctype, filters) == ("Putaway Rule", {"item_code": "ITEM-RAW", "warehouse": "WAREHOUSE-RAW"})
		self.reads.append(("duplicate", doctype, deepcopy(filters)))
		return self.duplicate

	def get_value(self, doctype, name, fields):
		self.reads.append(("get_value", doctype, name, deepcopy(fields)))
		if doctype == "Warehouse":
			assert (name, fields) == ("WAREHOUSE-RAW", ["company", "is_group", "disabled"])
			return self.warehouse
		assert (doctype, name, fields) == ("Item", "ITEM-RAW", "stock_uom")
		return "Nos"

	def sql(self, query, params=None, **kwargs):
		self.reads.append(("sql", query, deepcopy(params), kwargs))
		if "tabUOM Conversion Detail" in query:
			assert kwargs.get("pluck") == "conversion_factor"
			assert "ITEM-RAW" in params.values() and "Box" in params.values()
			return [self.factor]
		assert "tabStock Ledger Entry" in query
		assert "is_cancelled = 0" in query and "limit 1" in query
		assert params["item_code"] == "ITEM-RAW" and params["warehouse"] == "WAREHOUSE-RAW"
		assert str(params["posting_date"]) == "2026-10-21" and params["posting_time"] == "12:00:00"
		assert kwargs == {"as_dict": 1, "debug": False}
		return [frappe._dict(qty_after_transaction=self.balance)]

	def cached(self, doctype, name, field, **kwargs):
		assert (doctype, name) == ("Item", "ITEM-RAW")
		self.reads.append(("cached", doctype, name, deepcopy(field), kwargs))
		if field == "stock_uom":
			assert not kwargs
			return "Nos"
		if field == "is_stock_item":
			assert not kwargs
			return self.stock_item
		assert field == ["variant_of", "stock_uom"] and kwargs == {"as_dict": True}
		return frappe._dict(variant_of=None, stock_uom="Nos")

	def permission(self, *args, **kwargs):
		assert args == ("Item", "read") and kwargs == {"throw": True}
		self.reads.append(("permission", args, kwargs))
		return True

	def __getattr__(self, name):
		raise AssertionError(f"Unexpected database operation: {name}")


class TestPutawayCapacityMessage(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		self.addCleanup(setattr, frappe.local, "db", getattr(frappe.local, "db", None))
		self.addCleanup(setattr, frappe.local, "message_log", getattr(frappe.local, "message_log", []))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"

	def extracted(self, path):
		methods = get_method_map("erpnext") + get_method_map("frappe")
		relative = str(Path(putaway_rule.__file__).relative_to(Path(frappe.get_app_path("erpnext")).parent))
		method = next(method for pattern, method in methods if pathmatch(pattern, relative))
		self.assertEqual(method, "frappe.gettext.extractors.python.extract")
		return list(extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS))

	def dictionary(self):
		return {
			message: TARGET
			for _, message, _, context in self.extracted(Path(putaway_rule.__file__))
			if message == SOURCE and context is None
		}

	def validate_rule(
		self, capacity, uom=None, balance=0, disable=False, language="it", priority=1, **providers
	):
		db = CapacityReads(balance=balance, **providers)
		frappe.local.db = db
		frappe.local.message_log = []
		doc = putaway_rule.PutawayRule.__new__(putaway_rule.PutawayRule)
		doc.__dict__.update(
			doctype="Putaway Rule",
			name="RULE-RAW",
			company="COMPANY-RAW",
			item_code="ITEM-RAW",
			warehouse="WAREHOUSE-RAW",
			capacity=capacity,
			uom=uom,
			conversion_factor=1000,
			stock_capacity=999,
			disable=int(disable),
			priority=priority,
			flags=frappe._dict(),
		)
		before = deepcopy(doc.__dict__)
		error = None
		with (
			patch.object(frappe.local, "lang", language),
			patch(
				"frappe.translate.get_all_translations",
				return_value=self.dictionary() if language == "it" else {},
			),
			patch("frappe.get_cached_value", side_effect=db.cached),
			patch("frappe.has_permission", side_effect=db.permission),
			patch.object(putaway_rule, "nowdate", return_value="2026-10-21"),
			patch("erpnext.stock.utils.nowtime", return_value="12:00:00"),
		):
			try:
				doc.validate()
			except frappe.ValidationError as exc:
				error = str(exc)
		self.assertEqual(frappe.session.user, "translator@example.com")
		return error, db.reads, before, deepcopy(doc.__dict__), deepcopy(frappe.local.message_log)

	def assert_computed_state(self, result, capacity, uom=None, factor=10, disable=False):
		_, reads, before, after, _ = result
		expected = deepcopy(before)
		conversion = (factor or 1) if uom == "Box" else 1
		expected.update(
			stock_uom="Nos",
			uom=uom or "Nos",
			conversion_factor=conversion,
			stock_capacity=flt(conversion) * flt(capacity),
		)
		self.assertEqual(after, expected)
		self.assertEqual(
			[read[0] for read in reads],
			[
				"duplicate",
				"get_value",
				*([] if disable else ["cached"]),
				"cached",
				"cached",
				*(["sql"] if uom == "Box" else []),
				"get_value",
				"permission",
				"sql",
			],
		)
		self.assertEqual(before["conversion_factor"], 1000)
		self.assertEqual(before["stock_capacity"], 999)

	def test_configured_extraction_and_new_test_source(self):
		keys = [(message, context) for _, message, _, context in self.extracted(Path(putaway_rule.__file__))]
		self.assertIn((SOURCE, None), keys)
		self.assertEqual(self.extracted(Path(__file__)), [])

	def test_whole_english_and_italian_errors_keep_raw_item_uom_and_bold_stock(self):
		for language, frame in (("en", SOURCE), ("it", TARGET)):
			with self.subTest(language=language):
				result = self.validate_rule(1.9, "Box", 20, language=language)
				self.assert_computed_state(result, 1.9, "Box")
				self.assertEqual(result[0], frame.format("ITEM-RAW", "<strong>20</strong>", "Nos"))
				self.assertEqual(result[4][-1].message, result[0])
				self.assertEqual(result[4][-1].title, "Insufficient Capacity")

	def test_all_ten_stock_and_converted_capacity_boundaries(self):
		cases = [
			("below", 4.9, None, 5, False, True),
			("equal", 5, None, 5, False, False),
			("above", 5.1, None, 5, False, False),
			("converted below", 1.9, "Box", 20, False, True),
			("converted equal", 2, "Box", 20, False, False),
			("converted above", 2.1, "Box", 20, False, False),
			("zero balance", 0, None, 0, False, True),
			("zero below stock", 0, None, 1, False, True),
			("disabled equal", 2, "Box", 20, True, False),
			("disabled below", 1.9, "Box", 20, True, True),
		]
		for language, frame in (("en", SOURCE), ("it", TARGET)):
			for name, capacity, uom, balance, disabled, rejected in cases:
				with self.subTest(case=name, language=language):
					result = self.validate_rule(capacity, uom, balance, disabled, language)
					self.assert_computed_state(result, capacity, uom, disable=disabled)
					expected = None
					if name == "zero balance":
						expected = "Capacity must be greater than 0"
					elif rejected:
						expected = frame.format("ITEM-RAW", frappe.bold(balance), "Nos")
					self.assertEqual(result[0], expected)

	def test_conversion_precision_and_zero_factor_fallback_are_unchanged(self):
		for factor, capacity, balance, accepted in (
			(2.5, 2, 5, True),
			(2.5, 1.999, 5, False),
			(0, 5, 5, True),
		):
			with self.subTest(factor=factor, capacity=capacity):
				result = self.validate_rule(capacity, "Box", balance, factor=factor)
				self.assert_computed_state(result, capacity, "Box", factor)
				self.assertEqual(result[0] is None, accepted)

	def test_duplicate_and_warehouse_guards_leave_original_state_and_skip_capacity_reads(self):
		cases = [
			(
				{"duplicate": "OTHER-RULE"},
				"Putaway Rule already exists for Item <strong>ITEM-RAW</strong> in Warehouse <strong>WAREHOUSE-RAW</strong>.",
				["duplicate"],
			),
			(
				{"warehouse": ("COMPANY-RAW", 1, 0)},
				"Warehouse <strong>WAREHOUSE-RAW</strong> must be an enabled warehouse that is not a group.",
				["duplicate", "get_value"],
			),
			(
				{"warehouse": ("COMPANY-RAW", 0, 1)},
				"Warehouse <strong>WAREHOUSE-RAW</strong> must be an enabled warehouse that is not a group.",
				["duplicate", "get_value"],
			),
			(
				{"stock_item": 0},
				"Item <strong>ITEM-RAW</strong> is not a stock item.",
				["duplicate", "get_value", "cached"],
			),
			(
				{"warehouse": ("OTHER-COMPANY", 0, 0)},
				"Warehouse <strong>WAREHOUSE-RAW</strong> does not belong to Company <strong>COMPANY-RAW</strong>.",
				["duplicate", "get_value", "cached"],
			),
		]
		for providers, expected, operations in cases:
			with self.subTest(providers=providers):
				result = self.validate_rule(5, balance=5, **providers)
				self.assertEqual(result[0], expected)
				self.assertEqual(result[2], result[3])
				self.assertEqual([read[0] for read in result[1]], operations)

	def test_same_rule_disabled_warehouse_and_priority_preserve_validation_order(self):
		result = self.validate_rule(5, balance=5, duplicate="RULE-RAW")
		self.assertIsNone(result[0])
		self.assert_computed_state(result, 5)
		result = self.validate_rule(5, balance=5, disable=True, warehouse=("COMPANY-RAW", 1, 1), stock_item=0)
		self.assertIsNone(result[0])
		self.assert_computed_state(result, 5, disable=True)
		result = self.validate_rule(5, balance=5, priority=0)
		self.assertEqual(result[0], "Priority cannot be less than 1.")
		self.assert_computed_state(result, 5)
		result = self.validate_rule(4, balance=5, priority=0)
		self.assertEqual(result[0], TARGET.format("ITEM-RAW", "<strong>5</strong>", "Nos"))
		self.assert_computed_state(result, 4)
