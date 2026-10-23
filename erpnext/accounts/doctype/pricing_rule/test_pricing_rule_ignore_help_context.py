# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.pricing_rule import pricing_rule as pricing


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


class TestPricingRuleIgnoreHelpContext(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "en"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict(in_test=True)))
		self.enterContext(patch("frappe.translate.get_all_translations", return_value={}))
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

	def test_actual_pricing_selection_obeys_transaction_flag_and_preserves_enabled_rule(self):
		template = frappe._dict(
			name="RULE-RAW",
			disable=0,
			selling=1,
			buying=1,
			apply_on="Item Code",
			item_code="ITEM-RAW",
			price_or_product_discount="Price",
			rate_or_discount="Discount Percentage",
			discount_percentage=10,
			currency="EUR",
			min_qty=0,
			max_qty=0,
			min_amt=0,
			max_amt=0,
			priority=0,
			mixed_conditions=0,
			is_cumulative=0,
			apply_multiple_pricing_rules=0,
			has_priority=0,
			apply_rule_on_other=None,
			coupon_code_based=0,
			suggestion=0,
			validate_applied_rule=0,
			margin_type=None,
			for_price_list=None,
		)
		for doctype in ("Sales Order", "Purchase Order"):
			for ignore in (0, 1):
				reads = []
				rule = deepcopy(template)
				document_meta = meta_from_packaged(doctype)

				def exists(dt, name):
					reads.append(["exists", dt, name])
					return False

				def count(dt, **kwargs):
					assert dt == "Pricing Rule"
					reads.append(["count", dt])
					return 1

				def sql(query, values=None, **kwargs):
					assert "`tabPricing Rule`" in query and "`tabPricing Rule Item Code`" in query
					assert values["item_code"] == "ITEM-RAW"
					assert "`tabPricing Rule`.disable = 0" in query
					reads.append(["sql", "Pricing Rule Item Code", deepcopy(values)])
					return [deepcopy(rule)]

				def cached(dt, name, field, *args, **kwargs):
					assert (dt, name, field) == ("Item", "ITEM-RAW", ("item_group", "brand"))
					reads.append(["cached", dt, name, list(field)])
					return None

				def cached_doc(dt, name):
					assert (dt, name) == ("Pricing Rule", "RULE-RAW")
					reads.append(["cached_doc", dt, name])
					return deepcopy(rule)

				item = {
					"doctype": f"{doctype} Item",
					"name": "ROW-RAW",
					"parent": "DOC-RAW",
					"parenttype": doctype,
					"item_code": "ITEM-RAW",
					"qty": 2,
					"stock_qty": 2,
					"price_list_rate": 100,
					"variant_of": None,
				}
				args = {
					"doctype": doctype,
					"name": "DOC-RAW",
					"items": [item],
					"ignore_pricing_rule": ignore,
					"transaction_date": "2026-10-21",
					"currency": "EUR",
					"conversion_rate": 1,
					"plc_conversion_rate": 1,
				}
				before = deepcopy((args, rule))
				with ExitStack() as stack:
					stack.enter_context(
						patch.object(frappe, "db", SimpleNamespace(exists=exists, count=count, sql=sql))
					)
					stack.enter_context(
						patch.object(
							frappe,
							"get_all",
							side_effect=lambda dt, **kw: [("ITEM-RAW", 0)] if dt == "Item" else forbidden(),
						)
					)
					stack.enter_context(patch.object(frappe, "has_permission", return_value=True))
					stack.enter_context(patch.object(frappe, "get_meta", return_value=document_meta))
					stack.enter_context(patch.object(frappe, "get_cached_value", side_effect=cached))
					stack.enter_context(patch.object(frappe, "get_cached_doc", side_effect=cached_doc))
					stack.enter_context(patch.object(frappe, "get_doc", side_effect=forbidden))
					stack.enter_context(
						patch.object(frappe, "get_system_settings", return_value="Banker's Rounding (legacy)")
					)
					result = pricing.apply_pricing_rule(deepcopy(args))
				assert (args, rule) == before
				assert len(result) == 1 and result[0].name == "ROW-RAW"
				if ignore:
					assert not any(r[0] in ("count", "sql", "cached_doc") for r in reads)
					assert "has_pricing_rule" not in result[0]
				else:
					assert result[0].has_pricing_rule == 1
					assert result[0].discount_percentage == 10 and result[0].discount_amount == 10
					assert json.loads(result[0].pricing_rules) == ["RULE-RAW"]
				assert rule.disable == 0

	def test_shared_packaged_option_keeps_raw_check_field_and_existing_access_levels(self):
		for doctype in (
			"Supplier Quotation",
			"Purchase Order",
			"Quotation",
			"Sales Order",
			"POS Profile",
			"Sales Invoice",
			"POS Invoice",
			"Purchase Invoice",
			"Pick List",
			"Delivery Note",
			"Purchase Receipt",
		):
			with self.subTest(doctype=doctype):
				meta = meta_from_packaged(doctype)
				field = next(field for field in meta.fields if field.fieldname == "ignore_pricing_rule")
				self.assertEqual(field.label, "Ignore Pricing Rule")
				self.assertEqual(field.fieldtype, "Check")
				self.assertIn(field.get("permlevel", 0), (0, 1))
