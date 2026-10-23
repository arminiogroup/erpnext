# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import hashlib
import itertools
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.query_builder.builder import MariaDB
from frappe.tests import UnitTestCase
from frappe.utils import flt

from erpnext.accounts.services import advances
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.controllers.selling_controller import SellingController

PATHS = {
	"Purchase Order": "buying/doctype/purchase_order/purchase_order.json",
	"Sales Team": "selling/doctype/sales_team/sales_team.json",
	"Job Card Secondary Item": "manufacturing/doctype/job_card_secondary_item/job_card_secondary_item.json",
	"Sales Order": "selling/doctype/sales_order/sales_order.json",
}
CHANGED_FIELDS = {
	"Purchase Order": "advance_paid",
	"Sales Team": "allocated_amount",
	"Job Card Secondary Item": "quantity_and_rate",
}
SCHEMA_DIGESTS = {
	"Purchase Order": "c03c64239f015fd976a325f3993f128f5acf5026d355f610f3c9d067ecb24069",
	"Sales Team": "cb52ec2c0880697d75695523dbd5530029da6d6cd527529db9b272aa6979f152",
	"Job Card Secondary Item": "298c83f46fc7c1a33975221ddc67a557ea63a2aa1dcae8cc2f013ef32806b314",
}
TARGETS = {
	"Advance Paid (Supplier Account Currency)": "Acconti versati (valuta del conto fornitore)",
	"Allocated Commissionable Amount": "Quota dell’importo soggetto a provvigione",
	"Quantity": "Quantità",
}


def read_schema(doctype):
	return json.loads(Path(frappe.get_app_path("erpnext", PATHS[doctype])).read_text())


def make_meta(data):
	meta = Meta.__new__(Meta)
	fields = [frappe._dict(parent=data["name"], **field) for field in data["fields"]]
	meta.__dict__.update(
		data, doctype="DocType", fields=fields, _fields={field.fieldname: field for field in fields}
	)
	return meta


def extracted_keys():
	keys = set()
	paths = [*PATHS.values(), "buying/doctype/purchase_order_item/purchase_order_item.json"]
	for relative in paths:
		path = Path(frappe.get_app_path("erpnext", relative))
		method = next(
			method
			for pattern, method in get_method_map("erpnext") + get_method_map("frappe")
			if pathmatch(pattern, "erpnext/" + relative)
		)
		keys.update(
			message
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
			if not context and isinstance(message, str)
		)
	return keys


class MemoryDatabase:
	db_type = "mariadb"

	def __init__(self):
		self.amount = 123.456
		self.currency = "USD"
		self.calls = []

	def get_default(self, fieldname):
		self.calls.append(("default", fieldname))
		return "2" if fieldname == "currency_precision" else "3" if fieldname == "float_precision" else None

	def sql(self, query, params, *args, **kwargs):
		assert query.startswith("SELECT ABS(SUM(") and "`tabAdvance Payment Ledger Entry`" in query
		assert "MAX(`currency`)" in query and "`delinked`=0" in query
		assert kwargs == {"as_dict": True} and not args
		self.calls.append(("select", query, params, kwargs))
		return [frappe._dict(amount=self.amount, account_currency=self.currency)]

	def set_value(self, *args, **kwargs):
		# Record the storage boundary without calling any database writer.
		self.calls.append(("set_value_boundary", args, kwargs))

	def get_all(self, doctype, **kwargs):
		assert doctype == "Sales Person"
		self.calls.append(("get_all", doctype, kwargs))
		return [frappe._dict(name=name, enabled=1) for name in kwargs["filters"]["name"][1]]


class TestPriceCostMetadataWording(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.flags, "company_currency", frappe.flags.get("company_currency"))
		for key in ("lang", "message_log", "db"):
			self.addCleanup(setattr, frappe.local, key, getattr(frappe.local, key, None))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		keys = extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={key: value for key, value in TARGETS.items() if key in keys},
			)
		)
		self.schemas = {name: read_schema(name) for name in PATHS}
		self.metas = {name: make_meta(data) for name, data in self.schemas.items()}
		self.enterContext(patch("frappe.get_meta", side_effect=lambda name: self.metas[name]))
		self.enterContext(
			patch(
				"frappe.get_system_settings",
				side_effect=lambda key: "Banker's Rounding" if key == "rounding_method" else None,
			)
		)
		self.enterContext(patch.object(frappe, "qb", MariaDB))
		self.db = MemoryDatabase()
		frappe.local.db = self.db
		self.enterContext(
			patch.object(
				advances,
				"set_advance_payment_status",
				side_effect=lambda doc: self.db.calls.append(("status_boundary", doc.doctype, doc.name)),
			)
		)

	def advance(self, company_currency="EUR", account_currency="USD", amount=123.456):
		frappe.flags.company_currency = {"Quantity": company_currency}
		self.db.currency, self.db.amount = account_currency, amount
		self.db.calls = []
		doc = AccountsController.__new__(AccountsController)
		doc.__dict__.update(
			doctype="Purchase Order",
			name="Allocated Commissionable Amount",
			company="Quantity",
			currency="GBP",
			party_account_currency=account_currency,
			advance_paid=0,
			flags=frappe._dict(),
		)

		def record(fieldname, value, **kwargs):
			self.db.calls.append(("db_set_boundary", fieldname, value, kwargs))
			doc.set(fieldname, value)

		doc.db_set = record
		# Calls the real ledger query and updater through the existing controller shim.
		doc.set_total_advance_paid()
		self.assertIs(doc.meta, self.metas["Purchase Order"])
		self.assertEqual(doc._precision, {"main": {"advance_paid": 2}})
		self.assertEqual(doc.advance_paid, flt(amount, 2))
		self.assertEqual(
			(doc.company_currency, doc.currency, doc.party_account_currency),
			(company_currency, "GBP", account_currency),
		)
		select = next(call for call in self.db.calls if call[0] == "select")
		self.assertEqual(
			list(select[2].values()), ["Quantity", "Purchase Order", "Allocated Commissionable Amount"]
		)
		self.assertIn(
			(
				"set_value_boundary",
				("Purchase Order", doc.name, "party_account_currency", account_currency),
				{},
			),
			self.db.calls,
		)
		self.assertIn(("db_set_boundary", "advance_paid", flt(amount, 2), {}), self.db.calls)
		return doc

	def commission(self, rate=10, eligible=1000):
		doc = SellingController.__new__(SellingController)
		team = []
		for name, percentage in (("Quantity", 60), ("Allocated Commissionable Amount", 40)):
			row = Document.__new__(Document)
			row.__dict__.update(
				doctype="Sales Team",
				name=name,
				parentfield="sales_team",
				sales_person=name,
				allocated_percentage=percentage,
				commission_rate=5,
				flags=frappe._dict(),
			)
			team.append(row)
		doc.__dict__.update(
			doctype="Sales Order",
			name="MEMORY-ONLY",
			company="Quantity",
			commission_rate=rate,
			amount_eligible_for_commission=0,
			flags=frappe._dict(),
			net_total=eligible + 2000,
			items=[
				frappe._dict(base_net_amount=eligible, grant_commission=1),
				frappe._dict(base_net_amount=2000, grant_commission=0),
			],
			sales_team=team,
		)
		self.db.calls = []
		doc.calculate_commission()
		doc.calculate_contribution()
		self.assertIs(doc.meta, self.metas["Sales Order"])
		self.assertTrue(all(row.meta is self.metas["Sales Team"] for row in team))
		self.assertEqual(doc.amount_eligible_for_commission, eligible)
		self.assertEqual(doc.total_commission, flt(eligible * rate / 100, 2))
		self.assertEqual(
			[row.allocated_amount for row in team], [flt(eligible * 0.6, 2), flt(eligible * 0.4, 2)]
		)
		self.assertEqual(
			[row.incentives for row in team], [flt(row.allocated_amount * 0.05, 2) for row in team]
		)
		self.assertEqual([row.sales_person for row in team], ["Quantity", "Allocated Commissionable Amount"])
		return doc

	def test_advance_label_matches_actual_supplier_account_currency(self):
		for currency in ("EUR", "USD"):
			with self.subTest(account_currency=currency):
				doc = self.advance(account_currency=currency)
				field = doc.meta.get_field("advance_paid")
				self.assertEqual(
					(field.fieldtype, field.options, field.read_only),
					("Currency", "party_account_currency", 1),
				)
				self.assertEqual(
					doc.meta.get_translated_label("advance_paid"),
					TARGETS["Advance Paid (Supplier Account Currency)"],
				)

	def test_allocation_label_matches_commissionable_amount_not_whole_net_total(self):
		doc = self.commission()
		self.assertEqual(doc.net_total, 3000)
		self.assertEqual([row.allocated_amount for row in doc.sales_team], [600, 400])
		field = self.metas["Sales Team"].get_field("allocated_amount")
		self.assertEqual(
			(field.fieldtype, field.options, field.read_only),
			("Currency", "Company:company:default_currency", 1),
		)
		self.assertEqual(
			self.metas["Sales Team"].get_translated_label("allocated_amount"),
			TARGETS["Allocated Commissionable Amount"],
		)

	def test_secondary_item_section_describes_its_actual_quantity_fields(self):
		meta = self.metas["Job Card Secondary Item"]
		order = self.schemas[meta.name]["field_order"]
		after_section = order[order.index("quantity_and_rate") + 1 :]
		self.assertEqual(after_section, ["stock_qty", "column_break_6", "stock_uom"])
		self.assertEqual(meta.get_field("stock_qty").fieldtype, "Float")
		self.assertEqual(
			(meta.get_field("stock_uom").fieldtype, meta.get_field("stock_uom").options), ("Link", "UOM")
		)
		self.assertFalse(
			any(
				field.fieldtype == "Currency"
				or field.fieldname in ("rate", "amount", "base_rate", "base_amount")
				for field in meta.fields
			)
		)
		self.assertEqual(meta.get_translated_label("quantity_and_rate"), TARGETS["Quantity"])

	def test_whole_schema_retains_all_other_fields_options_order_and_permissions(self):
		for name in CHANGED_FIELDS:
			with self.subTest(doctype=name):
				data = deepcopy(self.schemas[name])
				data.pop("modified")
				next(field for field in data["fields"] if field["fieldname"] == CHANGED_FIELDS[name]).pop(
					"label"
				)
				if name == "Sales Team":
					self.assertIn(data.get("row_format"), (None, "Dynamic"))
					data.pop("row_format", None)
				if name == "Job Card Secondary Item":
					self.assertIn(data.get("index_web_pages_for_search"), (None, 1))
					data.pop("index_web_pages_for_search", None)
				self.assertEqual(
					hashlib.sha256(
						json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
					).hexdigest(),
					SCHEMA_DIGESTS[name],
				)

	def test_serializer_normalizations_match_current_doctype_schema(self):
		data = json.loads(
			Path(frappe.get_app_path("frappe", "core/doctype/doctype/doctype.json")).read_text()
		)
		meta = make_meta(data)
		self.assertEqual(meta.get_field("row_format").default, "Dynamic")
		self.assertEqual((self.metas["Sales Team"].get("row_format") or "Dynamic").upper(), "DYNAMIC")
		self.assertIsNone(meta.get_field("index_web_pages_for_search"))

	def test_advance_currency_and_amount_parity_matrix(self):
		cases = []
		for company, account, amount in itertools.product(
			("EUR", "USD"), ("EUR", "USD", "JPY"), (0, 123.456, 987.654)
		):
			doc = self.advance(company, account, amount)
			cases.append(
				(
					doc.doctype,
					doc.name,
					doc.company,
					company,
					account,
					amount,
					doc.advance_paid,
					deepcopy(self.db.calls),
				)
			)
		self.parity_evidence = {
			"advance_cases": cases,
			"sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
		}

	def test_commission_and_allocation_parity_matrix(self):
		cases = []
		for rate, eligible in itertools.product((0, 10, 100), (0, 1000)):
			doc = self.commission(rate, eligible)
			cases.append(
				(
					rate,
					eligible,
					doc.net_total,
					doc.total_commission,
					doc.amount_eligible_for_commission,
					[
						(
							row.sales_person,
							row.allocated_percentage,
							row.allocated_amount,
							row.commission_rate,
							row.incentives,
						)
						for row in doc.sales_team
					],
					deepcopy(self.db.calls),
				)
			)
		self.parity_evidence = {
			"commission_cases": cases,
			"sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
		}
