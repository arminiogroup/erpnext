# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import hashlib
import itertools
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.model.meta import Meta
from frappe.query_builder.builder import MariaDB
from frappe.tests import UnitTestCase

from erpnext.accounts import general_ledger as ledger
from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice
from erpnext.accounts.doctype.purchase_invoice.services.gl_composer import PurchaseInvoiceGLComposer
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.accounts.doctype.sales_invoice.services.gl_composer import SalesInvoiceGLComposer

MENTION = "Please mention '{0}' in Company: {1}"
SET = "Please set '{0}' in Company: {1}"
OPENING = "Opening Invoice has rounding adjustment of {0}.<br><br> '{1}' account is required to post these values. Please set it in Company: {2}.<br><br> Or, '{3}' can be enabled to not post any rounding adjustment."
TARGETS = {
	MENTION: "Specificare {0} nell’azienda {1}.",
	SET: "Impostare {0} nell’azienda {1}.",
	OPENING: "La fattura di apertura presenta una rettifica di arrotondamento di {0}.<br><br>Per registrare questo importo, impostare {1} nell’azienda {2}.<br><br>In alternativa, attivare {3} per non registrare la rettifica di arrotondamento.",
	"Round Off for Opening": "Arrotondamento dei saldi di apertura",
	"Round Off Account": "Conto arrotondamento",
	"Round Off Cost Center": "Centro di costo arrotondamento",
	"Disable Rounded Total": "Disattiva totale arrotondato",
}
COMPANY_LINK = '<a href="http://erpnext.invalid/desk/company/COMPANY-RAW">COMPANY-RAW</a>'
COMPOSERS = ((SalesInvoice, SalesInvoiceGLComposer), (PurchaseInvoice, PurchaseInvoiceGLComposer))


class ReadResults:
	db_type = "mariadb"

	def __init__(self, owner):
		self.owner = owner

	def get_value(self, doctype, name, field, **kwargs):
		if doctype == "Company":
			self.owner.assertEqual(
				(name, field, kwargs), ("COMPANY-RAW", "default_currency", {"cache": True})
			)
			self.owner.events.append(("company_currency", doctype, name, field, kwargs))
			return "EUR"
		self.owner.assertIn(doctype, ("Sales Invoice", "Purchase Invoice"))
		self.owner.assertEqual((name, field, kwargs), ("INVOICE-RAW", "cost_center", {}))
		self.owner.events.append(("parent_cost_center", doctype, name, field))
		return self.owner.parent_cc

	def sql(self, query, parameters, **kwargs):
		self.owner.assertTrue(query.startswith("SELECT "))
		self.owner.assertIn("`tabAccounting Dimension`", query)
		self.owner.events.append(("dimension_sql", query, parameters, kwargs))
		return []


class FiscalCache:
	def __init__(self, owner):
		self.owner = owner

	def __call__(self):
		return self

	def hget(self, namespace, company):
		self.owner.assertEqual((namespace, company), ("fiscal_years", "COMPANY-RAW"))
		self.owner.events.append(("fiscal_cache", namespace, company))
		return [frappe._dict(name="FY-RAW", year_start_date="2026-01-01", year_end_date="2026-12-31")]


class TestRoundingFieldLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		flags = frappe._dict()
		self.enterContext(patch.object(frappe, "flags", flags))
		self.enterContext(patch.object(frappe.local, "flags", flags))
		self.enterContext(patch.object(frappe.local, "cache", {}))
		self.enterContext(patch.dict(frappe.conf, {"host_name": "http://erpnext.invalid"}))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TARGETS if lang == "it" else {},
			)
		)
		self.events = []
		self.rounding_accounts = ["ROUND-RAW", "COMPANY-CC-RAW", "OPENING-RAW"]
		self.expense = None
		self.parent_cc = "DB-CC-RAW"
		self.metadata = {}
		for name, module in (
			("Company", "setup"),
			("Sales Invoice", "accounts"),
			("Purchase Invoice", "accounts"),
		):
			slug = frappe.scrub(name)
			data = json.loads(
				Path(frappe.get_app_path("erpnext", f"{module}/doctype/{slug}/{slug}.json")).read_text()
			)
			fields = [frappe._dict(parent=name, **field) for field in data["fields"]]
			meta = Meta.__new__(Meta)
			meta.__dict__.update(
				doctype="DocType",
				name=name,
				fields=fields,
				_fields={field.fieldname: field for field in fields},
			)
			self.metadata[name] = meta
		self.enterContext(patch("frappe.get_meta", side_effect=self.get_meta))
		self.enterContext(patch("frappe.get_cached_value", side_effect=self.cached))
		self.enterContext(patch("frappe.get_all", side_effect=self.get_all))
		self.enterContext(patch("frappe.get_hooks", side_effect=self.hooks))
		self.enterContext(
			patch(
				"frappe.get_system_settings",
				side_effect=lambda key: {
					"rounding_method": "Banker's Rounding (legacy)",
					"float_precision": 3,
					"currency_precision": 2,
					"number_format": "#,###.##",
				}[key],
			)
		)
		self.enterContext(patch.object(frappe, "cache", FiscalCache(self)))
		self.db = ReadResults(self)
		self.enterContext(patch.object(frappe, "db", self.db))
		self.enterContext(patch.object(frappe.local, "db", self.db, create=True))
		self.enterContext(patch.object(frappe, "qb", MariaDB))
		for name in ("get_doc", "new_doc", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))

	def get_meta(self, name, *args, **kwargs):
		self.assertEqual((args, kwargs), ((), {}))
		self.events.append(("meta", name))
		return self.metadata[name]

	def cached(self, doctype, name, fields):
		self.events.append(("cached", doctype, name, fields))
		if doctype == "Company":
			self.assertEqual(name, "COMPANY-RAW")
			if isinstance(fields, list):
				self.assertEqual(
					fields, ["round_off_account", "round_off_cost_center", "round_off_for_opening"]
				)
				return deepcopy(self.rounding_accounts)
			self.assertIn(fields, ("default_expense_account", "country"))
			return self.expense if fields == "default_expense_account" else "Italy"
		self.assertEqual(doctype, "Account")
		self.assertIn(name, ("ROUND-RAW", "OPENING-RAW", "EXPENSE-RAW"))
		if isinstance(fields, list):
			self.assertEqual(fields, ["account_currency", "company"])
			return ["EUR", "COMPANY-RAW"]
		self.assertEqual(fields, "report_type")
		return "Profit and Loss"

	def get_all(self, doctype, **kwargs):
		self.assertEqual(
			(doctype, kwargs),
			(
				"Accounting Dimension",
				{"fields": ["label", "fieldname", "disabled", "document_type"], "filters": {"disabled": 0}},
			),
		)
		self.events.append(("dimensions", doctype, kwargs))
		return []

	def hooks(self, key, default=None):
		self.assertIn(key, ("voucher_subtypes", "regional_overrides", "update_gl_dict_with_app_based_fields"))
		return {} if key == "regional_overrides" else []

	def document(self, cls, amount=0.01, precision=2, **values):
		doc = cls.__new__(cls)
		doc.__dict__.update(
			doctype="Sales Invoice" if cls is SalesInvoice else "Purchase Invoice",
			name="INVOICE-RAW",
			flags=frappe._dict(),
			company="COMPANY-RAW",
			_AccountsController__company_currency="EUR",
			currency="EUR",
			conversion_rate=1,
			customer="CUSTOMER-RAW",
			supplier="SUPPLIER-RAW",
			posting_date="2026-10-21",
			remarks="REMARK-RAW",
			project="PROJECT-RAW",
			rounding_adjustment=amount,
			base_rounding_adjustment=amount,
			is_opening="Yes",
			cost_center="DOC-CC-RAW",
			use_company_roundoff_cost_center=0,
			is_internal_customer=0,
			is_internal_supplier=0,
			represents_company="OTHER-COMPANY",
			is_return=0,
			is_debit_note=0,
			_precision={"main": {"rounding_adjustment": precision, "base_rounding_adjustment": precision}},
		)
		doc.__dict__.update(values)
		return doc

	def state(self, doc):
		if "meta" in doc.__dict__:
			self.assertIs(doc.__dict__["meta"], self.metadata[doc.doctype])
		return deepcopy({key: value for key, value in doc.__dict__.items() if key != "meta"})

	def test_missing_company_fields_use_actual_owner_labels_and_keep_link(self):
		for fields, label in (
			([None, "COMPANY-CC-RAW", None], "Round Off Account"),
			(["ROUND-RAW", None, None], "Round Off Cost Center"),
		):
			with self.subTest(label=label):
				self.events.clear()
				self.rounding_accounts = fields
				self.parent_cc = None
				before = deepcopy(fields)
				with self.assertRaises(frappe.ValidationError) as caught:
					ledger.get_round_off_account_and_cost_center(
						"COMPANY-RAW", "Sales Invoice", "INVOICE-RAW"
					)
				self.assertEqual(
					str(caught.exception), TARGETS[MENTION].format(frappe.bold(TARGETS[label]), COMPANY_LINK)
				)
				self.assertEqual(fields, before)
				self.assertEqual(self.events[-1], ("meta", "Company"))

	def test_expense_fallback_parent_preference_and_company_default_are_unchanged(self):
		for account, expense, parent, use_default in itertools.product(
			(None, "ROUND-RAW"), ("EXPENSE-RAW",), (None, "DB-CC-RAW"), (False, True)
		):
			self.events.clear()
			self.rounding_accounts = [account, "COMPANY-CC-RAW", "OPENING-RAW"]
			self.expense, self.parent_cc = expense, parent
			self.assertEqual(
				ledger.get_round_off_account_and_cost_center(
					"COMPANY-RAW", "Sales Invoice", "INVOICE-RAW", use_default
				),
				(
					account or expense,
					parent if parent and not use_default else "COMPANY-CC-RAW",
					"OPENING-RAW",
				),
			)
			self.assertNotIn(("meta", "Company"), self.events)
			self.assertEqual(any(event[0] == "parent_cost_center" for event in self.events), not use_default)

	def test_gl_opening_guard_translates_field_without_mutating_entries(self):
		self.rounding_accounts[2] = None
		entries = [
			frappe._dict(
				company="COMPANY-RAW",
				voucher_type="Sales Invoice",
				voucher_no="INVOICE-RAW",
				account="RAW-ACCOUNT",
				is_opening="Yes",
				debit=1,
				credit=0,
			)
		]
		before = deepcopy(entries)
		with self.assertRaises(frappe.ValidationError) as caught:
			ledger.make_round_off_gle(entries, 0.01, 0.01, 2)
		self.assertEqual(
			str(caught.exception),
			TARGETS[SET].format(frappe.bold(TARGETS["Round Off for Opening"]), COMPANY_LINK),
		)
		self.assertEqual(entries, before)

	def test_both_opening_invoice_errors_use_whole_message_and_actual_invoice_meta(self):
		self.rounding_accounts[2] = None
		for (cls, composer), precision, sign in itertools.product(COMPOSERS, (0, 2, 3), (-1, 1)):
			with self.subTest(doctype=cls.__name__, precision=precision, sign=sign):
				doc = self.document(cls, sign * 10**-precision, precision)
				before = self.state(doc)
				entries = []
				with self.assertRaises(frappe.ValidationError) as caught:
					composer(doc).make_gle_for_rounding_adjustment(entries)
				self.assertEqual(
					str(caught.exception),
					TARGETS[OPENING].format(
						frappe.bold(doc.rounding_adjustment),
						frappe.bold(TARGETS["Round Off for Opening"]),
						COMPANY_LINK,
						frappe.bold(TARGETS["Disable Rounded Total"]),
					),
				)
				self.assertEqual(self.state(doc), before)
				self.assertEqual(entries, [])

	def test_zero_base_zero_and_internal_transfer_controls_skip_all_roundoff_reads(self):
		for (cls, composer), values in itertools.product(
			COMPOSERS,
			(
				{"rounding_adjustment": 0},
				{"base_rounding_adjustment": 0},
				{"is_internal_customer": 1, "is_internal_supplier": 1, "represents_company": "COMPANY-RAW"},
			),
		):
			self.events.clear()
			doc = self.document(cls, **values)
			before = self.state(doc)
			entries = []
			composer(doc).make_gle_for_rounding_adjustment(entries)
			self.assertEqual((entries, self.events), ([], []))
			self.assertEqual(self.state(doc), before)

	def test_real_composers_and_gl_builder_keep_signed_precision_and_canonical_payloads(self):
		cases = []
		for (cls, composer), precision, sign, opening, use_default in itertools.product(
			COMPOSERS, (0, 2, 3), (-1, 1), ("No", "Yes"), (0, 1)
		):
			self.events.clear()
			amount = sign * 10**-precision
			doc = self.document(
				cls, amount, precision, is_opening=opening, use_company_roundoff_cost_center=use_default
			)
			before = self.state(doc)
			entries = []
			composer(doc).make_gle_for_rounding_adjustment(entries)
			self.assertEqual(self.state(doc), before)
			self.assertEqual(len(entries), 1)
			expected = {
				"company": "COMPANY-RAW",
				"posting_date": "2026-10-21",
				"fiscal_year": "FY-RAW",
				"voucher_type": doc.doctype,
				"voucher_no": "INVOICE-RAW",
				"remarks": "REMARK-RAW",
				"debit": 0,
				"credit": 0,
				"debit_in_account_currency": 0,
				"credit_in_account_currency": 0,
				"is_opening": opening,
				"party_type": None,
				"party": None,
				"project": "PROJECT-RAW",
				"post_net_value": None,
				"voucher_detail_no": None,
				"voucher_subtype": doc.doctype,
				"account": "OPENING-RAW" if opening == "Yes" else "ROUND-RAW",
				"against": "CUSTOMER-RAW" if cls is SalesInvoice else "SUPPLIER-RAW",
				"cost_center": "COMPANY-CC-RAW" if use_default else "DOC-CC-RAW",
				"account_currency": "EUR",
			}
			if cls is SalesInvoice:
				expected.update(
					credit=amount, credit_in_account_currency=amount, credit_in_transaction_currency=amount
				)
			else:
				expected.update(debit=amount, debit_in_account_currency=amount)
			self.assertEqual(entries[0], expected)
			self.assertNotIn(("meta", "Company"), self.events)
			cases.append(
				{
					"doctype": doc.doctype,
					"precision": precision,
					"sign": sign,
					"opening": opening,
					"company_default": use_default,
					"entries": entries,
					"reads": deepcopy(self.events),
				}
			)
		self.parity_evidence = {
			"cases": len(cases),
			"sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
			"raw_entries_and_ordered_reads": cases,
		}

	def test_custom_actual_metadata_label_and_owner_context_are_used(self):
		self.rounding_accounts = [None, "COMPANY-CC-RAW", None]
		self.metadata["Company"].get_field("round_off_account").label = "Custom Round Off"
		with patch.dict(TARGETS, {"Custom Round Off:Company": "Arrotondamento configurato"}):
			with self.assertRaises(frappe.ValidationError) as caught:
				ledger.get_round_off_account_and_cost_center("COMPANY-RAW", "Sales Invoice", "INVOICE-RAW")
			self.assertEqual(
				str(caught.exception),
				TARGETS[MENTION].format(frappe.bold("Arrotondamento configurato"), COMPANY_LINK),
			)

	def test_english_fallback_retains_whole_original_message(self):
		frappe.local.lang = "en"
		self.rounding_accounts[2] = None
		doc = self.document(SalesInvoice)
		with self.assertRaises(frappe.ValidationError) as caught:
			SalesInvoiceGLComposer(doc).make_gle_for_rounding_adjustment([])
		self.assertEqual(
			str(caught.exception),
			OPENING.format(
				frappe.bold(0.01),
				frappe.bold("Round Off for Opening"),
				COMPANY_LINK,
				frappe.bold("Disable Rounded Total"),
			),
		)
