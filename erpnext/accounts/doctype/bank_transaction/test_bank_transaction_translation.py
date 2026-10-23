# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.bank_transaction.bank_transaction import (
	BankTransaction,
	get_clearance_details,
)

DUPLICATE = "{0} {1} is allocated twice in this Bank Transaction"
MISSING_GL = "{0} {1} is not affecting bank account {2}"
INVALID_AMOUNT = "Invalid amount in accounting entries of {0} {1} for Account {2}: {3}"
LABELS = {
	"Sales Invoice": "Fattura di vendita",
	"Purchase Invoice": "Fattura di acquisto",
	"Payment Entry": "Registrazione di pagamento",
	"Journal Entry": "Registrazione contabile",
	"Bank Transaction": "Transazione bancaria",
}
TARGETS = {
	DUPLICATE: "Il documento {0} {1} è allocato due volte in questa transazione bancaria.",
	MISSING_GL: "Il documento {0} {1} non movimenta il conto bancario {2}.",
	INVALID_AMOUNT: "Importo non valido nelle registrazioni contabili del documento {0} {1} per il conto {2}: {3}",
}


class TestBankTransactionTranslation(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.translations = {**LABELS, **TARGETS}
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.translations if lang == "it" else {},
			)
		)
		self.enterContext(patch.object(frappe, "db", SimpleNamespace()))
		self.enterContext(patch("frappe.get_system_settings", return_value="Banker's Rounding (legacy)"))
		self.precision = Mock(return_value=2)
		self.transaction = SimpleNamespace(date="2026-01-02", precision=self.precision)

	def row(self, doctype="Payment Entry", name="Sales Invoice"):
		return frappe._dict(payment_document=doctype, payment_entry=name, allocated_amount=0)

	def assert_duplicate(self, doctype, label):
		rows = [self.row(doctype), self.row(doctype)]
		before = deepcopy(rows)
		with self.assertRaises(frappe.ValidationError) as caught:
			BankTransaction.validate_duplicate_references(SimpleNamespace(payment_entries=rows))
		self.assertEqual(str(caught.exception), TARGETS[DUPLICATE].format(label, "Sales Invoice"))
		self.assertEqual(rows, before)

	def test_duplicate_document_type_is_translated_without_translating_record_name(self):
		for doctype, label in LABELS.items():
			with self.subTest(doctype=doctype):
				self.assert_duplicate(doctype, label)

	def test_unknown_and_none_duplicate_document_types_keep_fallback(self):
		for doctype in ("Custom Voucher", None, "", 0):
			with self.subTest(doctype=doctype):
				self.assert_duplicate(doctype, doctype)

	def test_duplicate_identity_uses_raw_type_and_name(self):
		self.translations["Custom Voucher"] = LABELS["Payment Entry"]
		for rows in (
			[],
			None,
			[self.row(), self.row(name="OTHER-REF")],
			[self.row(), self.row("Custom Voucher")],
			[self.row(), self.row("Sales Invoice")],
		):
			with self.subTest(rows=rows):
				before = deepcopy(rows)
				self.assertIsNone(
					BankTransaction.validate_duplicate_references(SimpleNamespace(payment_entries=rows))
				)
				self.assertEqual(rows, before)

	def test_missing_gl_message_preserves_raw_name_account_and_maps(self):
		for doctype in (*list(LABELS)[:4], "Custom Voucher", None):
			with self.subTest(doctype=doctype):
				gl = {"OTHER-ACCOUNT": 20}
				allocations = {"Journal Entry": {"total": 5}}
				before = deepcopy((gl, allocations))
				with self.assertRaises(frappe.ValidationError) as caught:
					get_clearance_details(
						self.transaction, self.row(doctype), allocations, gl, "Journal Entry"
					)
				self.assertEqual(
					str(caught.exception),
					TARGETS[MISSING_GL].format(
						LABELS.get(doctype, doctype), "Sales Invoice", "Journal Entry"
					),
				)
				self.assertEqual((gl, allocations), before)
		self.precision.assert_not_called()

	def test_invalid_gl_amount_is_translated_after_only_selected_gl_pop(self):
		for doctype in (*list(LABELS)[:4], "Custom Voucher", None):
			for amount in (None, 0, -1):
				with self.subTest(doctype=doctype, amount=amount):
					gl = {"Journal Entry": amount, "OTHER-ACCOUNT": 20}
					allocations = {"Journal Entry": {"total": 5}}
					before_allocations = deepcopy(allocations)
					with self.assertRaises(frappe.ValidationError) as caught:
						get_clearance_details(
							self.transaction, self.row(doctype), allocations, gl, "Journal Entry"
						)
					self.assertEqual(
						str(caught.exception),
						TARGETS[INVALID_AMOUNT].format(
							LABELS.get(doctype, doctype), "Sales Invoice", "Journal Entry", amount or 0
						),
					)
					self.assertEqual(gl, {"OTHER-ACCOUNT": 20})
					self.assertEqual(allocations, before_allocations)
		self.precision.assert_not_called()

	def test_positive_clearance_keeps_amount_precision_dates_and_other_accounts(self):
		cases = (
			(12.5, {}, {}, (12.5, True, date(2026, 1, 2))),
			(12.345, {}, {}, (12.35, True, date(2026, 1, 2))),
			(12.5, {"total": 5, "latest_date": date(2026, 1, 5)}, {}, (7.5, True, date(2026, 1, 5))),
			(12.5, {"total": 12.5, "latest_date": date(2025, 12, 31)}, {}, (0, True, date(2026, 1, 2))),
			(12.5, {"total": 20}, {}, (-7.5, True, date(2026, 1, 2))),
			(12.5, {}, {"OTHER": 3}, (12.5, False, date(2026, 1, 2))),
		)
		for amount, selected_allocation, other_gl, expected in cases:
			with self.subTest(amount=amount, allocation=selected_allocation, other_gl=other_gl):
				gl = {"RAW-GL-ACCOUNT": amount, **other_gl}
				allocations = {"RAW-GL-ACCOUNT": deepcopy(selected_allocation), "UNTOUCHED": {"total": 9}}
				row = self.row()
				before = deepcopy(row)
				self.assertEqual(
					get_clearance_details(self.transaction, row, allocations, gl, "RAW-GL-ACCOUNT"),
					expected,
				)
				self.assertEqual(gl, other_gl)
				self.assertEqual(allocations, {"UNTOUCHED": {"total": 9}})
				self.assertEqual(row, before)
		self.assertEqual(self.precision.call_args_list, [call("unallocated_amount")] * len(cases))

	def test_other_account_allocation_controls_should_clear(self):
		gl = {"RAW-GL-ACCOUNT": 12.5, "OTHER": 3}
		allocations = {"OTHER": {"total": 3}}
		self.assertEqual(
			get_clearance_details(self.transaction, self.row(), allocations, gl, "RAW-GL-ACCOUNT"),
			(12.5, True, date(2026, 1, 2)),
		)
		self.assertEqual(gl, {"OTHER": 3})
		self.assertEqual(allocations, {"OTHER": {"total": 3}})

	def test_linked_bank_transaction_retains_ordered_raw_reads_without_gl_pops(self):
		get_value = Mock(
			side_effect=[frappe._dict(unallocated_amount=-13, bank_account="RAW-BANK"), "RAW-GL"]
		)
		with patch.object(frappe, "db", SimpleNamespace(get_value=get_value)):
			gl, allocations = {"RAW-GL": 99}, {"RAW-GL": {"total": 8}}
			self.assertEqual(
				get_clearance_details(
					self.transaction, self.row("Bank Transaction", "RAW-BT"), allocations, gl, "RAW-GL"
				),
				(13, True, date(2026, 1, 2)),
			)
		self.assertEqual(
			get_value.call_args_list,
			[
				call("Bank Transaction", "RAW-BT", ("unallocated_amount", "bank_account"), as_dict=True),
				call("Bank Account", "RAW-BANK", "account"),
			],
		)
		self.assertEqual((gl, allocations), ({"RAW-GL": 99}, {"RAW-GL": {"total": 8}}))
		self.precision.assert_not_called()

	def test_untranslated_values_keep_original_translator_fallback_identity(self):
		for value in ("Custom Voucher", None, 0, {"doctype": "Payment Entry"}):
			with self.subTest(value=value):
				self.assertIs(frappe._(value), value)

	def test_english_language_keeps_existing_messages(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			BankTransaction.validate_duplicate_references(
				SimpleNamespace(payment_entries=[self.row(), self.row()])
			)
		self.assertEqual(str(caught.exception), DUPLICATE.format("Payment Entry", "Sales Invoice"))
