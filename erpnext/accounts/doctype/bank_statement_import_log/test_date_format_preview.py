from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.bank_statement_import_log import bank_statement_import_log as controller


class TestStatementDateFormatPreview(TestCase):
	def setUp(self):
		self.previous_language = frappe.local.lang
		frappe.local.lang = "it"

	def tearDown(self):
		frappe.local.lang = self.previous_language

	def make_document(self, date_format, pdf=False):
		return SimpleNamespace(
			detected_date_format=date_format,
			bank_account="Example Bank",
			start_date="2026-10-20",
			end_date="2026-10-21",
			check_permission=Mock(),
			is_pdf=lambda: pdf,
			get_pdf_tables=lambda: [{"page": 1}],
			get_pdf_final_transactions=lambda: [{"date": "2026-10-20"}],
			get_data=lambda: [["Date", "Amount"], ["20 October 2026", "10"]],
			get_transaction_rows=lambda rows: (rows[1:], 1, 1),
			get_final_transactions=lambda **kwargs: [{"date": "2026-10-20"}],
		)

	def response(self, doc):
		with (
			patch.object(frappe, "get_doc", return_value=doc) as get_doc,
			patch.object(controller, "check_for_conflicts", return_value=[]) as conflicts,
		):
			response = controller.get_statement_details("Example Import")
		get_doc.assert_called_once_with("Bank Statement Import Log", "Example Import")
		conflicts.assert_called_once_with("Example Bank", doc.start_date, doc.end_date)
		doc.check_permission.assert_called_once_with()
		return response

	def test_csv_and_pdf_preserve_response_contract_and_supply_parser_example(self):
		for pdf in (False, True):
			with self.subTest(pdf=pdf):
				doc = self.make_document("%d %B %Y", pdf)
				response = self.response(doc)
				self.assertIs(response["doc"], doc)
				self.assertEqual(response["date_format"], "DD MMMM YYYY")
				self.assertEqual(doc.detected_date_format, "%d %B %Y")
				self.assertEqual(response["date_format_example"], datetime(2026, 10, 20).strftime("%d %B %Y"))
				self.assertEqual(response["final_transactions"], [{"date": "2026-10-20"}])
				self.assertEqual(response["conflicting_transactions"], [])
				if pdf:
					self.assertEqual(response["raw_data"], [])
					self.assertEqual(response["pdf_tables"], [{"page": 1}])
				else:
					self.assertEqual(response["raw_data"], doc.get_data())
					self.assertNotIn("pdf_tables", response)

	def test_examples_roundtrip_through_actual_parser_and_transaction_normalization(self):
		date_formats = (
			"%d/%b/%y",
			"%d/%b/%Y",
			"%d %b %Y",
			"%d %B %Y",
			"%d-%b-%Y",
			"%d-%b-%y",
			"%d-%m-%Y",
			"%m-%d-%Y",
			"%Y-%m-%d",
			"%d-%m-%y",
			"%m-%d-%y",
			"%y-%m-%d",
			"%y-%b-%d",
			"%d/%m/%Y",
			"%m/%d/%Y",
			"%Y/%m/%d",
			"%d/%m/%y",
			"%m/%d/%y",
			"%y/%m/%d",
			"%d.%m.%Y",
			"%m.%d.%Y",
			"%Y.%m.%d",
			"%d.%m.%y",
			"%m.%d.%y",
			"%y.%m.%d",
		)
		time_formats = ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M", "%I:%M:%S.%f %p", "%I:%M:%S %p", "%I:%M %p")
		for date_format in (*date_formats, *(f"%d/%m/%Y {format}" for format in time_formats)):
			for pdf in (False, True):
				with self.subTest(date_format=date_format, pdf=pdf):
					response = self.response(self.make_document(date_format, pdf))
					example = response["date_format_example"]
					self.assertEqual(
						datetime.strptime(example, date_format).date(), datetime(2026, 10, 20).date()
					)
					transactions = controller.compute_final_transactions(
						[{"date": example, "withdrawal": "10", "deposit": "0"}],
						date_format,
						"Separate columns for withdrawal and deposit",
					)
					self.assertEqual(transactions[0]["date"], "2026-10-20")
					self.assertEqual(transactions[0]["withdrawal"], 10)
					self.assertEqual(transactions[0]["deposit"], 0)
