# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import Mock, call, patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.document import Document
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.journal_entry import journal_entry
from erpnext.accounts.doctype.journal_entry.services import reference_validator
from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice
from erpnext.controllers import item_close


class TestReferenceLabelTranslation(UnitTestCase):
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
					"Sales Order": "Ordine di vendita",
					"Purchase Order": "Ordine di acquisto",
					"Customer": "Cliente",
					"Supplier": "Fornitore",
					"Debit To": "Conto clienti",
					"Credit To": "Conto fornitori",
					"Bank Entry": "Registrazione bancaria",
					"Sanctioned": "Approvato",
					"Disbursed": "Erogato",
					"Settled": "Saldato",
					"{0} {1} is blocked and on hold.": "Il documento {1} di tipo {0} è sospeso.",
					"Warning: A submitted Journal Entry exists against Stock Entry {0}.": "Attenzione: esiste una registrazione contabile confermata collegata al movimento di magazzino {0}.",
				},
			)
		)
		self.enterContext(patch("frappe.get_system_settings", return_value="Banker's Rounding"))
		self.enterContext(patch("frappe.throw", side_effect=self.raise_message))
		self.db = self.enterContext(patch("frappe.db", new=Mock()))

	@staticmethod
	def raise_message(message, exc=frappe.ValidationError, **kwargs):
		raise exc(message)

	@staticmethod
	def make_doc(cls=journal_entry.JournalEntry, **values):
		doc = cls.__new__(cls)
		doc.__dict__.update(
			doctype="Journal Entry",
			name="JE-CURRENT-RAW",
			voucher_type="Journal Entry",
			docstatus=0,
			accounts=[],
			company="COMPANY-RAW",
			party_not_required=False,
			flags=frappe._dict(),
			_precision=frappe._dict(main=frappe._dict(outstanding_amount=2)),
		)
		doc.__dict__.update(values)
		return doc

	def assert_labels(self, action, *labels):
		with self.assertRaises(frappe.ValidationError) as error:
			action()
		for label in labels:
			self.assertIn(label, str(error.exception))
		return str(error.exception)

	def test_order_direction_errors_translate_types_and_keep_the_financial_side(self):
		validator = reference_validator.JournalEntryReferenceValidator(self.make_doc())
		for reference_type, debit, credit in (("Sales Order", 10, 0), ("Purchase Order", 0, 10)):
			with self.subTest(reference_type=reference_type):
				row = frappe._dict(idx=3, reference_type=reference_type, debit=debit, credit=credit)
				before = row.copy()
				self.assert_labels(lambda: validator._validate_order_direction(row), frappe._(reference_type))
				self.assertEqual(row, before)
				row.debit, row.credit = credit, debit
				validator._validate_order_direction(row)
				row.debit = row.credit = 0
				validator._validate_order_direction(row)

	def test_invoice_party_account_errors_use_translated_labels_and_raw_lookup_fields(self):
		for reference_type, party_type, account_field in (
			("Sales Invoice", "Customer", "Debit To"),
			("Purchase Invoice", "Supplier", "Credit To"),
		):
			with self.subTest(reference_type=reference_type):
				row = frappe._dict(
					idx=2,
					reference_type=reference_type,
					reference_name="INVOICE-RAW",
					party="PARTY-RAW",
					party_type=party_type,
					account="ACCOUNT-RAW",
				)
				before = row.copy()
				self.db.get_value.return_value = ("OTHER-PARTY", "OTHER-ACCOUNT")
				validator = reference_validator.JournalEntryReferenceValidator(self.make_doc())
				with patch.object(
					reference_validator, "get_party_account_based_on_invoice_discounting", return_value=None
				):
					self.assert_labels(
						lambda: validator._validate_reference_party_and_account(row),
						frappe._(party_type),
						frappe._(account_field),
						frappe._(reference_type),
						"INVOICE-RAW",
					)
				self.assertEqual(row, before)
				self.db.get_value.assert_called_once_with(
					reference_type, "INVOICE-RAW", [frappe.scrub(party_type), frappe.scrub(account_field)]
				)
				self.db.reset_mock()

	def test_order_party_and_status_diagnostics_preserve_reference_identity(self):
		validator = reference_validator.JournalEntryReferenceValidator(self.make_doc())
		for reference_type, party_type in (("Sales Order", "Customer"), ("Purchase Order", "Supplier")):
			with self.subTest(reference_type=reference_type, issue="party"):
				row = frappe._dict(
					idx=4, party_type=party_type, party="PARTY-RAW", reference_type=reference_type
				)
				self.assert_labels(
					lambda: validator._validate_order_party(row, "OTHER-PARTY"),
					frappe._(party_type),
					"PARTY-RAW",
					frappe._(reference_type),
				)
				validator._validate_order_party(row, "PARTY-RAW")
			for overrides in ({"docstatus": 0}, {"per_billed": 100}, {"status": "Closed"}):
				with self.subTest(reference_type=reference_type, issue=overrides):
					order = frappe._dict(docstatus=1, per_billed=0, status="To Deliver")
					order.update(overrides)
					before = order.copy()
					self.assert_labels(
						lambda: validator._validate_order_status(order, reference_type, "ORDER-RAW"),
						frappe._(reference_type),
						"ORDER-RAW",
					)
					self.assertEqual(order, before)
			validator._validate_order_status(
				frappe._dict(docstatus=1, per_billed=99, status="To Deliver"), "Sales Order", "ORDER-RAW"
			)

	def test_invoice_status_and_outstanding_errors_keep_amounts_and_precision(self):
		validator = reference_validator.JournalEntryReferenceValidator(self.make_doc())
		for reference_type in ("Sales Invoice", "Purchase Invoice"):
			for docstatus, total in ((0, 10), (1, 101)):
				with self.subTest(reference_type=reference_type, docstatus=docstatus):
					invoice = self.make_doc(
						Document, doctype=reference_type, docstatus=docstatus, outstanding_amount=100
					)
					before = deepcopy(invoice.__dict__)
					self.assert_labels(
						lambda: validator._validate_invoice_outstanding(
							invoice, total, reference_type, "INVOICE-RAW"
						),
						frappe._(reference_type),
						"INVOICE-RAW",
					)
					self.assertEqual(invoice.__dict__, before)
			invoice = self.make_doc(Document, doctype=reference_type, docstatus=1, outstanding_amount=100)
			validator._validate_invoice_outstanding(invoice, 100, reference_type, "INVOICE-RAW")

	def test_actual_hold_predicate_has_complete_dated_and_undated_diagnostics(self):
		validator = reference_validator.JournalEntryReferenceValidator(self.make_doc())
		for release_date in (None, "", date(2026, 10, 22)):
			with self.subTest(release_date=release_date):
				invoice = self.make_doc(
					PurchaseInvoice,
					doctype="Purchase Invoice",
					name="INVOICE-RAW",
					on_hold=1,
					release_date=release_date,
				)
				before = deepcopy(invoice.__dict__)
				with patch(
					"erpnext.accounts.doctype.purchase_invoice.purchase_invoice.nowdate",
					return_value="2026-10-21",
				):
					message = self.assert_labels(
						lambda: validator._validate_block_invoice(invoice),
						"Fattura di acquisto",
						"INVOICE-RAW",
					)
				self.assertEqual(invoice.__dict__, before)
				if release_date:
					self.assertIn("2026-10-22", message)
				else:
					self.assertNotIn("None", message)
					self.assertNotIn("until", message)
		for on_hold, release_date in ((0, None), (1, date(2026, 10, 21)), (1, date(2026, 10, 20))):
			invoice = self.make_doc(
				PurchaseInvoice, doctype="Purchase Invoice", on_hold=on_hold, release_date=release_date
			)
			with patch(
				"erpnext.accounts.doctype.purchase_invoice.purchase_invoice.nowdate",
				return_value="2026-10-21",
			):
				validator._validate_block_invoice(invoice)
		validator._validate_block_invoice(self.make_doc(Document, doctype="Sales Invoice"))

	def test_valid_reference_flow_keeps_canonical_queries_and_aggregated_totals(self):
		rows = []
		records = {}
		for doctype, party_type, amount in (
			("Sales Invoice", "Customer", 10),
			("Purchase Invoice", "Supplier", 15),
			("Sales Order", "Customer", 5),
			("Purchase Order", "Supplier", 8),
		):
			name = f"{doctype} RAW"
			rows.append(
				frappe._dict(
					idx=len(rows) + 1,
					reference_type=doctype,
					reference_name=name,
					party_type=party_type,
					party="PARTY-RAW",
					account="ACCOUNT-RAW",
					reference_detail_no=None,
					debit=amount if party_type == "Supplier" else 0,
					credit=amount if party_type == "Customer" else 0,
					debit_in_account_currency=amount if party_type == "Supplier" else 0,
					credit_in_account_currency=amount if party_type == "Customer" else 0,
				)
			)
			records[(doctype, name)] = self.make_doc(
				PurchaseInvoice if doctype == "Purchase Invoice" else Document,
				doctype=doctype,
				name=name,
				docstatus=1,
				per_billed=0,
				status="To Deliver",
				advance_paid=0,
				base_grand_total=100,
				grand_total=100,
				outstanding_amount=100,
				on_hold=0,
				release_date=None,
			)
		doc = self.make_doc(accounts=rows)
		before = deepcopy(rows)
		self.db.get_value.side_effect = (
			lambda doctype, name, fields: ("PARTY-RAW", "ACCOUNT-RAW") if len(fields) == 2 else "PARTY-RAW"
		)
		with (
			patch("frappe.get_doc", side_effect=lambda doctype, name: records[(doctype, name)]) as get_doc,
			patch("erpnext.get_company_currency", return_value="EUR"),
			patch.object(
				reference_validator, "get_party_account_based_on_invoice_discounting", return_value=None
			),
			patch.object(reference_validator, "get_account_currency", return_value="EUR"),
		):
			reference_validator.JournalEntryReferenceValidator(doc).validate()
		self.assertEqual(rows, before)
		self.assertEqual(
			doc.reference_totals,
			{
				"Sales Invoice RAW": 10,
				"Purchase Invoice RAW": 15,
				"Sales Order RAW": 5,
				"Purchase Order RAW": 8,
			},
		)
		self.assertEqual(doc.reference_types, {name: doctype for doctype, name in records})
		self.assertEqual(doc.reference_accounts, {name: "ACCOUNT-RAW" for doctype, name in records})
		self.assertEqual(
			get_doc.call_args_list,
			[
				call("Sales Order", "Sales Order RAW"),
				call("Purchase Order", "Purchase Order RAW"),
				call("Sales Invoice", "Sales Invoice RAW"),
				call("Purchase Invoice", "Purchase Invoice RAW"),
			],
		)
		self.assertEqual(
			self.db.get_value.call_args_list,
			[
				call("Sales Invoice", "Sales Invoice RAW", ["customer", "debit_to"]),
				call("Purchase Invoice", "Purchase Invoice RAW", ["supplier", "credit_to"]),
				call("Sales Order", "Sales Order RAW", ["customer"]),
				call("Purchase Order", "Purchase Order RAW", ["supplier"]),
			],
		)

	def test_journal_party_label_preserves_canonical_lookups(self):
		row = frappe._dict(idx=2, account="ACCOUNT-RAW", party_type="Customer", party="PARTY-RAW")
		doc = self.make_doc(accounts=[row])
		self.db.get_value.return_value = "Payable"
		with patch("frappe.get_cached_value", return_value="Receivable") as cached:
			self.assert_labels(doc.validate_party, "Cliente", "ACCOUNT-RAW")
		cached.assert_called_once_with("Account", "ACCOUNT-RAW", "account_type")
		self.db.get_value.assert_called_once_with("Party Type", "Customer", "account_type")
		self.assertEqual(row.party_type, "Customer")

	def test_bank_entry_label_preserves_type_and_cheque_controls(self):
		with patch.object(journal_entry, "msgprint", side_effect=self.raise_message):
			doc = self.make_doc(voucher_type="Bank Entry", cheque_no=None, cheque_date=None)
			self.assert_labels(doc.validate_cheque_info, "Registrazione bancaria")
			doc.cheque_no, doc.cheque_date = "CHEQUE-RAW", "2026-10-21"
			doc.validate_cheque_info()
			self.assertEqual(doc.voucher_type, "Bank Entry")

	def test_discounting_expected_status_labels_keep_canonical_next_statuses(self):
		for submitting, credit, debit, expected, next_status in (
			(1, 10, 0, "Sanctioned", "Disbursed"),
			(0, 10, 0, "Disbursed", "Sanctioned"),
			(1, 0, 10, "Disbursed", "Settled"),
			(0, 0, 10, "Settled", "Disbursed"),
		):
			with self.subTest(submitting=submitting, credit=credit):
				row = frappe._dict(
					idx=3, reference_name="FINANCING-RAW", account="LOAN-RAW", credit=credit, debit=debit
				)
				doc = self.make_doc(accounts=[row], docstatus=submitting)
				financing = frappe._dict(
					name="FINANCING-RAW", short_term_loan="LOAN-RAW", status="OTHER-STATUS"
				)
				with patch.object(
					journal_entry, "get_link_to_form", return_value="<a>FINANCING-RAW</a>"
				) as link:
					self.assert_labels(
						lambda: doc._get_next_invoice_discounting_status(financing),
						frappe._(expected),
						"FINANCING-RAW",
					)
				link.assert_called_once_with("Invoice Discounting", "FINANCING-RAW")
				financing.status = expected
				self.assertEqual(doc._get_next_invoice_discounting_status(financing), next_status)
				self.assertEqual(financing.status, expected)
				self.assertEqual(row.account, "LOAN-RAW")

	def test_stock_warning_reports_only_what_the_existing_queries_prove(self):
		doc = self.make_doc(stock_entry="STOCK-RAW", voucher_type="Bank Entry")
		self.db.get_value.return_value = 1
		self.db.exists.return_value = True
		with patch("frappe.msgprint") as notice:
			doc.validate_credit_debit_note()
			message = notice.call_args.args[0]
			self.assertEqual(
				message,
				"Attenzione: esiste una registrazione contabile confermata collegata al movimento di magazzino STOCK-RAW.",
			)
			self.assertNotIn(doc.name, message)
			self.assertNotIn(doc.voucher_type, message)
		self.db.get_value.assert_called_once_with("Stock Entry", "STOCK-RAW", "docstatus")
		self.db.exists.assert_called_once_with(
			{"doctype": "Journal Entry", "stock_entry": "STOCK-RAW", "docstatus": 1}
		)
		self.db.exists.return_value = False
		with patch("frappe.msgprint") as notice:
			doc.validate_credit_debit_note()
			notice.assert_not_called()
		self.db.get_value.return_value = 0
		self.assert_labels(doc.validate_credit_debit_note, "STOCK-RAW", "not submitted")

	def test_existing_localized_shared_and_advance_producers_remain_controls(self):
		with patch("frappe.get_lazy_doc", return_value=frappe._dict(docstatus=0)) as get_doc:
			self.assert_labels(
				lambda: item_close.update_closed_status("Sales Order", "ORDER-RAW", ["ROW-RAW"], 1),
				"Ordine di vendita",
				"ORDER-RAW",
			)
			get_doc.assert_called_once_with("Sales Order", "ORDER-RAW", check_permission="submit")
		order = self.make_doc(
			Document,
			doctype="Sales Order",
			base_grand_total=100,
			advance_paid=0,
			_precision=frappe._dict(main=frappe._dict(base_grand_total=2)),
		)
		validator = reference_validator.JournalEntryReferenceValidator(self.make_doc())
		with (
			patch("erpnext.get_company_currency", return_value="EUR"),
			patch.object(reference_validator, "get_account_currency", return_value="EUR"),
			patch.object(reference_validator, "fmt_money", return_value="EUR 100.00"),
		):
			self.assert_labels(
				lambda: validator._validate_order_advance_total(
					order, "ACCOUNT-RAW", 101, "Sales Order", "ORDER-RAW"
				),
				"Ordine di vendita",
				"ORDER-RAW",
				"EUR 100.00",
			)

	def test_new_complete_sources_are_extracted_by_the_actual_python_extractor(self):
		messages = set()
		for module in (journal_entry, reference_validator):
			with Path(module.__file__).open("rb") as source:
				messages.update(
					message
					for _line, message, _comments, _context in extract(
						"frappe.gettext.extractors.python.extract", source, keywords=PYTHON_KEYWORDS
					)
				)
		self.assertIn("Warning: A submitted Journal Entry exists against Stock Entry {0}.", messages)
		self.assertIn("{0} {1} is blocked and on hold.", messages)
