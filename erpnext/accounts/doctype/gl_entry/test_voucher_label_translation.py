# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.gl_entry import gl_entry
from erpnext.accounts.doctype.payment_ledger_entry import payment_ledger_entry
from erpnext.exceptions import InvalidAccountCurrency


class TestVoucherLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={
					"Sales Invoice": "Fattura di vendita",
					"Purchase Invoice": "Fattura di acquisto",
					"Journal Entry": "Registrazione contabile",
					"Company": "Azienda",
					"Cost Center": "Centro di costo",
					"Project": "Progetto",
				},
			)
		)
		self.enterContext(patch.dict(frappe.flags, {"party_not_required": False}))
		self.enterContext(patch("frappe.get_system_settings", return_value="Banker's Rounding"))
		self.enterContext(patch("frappe.throw", side_effect=self.raise_message))

	@staticmethod
	def raise_message(message, exc=frappe.ValidationError, **kwargs):
		raise exc(message)

	def make_entry(self, cls=gl_entry.GLEntry, **values):
		doc = cls.__new__(cls)
		doc.__dict__.update(
			doctype="GL Entry" if cls is gl_entry.GLEntry else "Payment Ledger Entry",
			name="ENTRY-RAW",
			voucher_type="Sales Invoice",
			voucher_no="Sales Invoice",
			account="Cost Center",
			company="Company",
			cost_center="Project",
			party_type="Customer",
			party="Customer",
			account_type="Receivable",
			account_currency="EUR",
			debit=25,
			credit=0,
			is_cancelled=0,
			is_opening="No",
			fiscal_year="YEAR-RAW",
			against_voucher_type="Sales Invoice",
			against_voucher="INVOICE-RAW",
			against_voucher_no="INVOICE-RAW",
			delinked=0,
			flags=frappe._dict(
				adv_adj=False, from_repost=False, update_outstanding="No", ignore_submit_comment=True
			),
			_precision=frappe._dict(main=frappe._dict(debit=2, credit=2)),
		)
		doc.__dict__.update(values)
		return doc

	def assert_translated_error(self, doc, method, *args, exc=frappe.ValidationError):
		before = deepcopy(doc.__dict__)
		with self.assertRaises(exc) as error:
			method(*args)
		expected = frappe._(doc.voucher_type)
		self.assertTrue(str(error.exception).startswith(f"{expected} Sales Invoice:"), str(error.exception))
		self.assertEqual(doc.__dict__, before)
		return str(error.exception)

	def test_mandatory_party_and_amount_errors_translate_only_the_type(self):
		for account_type, voucher_type, expected_word in (
			("Receivable", "Sales Invoice", "Customer"),
			("Payable", "Purchase Invoice", "Supplier"),
			("Bank", "Sales Invoice", "Either debit or credit"),
		):
			with self.subTest(account_type=account_type):
				doc = self.make_entry(voucher_type=voucher_type, party_type=None, party=None, debit=0)
				with patch("frappe.get_cached_value", return_value=account_type) as cached:
					message = self.assert_translated_error(doc, doc.check_mandatory)
				self.assertIn(expected_word, message)
				self.assertIn("Cost Center", message)
				cached.assert_called_once_with("Account", "Cost Center", "account_type")

	def test_profit_loss_errors_translate_both_sentences_without_changing_account_names(self):
		for opening in (False, True):
			with self.subTest(opening=opening):
				doc = self.make_entry(is_opening="Yes") if opening else self.make_entry(cost_center=None)
				method = doc.check_pl_account if opening else doc.pl_must_have_cost_center
				with patch("frappe.get_cached_value", return_value="Profit and Loss") as cached:
					message = self.assert_translated_error(doc, method)
					if not opening:
						self.assertIn("cost center field in Fattura di vendita or", message)
					self.assertIn("account Cost Center", message)
					cached.assert_called_once_with("Account", "Cost Center", "report_type")

	def test_both_ledgers_share_translated_account_errors_and_raw_cache_keys(self):
		for cls in (gl_entry.GLEntry, payment_ledger_entry.PaymentLedgerEntry):
			for overrides in ({"is_group": 1}, {"docstatus": 2}, {"company": "OTHER-COMPANY"}):
				with self.subTest(ledger=cls.__name__, invalid=overrides):
					doc = self.make_entry(cls)
					account = frappe._dict(is_group=0, docstatus=0, company="Company")
					account.update(overrides)
					with patch("frappe.get_cached_value", return_value=account) as cached:
						args = (False,) if cls is gl_entry.GLEntry else ()
						message = self.assert_translated_error(doc, doc.validate_account_details, *args)
					self.assertIn("Cost Center", message)
					if "company" in overrides:
						self.assertIn("Company", message)
					cached.assert_called_once_with(
						"Account", "Cost Center", fieldname=["is_group", "docstatus", "company"], as_dict=True
					)

	def test_cost_center_errors_preserve_business_names_and_canonical_queries(self):
		for value in ((0, "OTHER-COMPANY"), (1, "Company")):
			with self.subTest(value=value):
				doc = self.make_entry()
				with patch("frappe.get_cached_value", return_value=value) as cached:
					message = self.assert_translated_error(doc, doc.validate_cost_center)
				self.assertIn("Project", message)
				self.assertNotIn("Progetto", message)
				cached.assert_called_once_with("Cost Center", "Project", ["is_group", "company"])

	def test_currency_error_translates_voucher_label_and_preserves_currency_and_accounts(self):
		doc = self.make_entry(account_currency="USD")
		with (
			patch.object(gl_entry.erpnext, "get_company_currency", return_value="EUR") as company_currency,
			patch.object(gl_entry, "get_account_currency", return_value="EUR") as account_currency,
		):
			message = self.assert_translated_error(doc, doc.validate_currency, exc=InvalidAccountCurrency)
		self.assertIn("Cost Center", message)
		self.assertIn("EUR", message)
		company_currency.assert_called_once_with("Company")
		account_currency.assert_called_once_with("Cost Center")

	def test_cancelled_and_closing_controls_keep_the_existing_validation_boundaries(self):
		with patch("frappe.get_cached_value", return_value="Profit and Loss") as cached:
			doc = self.make_entry(is_cancelled=1, is_opening="Yes")
			doc.validate_currency()
			doc.validate_cost_center()
			doc.check_pl_account()
			cached.assert_called_once_with("Account", "Cost Center", "report_type")
		with patch("frappe.get_cached_value", return_value=("EUR", None)) as cached:
			doc = self.make_entry(voucher_type="Period Closing Voucher", cost_center=None, debit=0)
			before = deepcopy(doc.__dict__)
			doc.validate()
			self.assertEqual(doc.__dict__, before)
			cached.assert_called_once_with("Company", "Company", ["default_currency", "reporting_currency"])

	def test_valid_entries_keep_raw_party_account_voucher_and_outstanding_payloads(self):
		def cached_value(doctype, name, fieldname, **kwargs):
			if doctype == "Cost Center":
				return (0, "Company")
			if doctype == "Company":
				return ("EUR", None)
			if isinstance(fieldname, list):
				return frappe._dict(is_group=0, docstatus=0, company="Company", account_type="Receivable")
			return "Balance Sheet" if fieldname == "report_type" else "Receivable"

		with (
			patch("frappe.get_cached_value", side_effect=cached_value),
			patch.object(gl_entry.erpnext, "get_company_currency", return_value="EUR"),
			patch.object(gl_entry, "get_account_currency", return_value="EUR"),
			patch.object(gl_entry, "validate_party_frozen_disabled") as frozen,
			patch.object(gl_entry, "validate_account_party_type") as party_type,
			patch.object(gl_entry, "validate_party_gle_currency") as party_currency,
		):
			doc = self.make_entry()
			before = deepcopy(doc.__dict__)
			doc.validate()
			self.assertEqual(doc.__dict__, before)
			frozen.assert_called_once_with("Company", "Customer", "Customer")
			party_type.assert_called_once_with(doc)
			party_currency.assert_called_once_with("Customer", "Customer", "Company", "EUR")
		with (
			patch("frappe.get_cached_value", side_effect=cached_value),
			patch.object(payment_ledger_entry, "get_checks_for_pl_and_bs_accounts", return_value=[]),
			patch.object(payment_ledger_entry, "get_dimension_filter_map", return_value={}),
			patch.object(payment_ledger_entry, "validate_frozen_account") as frozen,
			patch.object(payment_ledger_entry, "validate_balance_type") as balance,
			patch.object(payment_ledger_entry, "update_voucher_outstanding") as outstanding,
		):
			doc = self.make_entry(payment_ledger_entry.PaymentLedgerEntry)
			doc.flags.update_outstanding = "Yes"
			before = deepcopy(doc.__dict__)
			doc.validate()
			doc.on_update()
			self.assertEqual(doc.__dict__, before)
			frozen.assert_called_once_with("Company", "Cost Center", False)
			balance.assert_called_once_with("Cost Center", False)
			outstanding.assert_called_once_with(
				"Sales Invoice", "INVOICE-RAW", "Cost Center", "Customer", "Customer"
			)

	def test_journal_exchange_gain_loss_zero_amount_exception_queries_the_canonical_doctype(self):
		doc = self.make_entry(voucher_type="Journal Entry", debit=0)
		before = deepcopy(doc.__dict__)
		with patch("frappe.get_cached_value", return_value="Exchange Gain Or Loss") as cached:
			doc.check_mandatory()
			cached.assert_called_once_with("Journal Entry", "Sales Invoice", "voucher_type")
		self.assertEqual(doc.__dict__, before)
