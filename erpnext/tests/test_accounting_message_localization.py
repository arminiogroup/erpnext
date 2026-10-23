# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import frappe

from erpnext.accounts import general_ledger
from erpnext.accounts.doctype.account import account
from erpnext.accounts.doctype.journal_entry import journal_entry
from erpnext.accounts.doctype.opening_invoice_creation_tool import opening_invoice_creation_tool
from erpnext.accounts.doctype.payment_entry import payment_entry
from erpnext.accounts.doctype.pricing_rule import utils as pricing_rule_utils
from erpnext.accounts.doctype.process_statement_of_accounts import process_statement_of_accounts
from erpnext.accounts.doctype.sales_invoice import mapper as sales_invoice_mapper
from erpnext.accounts.report.customer_ledger_summary import customer_ledger_summary
from erpnext.accounts.report.item_wise_sales_register import item_wise_sales_register
from erpnext.accounts.services import internal_transfer
from erpnext.selling.report.sales_analytics import sales_analytics


def raise_message(message, **kwargs):
	raise ValueError(message)


class TestAccountingMessageLocalization(unittest.TestCase):
	def test_dynamic_tax_column_labels_keep_account_names_and_values(self):
		translations = {"Rate for {0}": "Aliquota / tariffa di {0}", "Amount for {0}": "Importo di {0}"}
		for charge_type, rate in (("On Net Total", 22), ("On Item Quantity", 2), ("Actual", 0)):
			with self.subTest(charge_type=charge_type):
				query = MagicMock()
				for method in ("left_join", "on", "select", "where"):
					getattr(query, method).return_value = query
				query.run.return_value = [
					frappe._dict(
						item_row="ROW-RAW",
						account_head="Custom Account - RAW",
						description="Custom description",
						charge_type=charge_type,
						account_type="Tax",
						rate=rate,
						amount=11,
					)
				]
				columns = []
				with (
					patch.object(item_wise_sales_register, "frappe") as mock_frappe,
					patch.object(
						item_wise_sales_register, "_", side_effect=lambda text: translations.get(text, text)
					),
					patch.object(item_wise_sales_register, "get_tax_details_query", return_value=query),
					patch.object(item_wise_sales_register, "has_scrubbed_name_clash", return_value=False),
					patch("frappe.get_system_settings", return_value="Banker's Rounding"),
				):
					mock_frappe._dict.side_effect = frappe._dict
					mock_frappe.scrub.side_effect = frappe.scrub
					mock_frappe.get_precision.return_value = 2
					taxes, keys = item_wise_sales_register.get_tax_accounts(
						[frappe._dict(name="ROW-RAW")], columns, "EUR"
					)
				self.assertEqual(columns[0]["label"], "Aliquota / tariffa di Custom Account - RAW")
				self.assertEqual(columns[1]["label"], "Importo di Custom Account - RAW")
				self.assertEqual(columns[0]["fieldname"], "custom_account___raw_rate")
				self.assertEqual(taxes["ROW-RAW"][keys[0]].tax_amount, 11)
				self.assertEqual(taxes["ROW-RAW"][keys[0]].tax_rate, rate or "NA")

	def test_entity_name_columns_keep_canonical_translations_and_raw_options(self):
		translations = {
			"{0} Name": "Nome {0}",
			"Customer Name": "Nome cliente",
			"Supplier Name": "Nome fornitore",
			"Item Name": "Nome articolo",
			"Customer": "Cliente",
			"Supplier": "Fornitore",
			"Item": "Articolo",
		}
		for party_type in ("Customer", "Supplier"):
			with self.subTest(report="party ledger", party_type=party_type):
				report = SimpleNamespace(
					filters=frappe._dict(party_type=party_type),
					party_naming_by="Naming Series",
					party_adjustment_accounts=[],
				)
				with patch.object(
					customer_ledger_summary, "_", side_effect=lambda text: translations.get(text, text)
				):
					columns = customer_ledger_summary.PartyLedgerSummaryReport.get_columns(report)
				self.assertEqual(columns[1]["label"], translations[party_type + " Name"])
				self.assertEqual(columns[0]["options"], party_type)
		for tree_type in ("Customer", "Supplier", "Item"):
			with self.subTest(report="sales analytics", tree_type=tree_type):
				report = SimpleNamespace(filters=frappe._dict(tree_type=tree_type), periodic_daterange=[])
				with patch.object(
					sales_analytics, "_", side_effect=lambda text: translations.get(text, text)
				):
					sales_analytics.Analytics.get_columns(report)
				self.assertEqual(report.columns[1]["label"], translations[tree_type + " Name"])
				self.assertEqual(report.columns[0]["options"], tree_type)

	def test_statement_type_label_keeps_query_keys_and_record_names(self):
		translations = {
			"<p>The following records of type {0} do not belong to Company {1}:</p>": "<p>Le seguenti voci di tipo {0} non appartengono all’azienda {1}:</p>",
			"Cost Center": "Centro di costo",
		}
		doc = SimpleNamespace(company="Company RAW")
		doc.get = lambda field: [frappe._dict(cost_center_name="Center RAW")]
		with (
			patch.object(process_statement_of_accounts, "frappe") as mock_frappe,
			patch.object(
				process_statement_of_accounts, "_", side_effect=lambda text: translations.get(text, text)
			),
		):
			mock_frappe.scrub.side_effect = frappe.scrub
			mock_frappe.bold.side_effect = frappe.bold
			mock_frappe.db.get_all.return_value = ["Center RAW"]
			mock_frappe.throw.side_effect = raise_message
			with self.assertRaises(ValueError) as error:
				process_statement_of_accounts.ProcessStatementOfAccounts.validate_company_for_table(
					doc, "Cost Center"
				)
			self.assertIn("tipo Centro di costo", str(error.exception))
			self.assertIn("<strong>Company RAW</strong>", str(error.exception))
			self.assertIn("<li><strong>Center RAW</strong></li>", str(error.exception))
			mock_frappe.db.get_all.assert_called_once_with(
				"Cost Center",
				filters={"name": ["in", {"Center RAW"}], "company": ["!=", "Company RAW"]},
				pluck="name",
			)

	def test_default_account_role_is_a_translated_label_not_a_stored_field_key(self):
		translations = {"Default Bank Account": "Conto bancario predefinito"}
		for disabled in (False, True):
			with self.subTest(disabled=disabled):
				doc = SimpleNamespace(name="Account RAW", company="Company RAW", disabled=disabled)
				with (
					patch.object(account, "frappe") as mock_frappe,
					patch.object(account, "_", side_effect=lambda text: translations.get(text, text)),
					patch.object(account, "throw", side_effect=raise_message),
				):
					mock_frappe.bold.side_effect = frappe.bold
					mock_frappe.db.get_value.return_value = frappe._dict(default_bank_account="Account RAW")
					with self.assertRaises(ValueError) as error:
						account.Account.validate_default_accounts_in_company(doc)
					self.assertIn("Conto bancario predefinito", str(error.exception))
					self.assertIn("Account RAW", str(error.exception))
					self.assertIn("Company RAW", str(error.exception))
					self.assertEqual(mock_frappe.db.get_value.call_args.args[0], "Company")
					self.assertIn("default_bank_account", mock_frappe.db.get_value.call_args.args[2])

	def test_unmatched_accounting_side_uses_canonical_debit_credit_labels(self):
		translations = {"Debit": "Dare", "Credit": "Avere"}
		for credit, expected in ((5, "Dare"), (0, "Avere")):
			with self.subTest(credit=credit):
				entries = [{"debit": 0, "credit": 0}]
				doc = SimpleNamespace(
					name="JE-CURRENT",
					voucher_type="Journal Entry",
					_validate_jv_reference_direction=Mock(),
					_get_against_jv_entries=Mock(return_value=entries),
					system_generated_gain_loss=Mock(return_value=False),
				)
				row = frappe._dict(reference_name="JE-RAW", credit=credit)
				with (
					patch.object(journal_entry, "frappe") as mock_frappe,
					patch.object(journal_entry, "_", side_effect=lambda text: translations.get(text, text)),
				):
					mock_frappe.throw.side_effect = raise_message
					with self.assertRaises(ValueError) as error:
						journal_entry.JournalEntry._validate_jv_reference(doc, row)
					self.assertIn(expected, str(error.exception))
					self.assertEqual(entries, [{"debit": 0, "credit": 0}])
					self.assertEqual(row.credit, credit)

	def test_posting_error_translates_type_but_preserves_voucher_name(self):
		translations = {"Sales Invoice": "Fattura di vendita"}
		with (
			patch.object(general_ledger, "frappe") as mock_frappe,
			patch.object(general_ledger, "_", side_effect=lambda text: translations.get(text, text)),
		):
			mock_frappe.throw.side_effect = raise_message
			with self.assertRaises(ValueError) as error:
				general_ledger.raise_debit_credit_not_equal_error(10, "Sales Invoice", "Sales Invoice RAW")
			self.assertIn("Fattura di vendita", str(error.exception))
			self.assertIn("Sales Invoice RAW", str(error.exception))

	def test_pricing_suggestions_keep_thresholds_names_and_purchase_sale_behavior(self):
		translations = {
			"At a quantity of {0} for Item {1}, scheme {2} applies.": "Con quantità {0} dell’articolo {1}, si applica la regola {2}.",
			"At an amount of {0} for Item {1}, scheme {2} applies.": "Con importo {0} dell’articolo {1}, si applica la regola {2}.",
		}
		for transaction in ("buying", "selling"):
			for field, value in (("min_qty", 9), ("max_qty", 11), ("min_amt", 9), ("max_amt", 11)):
				with self.subTest(transaction=transaction, field=field):
					args = frappe._dict(title="Custom Rule RAW", threshold_percentage=10, currency="EUR")
					args[field] = 10
					original = args.copy()
					with (
						patch.object(pricing_rule_utils, "frappe") as mock_frappe,
						patch.object(
							pricing_rule_utils, "_", side_effect=lambda text: translations.get(text, text)
						),
						patch.object(pricing_rule_utils, "fmt_money", return_value="EUR 10.00"),
					):
						message = pricing_rule_utils.validate_quantity_and_amount_for_suggestion(
							args,
							value if field.endswith("qty") else 0,
							value if field.endswith("amt") else 0,
							"ITEM-RAW",
							transaction,
						)
						self.assertTrue(
							message.startswith("Con quantità" if field.endswith("qty") else "Con importo")
						)
						self.assertIn("ITEM-RAW", message)
						self.assertIn("Custom Rule RAW", message)
						self.assertIn("10", message)
						self.assertEqual(args, original)
						mock_frappe.msgprint.assert_called_once_with(message)
		args = frappe._dict(title="Custom Rule RAW", threshold_percentage=10, min_qty=10)
		with patch.object(pricing_rule_utils, "frappe") as mock_frappe:
			self.assertEqual(
				pricing_rule_utils.validate_quantity_and_amount_for_suggestion(
					args, 5, 0, "ITEM-RAW", "buying"
				),
				"",
			)
			mock_frappe.msgprint.assert_not_called()

	def test_opening_invoice_progress_translates_type_and_preserves_event_data(self):
		translations = {
			"Creating {0} out of {1} documents of type {2}": "Creazione di documenti di tipo {2}: {0} su {1}",
			"Sales Invoice": "Fattura di vendita",
		}
		errors = ["ROW-RAW"]
		with (
			patch.object(opening_invoice_creation_tool, "frappe") as mock_frappe,
			patch.object(
				opening_invoice_creation_tool, "_", side_effect=lambda text: translations.get(text, text)
			),
		):
			mock_frappe.session.user = "User RAW"
			opening_invoice_creation_tool.publish(1, 5, "Sales Invoice", errors)
			event, payload = mock_frappe.publish_realtime.call_args.args
			self.assertEqual(event, "opening_invoice_creation_progress")
			self.assertEqual(payload["message"], "Creazione di documenti di tipo Fattura di vendita: 2 su 5")
			self.assertEqual((payload["count"], payload["total"]), (2, 5))
			self.assertIs(payload["errors"], errors)
			self.assertEqual(mock_frappe.publish_realtime.call_args.kwargs["user"], "User RAW")

	def test_intercompany_empty_mapping_uses_complete_translated_type_messages(self):
		translations = {
			"Sales Invoice": "Fattura di vendita",
			"Purchase Invoice": "Fattura di acquisto",
			"Sales Order": "Ordine di vendita",
			"Purchase Order": "Ordine di acquisto",
			"Cannot create Intercompany {0}. All items in the source {1} have already been fully invoiced. Please check the existing linked documents of type {2}.": "Impossibile creare il documento interaziendale di tipo {0}: tutte le righe del documento di origine di tipo {1} sono già state interamente fatturate. Controllare i documenti collegati esistenti di tipo {2}.",
			"Cannot create Intercompany {0}. All items in the source {1} have already been fully ordered. Please check the existing linked documents of type {2}.": "Impossibile creare il documento interaziendale di tipo {0}: tutte le righe del documento di origine di tipo {1} sono già state interamente ordinate. Controllare i documenti collegati esistenti di tipo {2}.",
		}
		for source_type, target_type in (
			("Sales Invoice", "Purchase Invoice"),
			("Sales Order", "Purchase Order"),
		):
			with self.subTest(source_type=source_type):
				with (
					patch.object(sales_invoice_mapper, "frappe") as mock_frappe,
					patch.object(
						sales_invoice_mapper, "_", side_effect=lambda text: translations.get(text, text)
					),
					patch.object(sales_invoice_mapper, "get_received_items", return_value={}),
					patch.object(sales_invoice_mapper, "validate_inter_company_transaction"),
					patch.object(sales_invoice_mapper, "get_inter_company_details", return_value={}),
					patch.object(
						sales_invoice_mapper, "get_mapped_doc", return_value=frappe._dict(items=[])
					) as mapping,
				):
					mock_frappe.get_doc.return_value = frappe._dict()
					mock_frappe.throw.side_effect = raise_message
					with self.assertRaises(ValueError) as error:
						sales_invoice_mapper.make_inter_company_transaction(source_type, "SOURCE-RAW")
					self.assertTrue(str(error.exception).startswith("Impossibile creare"))
					self.assertIn(translations[source_type], str(error.exception))
					self.assertIn(translations[target_type], str(error.exception))
					self.assertIn(
						"fatturate" if source_type.endswith("Invoice") else "ordinate", str(error.exception)
					)
					mock_frappe.get_doc.assert_called_once_with(source_type, "SOURCE-RAW")
					self.assertEqual(mapping.call_args.args[:2], (source_type, "SOURCE-RAW"))
					self.assertEqual(mapping.call_args.args[2][source_type]["doctype"], target_type)

	def test_negative_outstanding_warning_keeps_refund_allocation_guard(self):
		for payment_type, party_type in (("Pay", "Customer"), ("Receive", "Supplier")):
			with self.subTest(payment_type=payment_type):
				doc = SimpleNamespace(
					payment_type=payment_type,
					party_type=party_type,
					references=[frappe._dict(outstanding_amount=10, allocated_amount=0)],
					deductions=[],
					precision=lambda field: 2,
				)
				with (
					patch.object(payment_entry.frappe, "msgprint") as message,
					patch.object(
						payment_entry,
						"_",
						side_effect=lambda text: {
							"An invoice with a negative outstanding amount is required for this payment.": "Per questo pagamento è necessaria una fattura con un importo residuo negativo."
						}.get(text, text),
					),
					patch("frappe.get_system_settings", return_value="Banker's Rounding"),
				):
					payment_entry.PaymentEntry.allocate_amount_to_references(doc, 5, True, True)
					message.assert_called_once_with(
						"Per questo pagamento è necessaria una fattura con un importo residuo negativo."
					)
					self.assertEqual(doc.references[0].allocated_amount, 0)
					self.assertEqual((doc.payment_type, doc.party_type), (payment_type, party_type))

	def test_internal_transfer_notices_translate_type_and_keep_field_changes(self):
		doc = frappe._dict(
			doctype="Sales Invoice",
			company="Company RAW",
			represents_company="Company RAW",
			is_internal_customer=1,
			ignore_pricing_rule=0,
			taxes=[frappe._dict(included_in_print_rate=1)],
		)
		translations = {
			"Sales Invoice": "Fattura di vendita",
			"Disabled pricing rules since this {0} is an internal transfer": "Regole di prezzo disabilitate: il documento di tipo {0} è un trasferimento interno",
			"Disabled tax included prices since this {0} is an internal transfer": "Prezzi comprensivi di imposte disabilitati: il documento di tipo {0} è un trasferimento interno",
		}
		service = internal_transfer.InternalTransferService(doc)
		with (
			patch.object(internal_transfer.frappe, "msgprint") as message,
			patch.object(internal_transfer, "_", side_effect=lambda text: translations.get(text, text)),
		):
			service.disable_pricing_rule()
			service.disable_tax_included_prices()
			self.assertEqual(message.call_count, 2)
			for call in message.call_args_list:
				self.assertIn("Fattura di vendita", call.args[0])
				self.assertEqual(call.kwargs, {"alert": 1})
			self.assertEqual(doc.ignore_pricing_rule, 1)
			self.assertEqual(doc.taxes[0].included_in_print_rate, 0)
			self.assertEqual(doc.doctype, "Sales Invoice")
