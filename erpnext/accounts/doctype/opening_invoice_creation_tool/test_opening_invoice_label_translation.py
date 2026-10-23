# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, call, patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.opening_invoice_creation_tool import opening_invoice_creation_tool as opening


class TestOpeningInvoiceLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={
					"Customer": "Cliente",
					"Supplier": "Fornitore",
					"Sales Invoice": "Fattura di vendita",
					"Purchase Invoice": "Fattura di acquisto",
					"Temporary": "Temporaneo",
					"Outstanding Amount": "Importo da saldare",
					"Temporary Opening Account": "Conto temporaneo di apertura",
					"Error Log": "Registro degli errori",
					"Row #{0}: {1} is required to create an opening invoice of type {2}.": "Riga {0}: compilare {1} per creare una fattura di apertura di tipo {2}.",
					"You had {0} errors while creating opening invoices. Check {1} for more details": "Si sono verificati {0} errori durante la creazione delle fatture di apertura. Consultare {1}.",
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
	def make_tool(invoice_type="Sales"):
		doc = opening.OpeningInvoiceCreationTool.__new__(opening.OpeningInvoiceCreationTool)
		doc.__dict__.update(
			doctype="Opening Invoice Creation Tool",
			company="COMPANY-RAW",
			cost_center="CENTER-RAW",
			project="PROJECT-RAW",
			invoice_type=invoice_type,
			create_missing_party=False,
			flags=frappe._dict(),
		)
		return doc

	@staticmethod
	def make_row(party_type="Customer", **values):
		row = frappe._dict(
			idx=2,
			party_type=party_type,
			party="PARTY-RAW",
			party_name="PARTY-NAME-RAW",
			outstanding_amount=50,
			temporary_opening_account="Temporary",
			qty=2,
			currency="EUR",
			item_name="ITEM-NAME-RAW",
			due_date="2026-10-31",
			posting_date="2026-10-21",
			invoice_number="INVOICE-NUMBER-RAW",
			cost_center="CENTER-RAW",
			project="PROJECT-RAW",
		)
		row.update(values)
		return row

	def test_missing_party_translates_type_only_and_preserves_bold_business_identity(self):
		self.db.exists.return_value = False
		for invoice_type, party_type in (("Sales", "Customer"), ("Purchase", "Supplier")):
			with self.subTest(invoice_type=invoice_type):
				doc = self.make_tool(invoice_type)
				row = self.make_row(party_type, party="Customer")
				before = row.copy()
				with self.assertRaises(frappe.ValidationError) as error:
					doc.validate_mandatory_invoice_fields(row)
				self.assertIn(f"<strong>{frappe._(party_type)}</strong>", str(error.exception))
				self.assertIn("<strong>Customer</strong>", str(error.exception))
				self.assertEqual(row, before)
				self.db.exists.assert_called_once_with(party_type, "Customer")
				self.db.reset_mock()

	def test_required_fields_use_complete_invoice_type_messages_and_canonical_row_keys(self):
		self.db.exists.return_value = True
		for invoice_type, party_type, doctype in (
			("Sales", "Customer", "Sales Invoice"),
			("Purchase", "Supplier", "Purchase Invoice"),
		):
			for field, label in (
				("outstanding_amount", "Outstanding Amount"),
				("temporary_opening_account", "Temporary Opening Account"),
			):
				with self.subTest(invoice_type=invoice_type, field=field):
					doc = self.make_tool(invoice_type)
					row = self.make_row(party_type, **{field: None})
					before = row.copy()
					with self.assertRaises(frappe.ValidationError) as error:
						doc.validate_mandatory_invoice_fields(row)
					self.assertEqual(
						str(error.exception),
						f"Riga 2: compilare {frappe._(label)} per creare una fattura di apertura di tipo {frappe._(doctype)}.",
					)
					self.assertEqual(row, before)
					self.assertEqual(doc.invoice_type, invoice_type)

	def test_temporary_type_is_translated_without_translating_account_identity(self):
		doc = self.make_tool()
		row = self.make_row()
		before = row.copy()
		with patch("frappe.get_cached_value", return_value="Expense Account") as cached:
			with self.assertRaises(frappe.ValidationError) as error:
				doc.validate_temporary_opening_account(row)
			self.assertIn("Temporary account", str(error.exception))
			self.assertIn("type Temporaneo", str(error.exception))
			cached.assert_called_once_with("Account", "Temporary", "account_type")
		with patch("frappe.get_cached_value", return_value="Temporary"):
			doc.validate_temporary_opening_account(row)
		self.assertEqual(row, before)

	def test_generated_invoice_payload_and_real_amount_math_remain_canonical(self):
		meta = Mock()
		meta.get_field.side_effect = lambda field: frappe._dict(
			precision=2, fieldtype="Float" if field == "qty" else "Currency"
		)
		for invoice_type, party_type, doctype, account_field in (
			("Sales", "Customer", "Sales Invoice", "income_account"),
			("Purchase", "Supplier", "Purchase Invoice", "expense_account"),
		):
			with self.subTest(invoice_type=invoice_type):
				doc = self.make_tool(invoice_type)
				row = self.make_row(party_type)
				before = row.copy()
				with (
					patch("frappe.get_meta", return_value=meta) as get_meta,
					patch.object(opening, "get_default_stock_uom", return_value="Nos"),
					patch.object(opening, "get_accounting_dimensions", return_value=[]),
				):
					invoice = doc.get_invoice_dict(row)
				get_meta.assert_called_once_with(f"{invoice_type} Invoice Item")
				self.assertEqual(row, before)
				self.assertEqual(invoice["doctype"], doctype)
				self.assertEqual(invoice[frappe.scrub(party_type)], "PARTY-RAW")
				self.assertEqual(invoice["company"], "COMPANY-RAW")
				self.assertEqual(invoice["invoice_number"], "INVOICE-NUMBER-RAW")
				self.assertEqual(invoice["is_opening"], "Yes")
				self.assertEqual(invoice["disable_rounded_total"], 1)
				self.assertEqual(invoice["update_stock"], 0)
				item = invoice["items"][0]
				self.assertEqual((item["qty"], item["rate"], item["qty"] * item["rate"]), (2, 25, 50))
				self.assertEqual(item["uom"], "Nos")
				self.assertEqual(item[account_field], "Temporary")
				self.assertEqual((item["cost_center"], item["project"]), ("CENTER-RAW", "PROJECT-RAW"))

	def test_real_import_failure_reporting_translates_only_error_log_caption(self):
		invoices = [
			frappe._dict(doctype="Sales Invoice", invoice_number="CUSTOM-RAW"),
			frappe._dict(doctype="Purchase Invoice", invoice_number=None),
		]
		before = deepcopy(invoices)
		created = Mock()
		created.name = "CREATED-RAW"
		created.flags = frappe._dict()
		failed = Mock()
		failed.flags = frappe._dict()
		failed.insert.side_effect = ValueError("FAILURE-RAW")
		with (
			patch("frappe.get_doc", side_effect=[created, failed]) as get_doc,
			patch("frappe.generate_hash", side_effect=["FIRST", "SECOND"]),
			patch("frappe.in_test", True),
			patch("frappe.msgprint") as notice,
			patch("frappe.publish_realtime") as publish,
		):
			names = opening.start_import(invoices)
		self.assertEqual(names, ["CREATED-RAW"])
		self.assertEqual(invoices, before)
		self.assertEqual(get_doc.call_args_list, [call(invoices[0]), call(invoices[1])])
		created.insert.assert_called_once_with(set_name="CUSTOM-RAW")
		created.submit.assert_called_once_with()
		failed.insert.assert_called_once_with(set_name=None)
		failed.submit.assert_not_called()
		failed.log_error.assert_called_once_with("Opening invoice creation failed")
		self.db.savepoint.assert_has_calls([call("opening_invoice_FIRST"), call("opening_invoice_SECOND")])
		self.db.rollback.assert_called_once_with(save_point="opening_invoice_SECOND")
		self.db.commit.assert_not_called()
		self.assertEqual(
			notice.call_args.args[0],
			"Si sono verificati 1 errori durante la creazione delle fatture di apertura. Consultare <a href='/app/List/Error Log' class='variant-click'>Registro degli errori</a>.",
		)
		for index, recorded in enumerate(publish.call_args_list):
			event, payload = recorded.args
			self.assertEqual(event, "opening_invoice_creation_progress")
			self.assertEqual(
				(payload["count"], payload["total"], payload["errors"]),
				(index + 1, 2, None if index == 0 else 1),
			)
			self.assertEqual(recorded.kwargs["user"], "translator@example.com")

	def test_complete_required_field_and_link_label_are_extracted(self):
		with Path(opening.__file__).open("rb") as source:
			messages = {
				message
				for _line, message, _comments, _context in extract(
					"frappe.gettext.extractors.python.extract", source, keywords=PYTHON_KEYWORDS
				)
			}
		self.assertIn("Row #{0}: {1} is required to create an opening invoice of type {2}.", messages)
		self.assertIn("Error Log", messages)
