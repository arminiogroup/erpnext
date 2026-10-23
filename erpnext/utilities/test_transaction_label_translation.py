# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from pathlib import Path
from unittest.mock import Mock, call, patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.utilities.transaction_base import TransactionBase

ACCOUNT_CURRENCY = (
	"Account type {0}: account {1} ({2}) must use either party currency {3} or company default currency {4}."
)
ADVANCE_CURRENCY = "Advance account {0} must use either party currency {1} or company default currency {2}."
ACCOUNT_COMPANY_CURRENCY = "Account type {0}: account {1} ({2}) must use company default currency {3}."
ADVANCE_COMPANY_CURRENCY = "Advance account {0} must use company default currency {1}."
PAIR_CURRENCY = (
	"Account type {0}: account {1} and advance account {2} must have the same currency for company {3}."
)
MISSING_ACCOUNT = "No account is configured in {0} for {1}: {2}."
PARTY_ACCOUNT = "Field {0} in {1}, under {2}, for {3} {4}"
COMPANY_ACCOUNT = "{0} in Company {1}"
RETURN_WARNING = "This document ({0}) references {1}. To update the outstanding amount of {1}, clear {2}."
RATE_WARNING = "Row #{0}: Rate must be same as {1}: {2} ({3} / {4})"

METADATA = (
	("accounts", "Account"),
	("accounts", "Party Account"),
	("setup", "Company"),
	("selling", "Customer"),
	("buying", "Supplier"),
	("accounts", "Sales Invoice"),
	("accounts", "POS Invoice"),
	("accounts", "Purchase Invoice"),
	("accounts", "Purchase Invoice Item"),
	("stock", "Purchase Receipt Item"),
	("buying", "Purchase Order"),
	("selling", "Sales Order"),
	("stock", "Purchase Receipt"),
	("stock", "Delivery Note"),
	("selling", "Quotation"),
	("projects", "Project"),
	("accounts", "Cost Center"),
	("accounts", "Payment Reconciliation"),
	("stock", "Warehouse"),
)
PRODUCERS = (
	"utilities/transaction_base.py",
	"controllers/accounts_controller.py",
	"accounts/services/internal_transfer.py",
)
TARGETS = {
	ACCOUNT_CURRENCY: "Tipo di conto {0}: il conto {1} ({2}) deve usare la valuta della controparte {3} oppure la valuta predefinita dell’azienda {4}.",
	ADVANCE_CURRENCY: "Il conto anticipi {0} deve usare la valuta della controparte {1} oppure la valuta predefinita dell’azienda {2}.",
	ACCOUNT_COMPANY_CURRENCY: "Tipo di conto {0}: il conto {1} ({2}) deve usare la valuta predefinita dell’azienda {3}.",
	ADVANCE_COMPANY_CURRENCY: "Il conto anticipi {0} deve usare la valuta predefinita dell’azienda {1}.",
	PAIR_CURRENCY: "Tipo di conto {0}: il conto {1} e il conto anticipi {2} devono avere la stessa valuta per l’azienda {3}.",
	MISSING_ACCOUNT: "Nessun conto configurato nel campo {0} per {1}: {2}.",
	PARTY_ACCOUNT: "Campo {0} in {1}, nella scheda {2}, per {3}: {4}",
	COMPANY_ACCOUNT: "{0} nell’azienda {1}",
	RETURN_WARNING: "Questo documento ({0}) fa riferimento a {1}. Per aggiornare il saldo residuo di {1}, deselezionare {2}.",
	RATE_WARNING: "Riga n. {0}: il prezzo deve coincidere con quello del documento {1} {2} ({3} / {4}).",
	"Duplicate row {0} with same {1}": "Riga {0} duplicata: stesso riferimento di tipo {1}.",
	"Invalid reference {0} {1}": "Riferimento non valido: {0} {1}.",
	"{0}: {1} does not belong to the Company: {2}": "{0}: {1} non appartiene all’azienda {2}.",
	"Internal Sale or Delivery Reference missing.": "Manca il riferimento alla vendita interna o alla consegna.",
	"Please create purchase from internal sale or delivery document itself": "Creare il documento di acquisto direttamente dal documento interno di vendita o di consegna.",
	"Please set one of the following:": "Impostare una delle seguenti opzioni:",
	"Account Missing": "Conto mancante",
	"Customer": "Cliente",
	"Supplier": "Fornitore",
	"Company": "Azienda",
	"Receivable": "Crediti",
	"Payable": "Debiti",
	"Debit To": "Conto da addebitare",
	"Credit To": "Conto da accreditare",
	"Default Account": "Conto predefinito",
	"Default Accounts": "Conti predefiniti",
	"Per-Company Accounts": "Conti per azienda",
	"Default Receivable Account": "Conto crediti predefinito",
	"Default Payable Account": "Conto debiti predefinito",
	"Accounting": "Contabilità",
	"Credit Note": "Nota di credito",
	"Debit Note": "Nota di debito",
	"Update Outstanding for Self": "Aggiorna il saldo residuo di questo documento",
	"Purchase Receipt Item": "Riga ricevimento merci",
	"Purchase Invoice": "Fattura di acquisto",
	"Sales Invoice": "Fattura di vendita",
	"Purchase Order": "Ordine di acquisto",
	"Sales Order": "Ordine di vendita",
	"Purchase Receipt": "Ricevimento merci",
	"Delivery Note": "Documento di trasporto",
	"Quotation": "Preventivo",
	"Project": "Progetto",
	"Cost Center": "Centro di costo",
	"Payment Reconciliation": "Riconciliazione pagamenti",
	"Warehouse": "Magazzino",
	"Warning": "Avviso",
	"Advance Account": "Conto anticipi",
	"The outstanding amount {0} in {1} is lesser than {2}. Updating the outstanding to this invoice.": "Il saldo residuo {0} in {1} è inferiore a {2}. Il saldo residuo verrà aggiornato su questa fattura.",
	"You can use {0} to reconcile against {1} later.": "È possibile usare {0} per la riconciliazione con {1} in seguito.",
}


def metadata_path(module, doctype):
	slug = frappe.scrub(doctype)
	return Path(frappe.get_app_path("erpnext", module, "doctype", slug, f"{slug}.json"))


def read_meta(module, doctype):
	data = json.loads(metadata_path(module, doctype).read_text())
	meta = Meta.__new__(Meta)
	meta.__dict__.update(
		name=data["name"],
		fields=[frappe._dict(field) for field in data["fields"]],
		_fields={field["fieldname"]: frappe._dict(field) for field in data["fields"]},
		_non_computed_table_doctypes={
			field["fieldname"]: field["options"]
			for field in data["fields"]
			if field["fieldtype"] in ("Table", "Table MultiSelect") and not field.get("computed")
		},
	)
	return meta


def extracted_keys():
	keys = set()
	for relative in PRODUCERS:
		with Path(frappe.get_app_path("erpnext", relative)).open("rb") as source:
			keys.update(
				message
				for _, message, _, context in extract(
					"frappe.gettext.extractors.python.extract", source, keywords=PYTHON_KEYWORDS
				)
				if context is None
			)
	for module, doctype in METADATA:
		with metadata_path(module, doctype).open("rb") as source:
			keys.update(
				message
				for _, message, _, context in extract(
					"frappe.gettext.extractors.doctype.extract", source, keywords=PYTHON_KEYWORDS
				)
				if context is None
			)
	return keys


def make_doc(cls=Document, **values):
	doc = cls.__new__(cls)
	doc.__dict__.update(flags=frappe._dict(), **values)
	return doc


def raise_message(message, exc=frappe.ValidationError, **kwargs):
	raise exc(message)


class TestTransactionLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		keys = extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={k: v for k, v in TARGETS.items() if k in keys},
			)
		)
		self.enterContext(patch("frappe.throw", side_effect=raise_message))
		self.enterContext(patch("frappe.get_system_settings", return_value="Banker's Rounding"))
		self.db = self.enterContext(patch("frappe.db", new=Mock()))

	def error(self, action):
		with self.assertRaises(frappe.ValidationError) as caught:
			action()
		return str(caught.exception)

	def currency_doc(self, doctype, account="ACCOUNT-RAW", advance="ADVANCE-RAW"):
		return make_doc(
			TransactionBase,
			doctype=doctype,
			default_currency="EUR",
			accounts=[frappe._dict(company="COMPANY-RAW", account=account, advance_account=advance)],
		)

	def cached_currencies(self, main, advance):
		def get_value(doctype, name, field):
			if doctype == "Company":
				self.assertEqual((name, field), ("COMPANY-RAW", "default_currency"))
				return "USD"
			self.assertEqual((doctype, field), ("Account", "account_currency"))
			return {"ACCOUNT-RAW": main, "ADVANCE-RAW": advance}[name]

		return get_value

	def test_currency_errors_are_correct_for_both_party_types_and_all_three_guards(self):
		for doctype, account_type in (("Customer", "Crediti"), ("Supplier", "Debiti")):
			for main, advance, expected in (
				("GBP", None, "main"),
				(None, "GBP", "Il conto anticipi"),
				("EUR", "USD", "stessa valuta"),
			):
				with self.subTest(doctype=doctype, main=main, advance=advance):
					doc = self.currency_doc(
						doctype, "ACCOUNT-RAW" if main else None, "ADVANCE-RAW" if advance else None
					)
					with patch(
						"frappe.get_cached_value", side_effect=self.cached_currencies(main, advance)
					) as cache:
						message = self.error(doc.validate_currency_for_receivable_payable_and_advance_account)
					self.assertEqual(
						cache.call_args_list[0], call("Company", "COMPANY-RAW", "default_currency")
					)
					if main:
						self.assertIn(f"Tipo di conto {account_type}", message)
						self.assertIn("<strong>ACCOUNT-RAW</strong>", message)
					if advance:
						self.assertIn("<strong>ADVANCE-RAW</strong>", message)
					if expected == "main":
						self.assertIn("(<strong>GBP</strong>)", message)
					else:
						self.assertIn(expected, message)
					self.assertNotIn("customer billing currency", message)
					self.assertEqual(doc.doctype, doctype)
					self.assertEqual(doc.accounts[0].company, "COMPANY-RAW")

	def test_allowed_currencies_empty_accounts_and_unrelated_documents_remain_valid(self):
		for doctype in ("Customer", "Supplier"):
			for main, advance in (("EUR", "EUR"), ("USD", "USD"), ("EUR", None), (None, "USD"), (None, None)):
				doc = self.currency_doc(
					doctype, "ACCOUNT-RAW" if main else None, "ADVANCE-RAW" if advance else None
				)
				with patch("frappe.get_cached_value", side_effect=self.cached_currencies(main, advance)):
					doc.validate_currency_for_receivable_payable_and_advance_account()
			self.assertEqual(doc.default_currency, "EUR")
		with patch("frappe.get_cached_value") as cache:
			make_doc(
				TransactionBase, doctype="Lead"
			).validate_currency_for_receivable_payable_and_advance_account()
		cache.assert_not_called()

	def test_multiple_company_rows_and_missing_party_currency_preserve_raw_reads(self):
		doc = self.currency_doc("Supplier")
		doc.default_currency = None
		doc.accounts = [
			frappe._dict(company="FIRST-COMPANY", account="FIRST-ACCOUNT", advance_account=None),
			frappe._dict(company="SECOND-COMPANY", account="SECOND-ACCOUNT", advance_account=None),
		]

		def get_value(doctype, name, field):
			if doctype == "Company":
				self.assertEqual(field, "default_currency")
				return {"FIRST-COMPANY": "USD", "SECOND-COMPANY": "EUR"}[name]
			self.assertEqual((doctype, field), ("Account", "account_currency"))
			return {"FIRST-ACCOUNT": "USD", "SECOND-ACCOUNT": "GBP"}[name]

		with patch("frappe.get_cached_value", side_effect=get_value) as cache:
			message = self.error(doc.validate_currency_for_receivable_payable_and_advance_account)
		self.assertEqual(
			cache.call_args_list,
			[
				call("Company", "FIRST-COMPANY", "default_currency"),
				call("Account", "FIRST-ACCOUNT", "account_currency"),
				call("Company", "SECOND-COMPANY", "default_currency"),
				call("Account", "SECOND-ACCOUNT", "account_currency"),
			],
		)
		self.assertEqual(
			message,
			"Tipo di conto Debiti: il conto <strong>SECOND-ACCOUNT</strong> (<strong>GBP</strong>) deve usare la valuta predefinita dell’azienda <strong>EUR</strong>.",
		)
		self.assertIsNone(doc.default_currency)

	def test_missing_party_currency_errors_use_complete_company_only_messages(self):
		for doctype, account_type in (("Customer", "Crediti"), ("Supplier", "Debiti")):
			for missing_currency in (None, ""):
				for main, advance in (
					("GBP", None),
					(None, "GBP"),
					("GBP", "USD"),
					("USD", "GBP"),
					("GBP", "GBP"),
				):
					with self.subTest(doctype=doctype, missing=missing_currency, main=main, advance=advance):
						doc = self.currency_doc(
							doctype, "ACCOUNT-RAW" if main else None, "ADVANCE-RAW" if advance else None
						)
						doc.default_currency = missing_currency
						with patch(
							"frappe.get_cached_value", side_effect=self.cached_currencies(main, advance)
						) as cache:
							message = self.error(
								doc.validate_currency_for_receivable_payable_and_advance_account
							)
						if main == "GBP":
							expected = f"Tipo di conto {account_type}: il conto <strong>ACCOUNT-RAW</strong> (<strong>GBP</strong>) deve usare la valuta predefinita dell’azienda <strong>USD</strong>."
						else:
							expected = "Il conto anticipi <strong>ADVANCE-RAW</strong> deve usare la valuta predefinita dell’azienda <strong>USD</strong>."
						self.assertEqual(message, expected)
						reads = [call("Company", "COMPANY-RAW", "default_currency")]
						if main:
							reads.append(call("Account", "ACCOUNT-RAW", "account_currency"))
						if advance:
							reads.append(call("Account", "ADVANCE-RAW", "account_currency"))
						self.assertEqual(cache.call_args_list, reads)
						self.assertEqual(doc.default_currency, missing_currency)

	def test_missing_party_currency_still_accepts_company_currency_without_setting_a_default(self):
		for doctype in ("Customer", "Supplier"):
			for missing_currency in (None, ""):
				for main, advance in (("USD", "USD"), ("USD", None), (None, "USD")):
					doc = self.currency_doc(
						doctype, "ACCOUNT-RAW" if main else None, "ADVANCE-RAW" if advance else None
					)
					doc.default_currency = missing_currency
					with patch("frappe.get_cached_value", side_effect=self.cached_currencies(main, advance)):
						doc.validate_currency_for_receivable_payable_and_advance_account()
					self.assertEqual(doc.default_currency, missing_currency)

	def reference_doc(self, *, is_return=0, second_reference="PREV-ROW"):
		meta = read_meta("accounts", "Purchase Invoice Item")
		rows = [
			make_doc(
				doctype="Purchase Invoice Item",
				idx=i,
				pr_detail=reference,
				item_code="ITEM-RAW",
				uom="Nos",
				meta=meta,
			)
			for i, reference in ((1, "PREV-ROW"), (2, second_reference))
		]
		return make_doc(
			TransactionBase,
			doctype="Purchase Invoice",
			is_return=is_return,
			items=rows,
			meta=read_meta("accounts", "Purchase Invoice"),
		)

	def reference_config(self, allow_duplicates=False, fields=None):
		return {
			"Purchase Receipt Item": {
				"ref_dn_field": "pr_detail",
				"is_child_table": True,
				"allow_duplicate_prev_row_id": allow_duplicates,
				"compare_fields": fields or [["item_code", "="]],
			}
		}

	def test_duplicate_previous_child_reference_translates_type_but_preserves_query(self):
		doc = self.reference_doc()
		with patch(
			"frappe.get_all", return_value=[frappe._dict(name="PREV-ROW", item_code="ITEM-RAW")]
		) as query:
			message = self.error(lambda: doc.validate_with_previous_doc(self.reference_config()))
		self.assertEqual(message, "Riga 2 duplicata: stesso riferimento di tipo Riga ricevimento merci.")
		self.assertEqual(query.call_count, 2)
		self.assertEqual(
			query.call_args,
			call(
				"Purchase Receipt Item", filters={"name": ("in", ["PREV-ROW"])}, fields=["name", "item_code"]
			),
		)
		self.assertEqual(doc.items[1].pr_detail, "PREV-ROW")

	def test_allowed_duplicate_and_absent_reference_controls_use_real_comparison(self):
		doc = self.reference_doc()
		with patch(
			"frappe.get_all", return_value=[frappe._dict(name="PREV-ROW", item_code="ITEM-RAW")]
		) as query:
			doc.validate_with_previous_doc(self.reference_config(True))
		self.assertEqual(query.call_count, 2)
		doc.items[0].pr_detail = None
		doc.items[1].pr_detail = None
		with patch("frappe.get_all") as query:
			doc.validate_with_previous_doc(self.reference_config())
		query.assert_not_called()

	def test_previous_parent_reference_is_deduplicated_and_compared_without_translation(self):
		doc = self.reference_doc()
		doc.supplier = "Supplier"
		doc.company = "Company"
		for row in doc.items:
			row.purchase_receipt = "PREVIOUS-DOC"
		with patch(
			"frappe.get_all",
			return_value=[frappe._dict(name="PREVIOUS-DOC", supplier="Supplier", company="Company")],
		) as query:
			doc.validate_with_previous_doc(
				{
					"Purchase Receipt": {
						"ref_dn_field": "purchase_receipt",
						"compare_fields": [["supplier", "="], ["company", "="]],
					}
				}
			)
		query.assert_called_once_with(
			"Purchase Receipt",
			filters={"name": ("in", ["PREVIOUS-DOC"])},
			fields=["name", "supplier", "company"],
		)
		self.assertEqual(doc.supplier, "Supplier")
		self.assertEqual(doc.company, "Company")

	def test_invalid_reference_translates_only_the_canonical_doctype(self):
		doc = self.reference_doc()
		with patch("frappe.get_all", return_value=[]) as query:
			message = self.error(
				lambda: doc.compare_values(
					{"Purchase Receipt Item": ["Purchase Order"]}, [["item_code", "="]]
				)
			)
		self.assertEqual(message, "Riferimento non valido: Riga ricevimento merci Purchase Order.")
		query.assert_called_once_with(
			"Purchase Receipt Item",
			filters={"name": ("in", ["Purchase Order"])},
			fields=["name", "item_code"],
		)

	def test_returns_keep_uom_exclusion_and_blank_previous_values(self):
		doc = self.reference_doc(is_return=1)
		with patch("frappe.get_all", return_value=[frappe._dict(name="PREV-ROW", item_code="", uom="Box")]):
			doc.validate_with_previous_doc(self.reference_config(True, [["item_code", "="], ["uom", "="]]))
		self.assertEqual(doc.exclude_fields, ["conversion_factor", "uom"])
		self.assertEqual(doc.items[0].uom, "Nos")

	def rate_doc(self, rate=12, reference="PREV-ROW", doctype="Purchase Invoice"):
		row = make_doc(
			doctype=f"{doctype} Item",
			idx=3,
			rate=rate,
			reference_item=reference,
			reference_name="Purchase Order",
			_precision=frappe._dict(main=frappe._dict(rate=2)),
		)
		return make_doc(TransactionBase, doctype=doctype, is_internal_supplier=0, items=[row])

	def test_rate_stop_and_warn_translate_type_with_exact_data_and_options(self):
		for action in ("Stop", "Warn"):
			with self.subTest(action=action):
				doc = self.rate_doc()
				with patch(
					"frappe.get_cached_value", return_value=[action, "OVERRIDE-RAW"]
				) as settings, patch("frappe.get_all", return_value=[["PREV-ROW", 10]]) as query, patch(
					"frappe.get_roles", return_value=[]
				), patch("frappe.msgprint") as message:
					if action == "Stop":
						text = self.error(
							lambda: doc.validate_rate_with_reference_doc(
								[["Purchase Order", "reference_name", "reference_item"]]
							)
						)
					else:
						doc.validate_rate_with_reference_doc(
							[["Purchase Order", "reference_name", "reference_item"]]
						)
						text = message.call_args.args[0]
						self.assertEqual(message.call_args.kwargs, {"title": "Avviso", "indicator": "orange"})
				self.assertIn("documento Ordine di acquisto Purchase Order (12 / 10)", text)
				settings.assert_called_once_with(
					"Buying Settings", "None", ["maintain_same_rate_action", "role_to_override_stop_action"]
				)
				query.assert_called_once_with(
					"Purchase Order Item",
					filters={"name": ("in", ["PREV-ROW"])},
					fields=["name", "rate"],
					as_list=1,
				)
				self.assertEqual(doc.items[0].rate, 12)

	def test_rate_override_rounding_missing_reference_and_internal_supplier_controls(self):
		for rate, reference, roles in (
			(12, "PREV-ROW", ["OVERRIDE-RAW"]),
			(10.004, "PREV-ROW", []),
			(12, None, []),
		):
			doc = self.rate_doc(rate, reference)
			with patch("frappe.get_cached_value", return_value=["Stop", "OVERRIDE-RAW"]), patch(
				"frappe.get_all", return_value=[["PREV-ROW", 10]]
			), patch("frappe.get_roles", return_value=roles), patch("frappe.msgprint") as message:
				doc.validate_rate_with_reference_doc([["Purchase Order", "reference_name", "reference_item"]])
			message.assert_not_called()
		doc.is_internal_supplier = 1
		with patch("frappe.get_cached_value") as settings:
			doc.validate_rate_with_reference_doc([])
		settings.assert_not_called()

	def test_selling_rate_uses_selling_settings_and_preserves_reference(self):
		doc = self.rate_doc(doctype="Sales Order")
		with patch("frappe.get_cached_value", return_value=["Warn", "OVERRIDE-RAW"]) as settings, patch(
			"frappe.get_all", return_value=[["PREV-ROW", 10]]
		), patch("frappe.msgprint") as message:
			doc.validate_rate_with_reference_doc([["Quotation", "reference_name", "reference_item"]])
		self.assertIn("documento Preventivo Purchase Order", message.call_args.args[0])
		self.assertEqual(settings.call_args.args[:2], ("Selling Settings", "None"))

	def test_new_currency_templates_are_actual_custom_extractor_keys(self):
		keys = extracted_keys()
		for source in (
			ACCOUNT_CURRENCY,
			ADVANCE_CURRENCY,
			PAIR_CURRENCY,
			ACCOUNT_COMPANY_CURRENCY,
			ADVANCE_COMPANY_CURRENCY,
		):
			with self.subTest(source=source):
				self.assertIn(source, keys)
