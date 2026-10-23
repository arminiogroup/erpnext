# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from unittest.mock import Mock, call, patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import get_link_to_form

from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice
from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.accounts.services.internal_transfer import InternalTransferService
from erpnext.controllers.accounts_controller import AccountMissingError, AccountsController
from erpnext.utilities.test_transaction_label_translation import (
	COMPANY_ACCOUNT,
	METADATA,
	MISSING_ACCOUNT,
	PARTY_ACCOUNT,
	RETURN_WARNING,
	TARGETS,
	extracted_keys,
	make_doc,
	raise_message,
	read_meta,
)
from erpnext.utilities.transaction_base import TransactionBase


class TestTransactionAccountLabelTranslation(UnitTestCase):
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
		self.metadata = {name: read_meta(module, name) for module, name in METADATA}
		self.enterContext(patch("frappe.get_meta", side_effect=self.metadata.__getitem__))
		self.enterContext(patch("frappe.model.meta.get_meta", side_effect=self.metadata.__getitem__))
		self.enterContext(patch("frappe.throw", side_effect=raise_message))
		self.enterContext(patch("frappe.get_system_settings", return_value="Banker's Rounding"))
		self.enterContext(
			patch("frappe.utils.data.get_url", side_effect=lambda uri: "https://example.invalid" + uri)
		)
		self.db = self.enterContext(patch("frappe.db", new=Mock(db_type="mariadb")))
		self.enterContext(patch.object(frappe.local, "db", self.db, create=True))

	def error(self, action, exception=frappe.ValidationError):
		with self.assertRaises(exception) as caught:
			action()
		return str(caught.exception)

	def invoice(self, cls, doctype, **values):
		defaults = dict(
			doctype=doctype,
			company="Company",
			customer="Customer",
			supplier="Supplier",
			debit_to=None,
			credit_to=None,
			meta=self.metadata[doctype],
		)
		defaults.update(values)
		return make_doc(cls, **defaults)

	def test_actual_sales_purchase_and_inherited_POS_missing_account_paths(self):
		for cls, doctype, party_type, method, provider, account_label, default_label in (
			(
				SalesInvoice,
				"Sales Invoice",
				"Customer",
				"validate_debit_to_acc",
				"sales_invoice",
				"Conto da addebitare",
				"Conto crediti predefinito",
			),
			(
				POSInvoice,
				"POS Invoice",
				"Customer",
				"validate_debit_to_acc",
				"sales_invoice",
				"Conto da addebitare",
				"Conto crediti predefinito",
			),
			(
				PurchaseInvoice,
				"Purchase Invoice",
				"Supplier",
				"validate_credit_to_acc",
				"purchase_invoice",
				"Conto da accreditare",
				"Conto debiti predefinito",
			),
		):
			with self.subTest(doctype=doctype):
				doc = self.invoice(cls, doctype)
				with patch(
					f"erpnext.accounts.doctype.{provider}.{provider}.get_party_account", return_value=None
				) as account, patch("frappe.get_cached_value") as cache:
					message = self.error(getattr(doc, method), AccountMissingError)
				account.assert_called_once_with(party_type, party_type, "Company")
				cache.assert_not_called()
				self.assertIn(f"Nessun conto configurato nel campo <strong>{account_label}</strong>", message)
				self.assertIn(f"<strong>{default_label}</strong>", message)
				self.assertIn("<strong>Conto predefinito</strong>", message)
				self.assertIn("<strong>Contabilità</strong>", message)
				self.assertIn(
					"Conti predefiniti" if party_type == "Customer" else "Conti per azienda", message
				)
				self.assertIn(get_link_to_form(party_type, party_type), message)
				self.assertIn(get_link_to_form("Company", "Company"), message)
				self.assertEqual(message.count("<li>"), 2)
				self.assertEqual(message.count("</li>"), 2)
				self.assertIn("</li></ul>", message)
				self.assertEqual(doc.company, "Company")
				self.assertEqual(doc.get("debit_to" if party_type == "Customer" else "credit_to"), None)
		self.assertTrue(self.metadata["POS Invoice"].has_field("debit_to"))
		self.assertFalse(self.metadata["POS Invoice"].has_field("credit_to"))

	def test_valid_actual_account_validators_keep_the_same_account_and_currency(self):
		for cls, doctype, field, account_type, method in (
			(SalesInvoice, "Sales Invoice", "debit_to", "Receivable", "validate_debit_to_acc"),
			(POSInvoice, "POS Invoice", "debit_to", "Receivable", "validate_debit_to_acc"),
			(PurchaseInvoice, "Purchase Invoice", "credit_to", "Payable", "validate_credit_to_acc"),
		):
			doc = self.invoice(cls, doctype, **{field: "ACCOUNT-RAW"})
			with patch(
				"frappe.get_cached_value",
				return_value=frappe._dict(
					account_type=account_type, report_type="Balance Sheet", account_currency="EUR"
				),
			) as cache:
				getattr(doc, method)()
			cache.assert_called_once_with(
				"Account", "ACCOUNT-RAW", ["account_type", "report_type", "account_currency"], as_dict=True
			)
			self.assertEqual(doc.get(field), "ACCOUNT-RAW")
			self.assertEqual(doc.party_account_currency, "EUR")

	def test_account_instructions_use_real_metadata_instead_of_synthesized_labels(self):
		self.metadata["POS Invoice"]._fields["debit_to"].label = "Default Account"
		self.metadata["Company"]._fields["default_receivable_account"].label = "Advance Account"
		doc = self.invoice(POSInvoice, "POS Invoice")
		message = self.error(
			lambda: doc.raise_missing_debit_credit_account_error("Customer", "PARTY-RAW"), AccountMissingError
		)
		self.assertIn("Nessun conto configurato nel campo <strong>Conto predefinito</strong>", message)
		self.assertIn("<strong>Conto anticipi</strong> nell’azienda", message)
		self.assertNotIn("Default Receivable Account", message)

	def return_doc(self, doctype="Sales Invoice", **values):
		defaults = dict(
			doctype=doctype,
			meta=self.metadata[doctype],
			is_return=1,
			return_against="Sales Invoice",
			is_pos=0,
			is_paid=0,
			update_outstanding_for_self=1,
			rounded_total=-10,
			grand_total=-10,
			outstanding_amount=-10,
		)
		defaults.update(values)
		return make_doc(AccountsController, **defaults)

	def test_return_warning_translates_complete_note_and_actual_checkbox_label(self):
		for doctype, note in (("Sales Invoice", "Nota di credito"), ("Purchase Invoice", "Nota di debito")):
			with self.subTest(doctype=doctype):
				doc = self.return_doc(doctype)
				with patch("frappe.get_value", return_value=100) as original, patch(
					"frappe.msgprint"
				) as message:
					doc.validate_against_voucher_outstanding()
				text = message.call_args.args[0]
				original.assert_called_once_with(doctype, "Sales Invoice", "outstanding_amount")
				self.assertIn(f"Questo documento (<strong>{note}</strong>)", text)
				self.assertIn("<strong>Aggiorna il saldo residuo di questo documento</strong>", text)
				self.assertEqual(text.count(get_link_to_form(doctype, "Sales Invoice")), 3)
				self.assertIn(get_link_to_form("Payment Reconciliation"), text)
				self.assertIn("<br><br>", text)
				self.assertEqual(doc.update_outstanding_for_self, 1)
				self.assertEqual(doc.return_against, "Sales Invoice")

	def test_outstanding_alternate_branch_preserves_threshold_and_mutation(self):
		for total, expected_flag, expected_messages in ((-120, 1, 1), (-100, 0, 0), (-80, 0, 0)):
			doc = self.return_doc(
				update_outstanding_for_self=0,
				rounded_total=total,
				grand_total=total,
				outstanding_amount=total,
			)
			with patch("frappe.get_value", return_value=100) as original, patch("frappe.msgprint") as message:
				doc.validate_against_voucher_outstanding()
			self.assertEqual(doc.update_outstanding_for_self, expected_flag)
			self.assertEqual(message.call_count, expected_messages)
			original.assert_called_once_with("Sales Invoice", "Sales Invoice", "outstanding_amount")
			if expected_messages:
				self.assertIn("100", message.call_args.args[0])
				self.assertIn("120.0", message.call_args.args[0])

	def test_outstanding_early_exit_controls_do_not_read_or_warn(self):
		for values in ({"is_return": 0}, {"return_against": None}, {"is_pos": 1}, {"is_paid": 1}):
			doc = self.return_doc(**values)
			with patch("frappe.get_value") as original, patch("frappe.msgprint") as message:
				doc.validate_against_voucher_outstanding()
			original.assert_not_called()
			message.assert_not_called()
		self.metadata["Sales Invoice"]._fields.pop("outstanding_amount")
		with patch("frappe.get_value") as original:
			self.return_doc().validate_against_voucher_outstanding()
		original.assert_not_called()

	def test_outstanding_uses_grand_total_when_rounded_total_is_zero(self):
		doc = self.return_doc(
			update_outstanding_for_self=0, rounded_total=0, grand_total=-120, outstanding_amount=-120
		)
		with patch("frappe.get_value", return_value=100), patch("frappe.msgprint") as message:
			doc.validate_against_voucher_outstanding()
		self.assertEqual(doc.update_outstanding_for_self, 1)
		self.assertIn("120.0", message.call_args.args[0])
		self.assertEqual((doc.rounded_total, doc.grand_total), (0, -120))

	def test_dimension_type_display_does_not_translate_business_names(self):
		for dimension in ("Project", "Cost Center", "Warehouse"):
			for use_child in (False, True):
				with self.subTest(dimension=dimension, child=use_child):
					doc = make_doc(
						AccountsController,
						doctype="Sales Invoice",
						company="Company",
						**{frappe.scrub(dimension): dimension},
					)
					child = make_doc(**{frappe.scrub(dimension): dimension}) if use_child else None
					with patch("frappe.get_cached_value", return_value="OTHER-COMPANY-RAW") as cache:
						message = self.error(lambda: doc.validate_company([dimension], child))
					self.assertEqual(
						message,
						f"{TARGETS[dimension]}: <strong>{dimension}</strong> non appartiene all’azienda Company.",
					)
					cache.assert_called_once_with(dimension, dimension, "company")
					self.assertEqual(doc.company, "Company")

	def test_actual_dimension_query_and_child_dispatch_stay_canonical(self):
		child = make_doc(doctype="Sales Invoice Item", idx=1, warehouse="WAREHOUSE-RAW")
		doc = make_doc(
			AccountsController,
			doctype="Sales Invoice",
			company="Company",
			meta=self.metadata["Sales Invoice"],
			items=[child],
		)
		self.db.sql.return_value = [["Warehouse"]]
		with patch("frappe.get_cached_value", return_value="OTHER-COMPANY-RAW") as cache:
			message = self.error(doc.validate_company_in_accounting_dimension)
		self.assertIn("Magazzino: <strong>WAREHOUSE-RAW</strong>", message)
		cache.assert_called_once_with("Warehouse", "WAREHOUSE-RAW", "company")
		query, params = self.db.sql.call_args.args
		self.assertIn("tabAccounting Dimension", query)
		self.assertIn("document_type", query)
		self.assertIn("tabDocField", query)
		self.assertIn("company", params.values())

	def test_same_missing_company_and_blank_dimension_controls(self):
		doc = make_doc(AccountsController, doctype="Sales Invoice", company="Company", project="Project")
		for company in ("Company", None, ""):
			with patch("frappe.get_cached_value", return_value=company):
				doc.validate_company(["Project"])
		doc.project = None
		with patch("frappe.get_cached_value") as cache:
			doc.validate_company(["Project"])
		cache.assert_not_called()

	def test_internal_missing_reference_has_a_real_sentence_separator(self):
		for doctype in ("Purchase Invoice", "Purchase Receipt"):
			doc = make_doc(
				TransactionBase,
				doctype=doctype,
				is_internal_supplier=1,
				company="Company",
				represents_company="Company",
				is_return=0,
			)
			message = self.error(InternalTransferService(doc).validate_reference)
			self.assertEqual(
				message,
				"Manca il riferimento alla vendita interna o alla consegna. Creare il documento di acquisto direttamente dal documento interno di vendita o di consegna.",
			)

	def test_internal_reference_return_noninternal_and_present_reference_controls(self):
		for values in (
			{"is_return": 1},
			{"is_internal_supplier": 0},
			{"doctype": "Sales Order"},
			{"inter_company_invoice_reference": "INVOICE-RAW"},
		):
			defaults = dict(
				doctype="Purchase Invoice",
				is_return=0,
				is_internal_supplier=1,
				company="Company",
				represents_company="Company",
				items=[],
			)
			defaults.update(values)
			InternalTransferService(make_doc(TransactionBase, **defaults)).validate_reference()

	def internal_rate_doc(self, doctype, rate=12):
		counterpart = {
			"Sales Order": "Purchase Order",
			"Purchase Order": "Sales Order",
			"Sales Invoice": "Purchase Invoice",
			"Purchase Invoice": "Sales Invoice",
		}[doctype]
		prefix = frappe.scrub(counterpart)
		row = make_doc(
			idx=4,
			parenttype=doctype,
			rate=rate,
			_precision=frappe._dict(main=frappe._dict(rate=2)),
			**{prefix: "ROW-DOC-RAW", prefix + "_item": "ROW-REF-RAW"},
		)
		doc = make_doc(
			TransactionBase,
			doctype=doctype,
			is_internal_customer=1,
			is_internal_supplier=1,
			inter_company_order_reference="ORDER-RAW",
			inter_company_invoice_reference="INVOICE-RAW",
			items=[row],
		)
		return doc, counterpart

	def test_actual_internal_rate_dispatch_translates_types_and_preserves_stop_warn(self):
		for doctype in ("Sales Order", "Purchase Order", "Sales Invoice", "Purchase Invoice"):
			for action in ("Stop", "Warn"):
				with self.subTest(doctype=doctype, action=action):
					doc, counterpart = self.internal_rate_doc(doctype)

					def read_all(name, **kwargs):
						return [] if name == "Has Role" else [["ROW-REF-RAW", 10]]

					with patch("frappe.get_single_value", return_value=1), patch(
						"frappe.get_cached_value", return_value=[action, "OVERRIDE-RAW"]
					) as settings, patch("frappe.get_all", side_effect=read_all) as reads, patch(
						"frappe.msgprint"
					) as message:
						if action == "Stop":
							text = self.error(InternalTransferService(doc).validate_transaction)
						else:
							InternalTransferService(doc).validate_transaction()
							text = message.call_args.args[0]
							self.assertEqual(
								message.call_args.kwargs, {"title": "Avviso", "indicator": "orange"}
							)
					ref_name = "INVOICE-RAW" if doctype.endswith("Invoice") else "ROW-DOC-RAW"
					self.assertIn(f"documento {TARGETS[counterpart]} {ref_name} (12 / 10)", text)
					self.assertEqual(
						reads.call_args_list[0],
						call(
							counterpart + " Item",
							filters={"name": ("in", ["ROW-REF-RAW"])},
							fields=["name", "rate"],
							as_list=1,
						),
					)
					if action == "Stop":
						self.assertEqual(
							reads.call_args_list[1],
							call(
								"Has Role",
								filters={"parent": "translator@example.com"},
								fields=["role"],
								pluck="role",
							),
						)
					settings.assert_called_once_with(
						"Accounts Settings",
						"None",
						["maintain_same_rate_action", "role_to_override_stop_action"],
					)
					self.assertEqual(doc.items[0].rate, 12)
					self.assertEqual(doc.inter_company_invoice_reference, "INVOICE-RAW")

	def test_internal_rate_override_rounding_absent_rate_and_disabled_controls(self):
		for rate, previous in ((10.004, 10), (12, None), (12, 10)):
			doc = self.internal_rate_doc("Sales Invoice", rate)[0]

			def read_all(name, **kwargs):
				return ["OVERRIDE-RAW"] if name == "Has Role" else [["ROW-REF-RAW", previous]]

			with patch("frappe.get_single_value", return_value=1), patch(
				"frappe.get_cached_value", return_value=["Stop", "OVERRIDE-RAW"]
			), patch("frappe.get_all", side_effect=read_all), patch("frappe.msgprint") as message:
				InternalTransferService(doc).validate_transaction()
			message.assert_not_called()
		with patch("frappe.get_single_value", return_value=0), patch("frappe.get_cached_value") as settings:
			InternalTransferService(doc).validate_transaction()
		settings.assert_not_called()

	def test_new_complete_account_and_return_frames_are_extracted(self):
		keys = extracted_keys()
		for source in (MISSING_ACCOUNT, PARTY_ACCOUNT, COMPANY_ACCOUNT, RETURN_WARNING):
			with self.subTest(source=source):
				self.assertIn(source, keys)
