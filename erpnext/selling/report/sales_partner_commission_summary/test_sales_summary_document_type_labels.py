# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils.data import comma_or

from erpnext.selling.report.sales_partner_commission_summary.sales_partner_commission_summary import (
	SALES_TRANSACTION_DOCTYPES,
	SalesPartnerCommissionSummaryReport,
)
from erpnext.selling.report.sales_partner_commission_summary.sales_partner_commission_summary import (
	execute as commission_execute,
)
from erpnext.selling.report.sales_partner_transaction_summary.sales_partner_transaction_summary import (
	SalesPartnerTransactionSummaryReport,
)
from erpnext.selling.report.sales_partner_transaction_summary.sales_partner_transaction_summary import (
	execute as transaction_execute,
)
from erpnext.selling.report.sales_person_wise_transaction_summary.sales_person_wise_transaction_summary import (
	execute as person_execute,
)
from erpnext.selling.report.sales_person_wise_transaction_summary.sales_person_wise_transaction_summary import (
	get_columns,
	validate_filters,
)

PARTNER_FRAME = "DocType can be one of {0}"
PERSON_FRAME = "{0}, {1} or {2} are the only allowed options."
TARGETS = {
	PARTNER_FRAME: "Seleziona uno dei seguenti tipi di documento: {0}.",
	PERSON_FRAME: "{0}, {1} o {2} sono le uniche opzioni consentite.",
	"Sales Order": "Ordine di vendita",
	"Sales Invoice": "Fattura di vendita",
	"Delivery Note": "Documento di trasporto",
	"POS Invoice": "Fattura POS",
	"Order Date": "Data ordine",
	"Posting Date": "Data di registrazione",
	"Please select the document type first.": "Seleziona prima il tipo di documento.",
	"Please select the document type first": "Seleziona prima il tipo di documento",
	"Please select a company.": "Seleziona una società.",
	"From Date cannot be greater than To Date.": "La data iniziale non può essere successiva alla data finale.",
}
RAW_TYPES = ["Sales Order", "Sales Invoice", "Delivery Note", "POS Invoice"]


class TestSalesSummaryDocumentTypeLabels(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TARGETS if lang == "it" else {},
			)
		)
		self.enterContext(patch.object(frappe, "db", SimpleNamespace()))
		for boundary in ("get_all", "get_cached_value", "get_doc", "new_doc", "sendmail", "enqueue"):
			self.enterContext(
				patch("frappe." + boundary, side_effect=AssertionError("Unexpected I/O: " + boundary))
			)

	def test_public_partner_entry_points_translate_all_four_choices(self):
		for execute in (commission_execute, transaction_execute):
			for invalid in ("Purchase Invoice", "Custom Type"):
				with self.subTest(execute=execute.__module__, invalid=invalid):
					filters = {"doctype": invalid, "company": "Sales Invoice"}
					before = deepcopy(filters)
					with self.assertRaises(frappe.ValidationError) as caught:
						execute(filters)
					choices = comma_or([TARGETS[value] for value in RAW_TYPES])
					self.assertEqual(str(caught.exception), TARGETS[PARTNER_FRAME].format(choices))
					self.assertEqual(filters, before)
		self.assertEqual(SALES_TRANSACTION_DOCTYPES, RAW_TYPES)

	def test_partner_missing_company_and_dates_keep_guard_order(self):
		for cls in (SalesPartnerCommissionSummaryReport, SalesPartnerTransactionSummaryReport):
			for filters, expected in (
				({}, "Please select the document type first."),
				({"doctype": "Sales Invoice"}, "Please select a company."),
				(
					{
						"doctype": "Sales Invoice",
						"company": "Sales Order",
						"from_date": "2026-02-01",
						"to_date": "2026-01-01",
					},
					"From Date cannot be greater than To Date.",
				),
			):
				with self.subTest(cls=cls.__name__, filters=filters):
					report = cls(filters)
					before = deepcopy(report.__dict__)
					with self.assertRaises(frappe.ValidationError) as caught:
						report.run()
					self.assertEqual(str(caught.exception), TARGETS[expected])
					self.assertEqual(report.__dict__, before)

	def test_allowed_partner_values_remain_canonical_in_filters_and_link_options(self):
		for cls in (SalesPartnerCommissionSummaryReport, SalesPartnerTransactionSummaryReport):
			for doctype in RAW_TYPES:
				with self.subTest(cls=cls.__name__, doctype=doctype):
					filters = {
						"doctype": doctype,
						"company": "Sales Invoice",
						"from_date": "2026-01-01",
						"to_date": "2026-01-01",
					}
					before = deepcopy(filters)
					report = cls(filters)
					report.validate_filters()
					report.prepare_columns()
					self.assertEqual(filters, before)
					self.assertEqual(
						report.date_field, "transaction_date" if doctype == "Sales Order" else "posting_date"
					)
					self.assertEqual(
						report.columns[0],
						dict(
							label=TARGETS[doctype],
							fieldname="name",
							fieldtype="Link",
							options=doctype,
							width=140,
							hidden=0,
						),
					)

	def test_public_person_invalid_and_missing_guards_precede_queries(self):
		for doctype in ("POS Invoice", "Custom Type"):
			filters = {"doc_type": doctype, "company": "Sales Invoice"}
			before = deepcopy(filters)
			with self.subTest(doctype=doctype), self.assertRaises(frappe.ValidationError) as caught:
				person_execute(filters)
			self.assertEqual(
				str(caught.exception),
				TARGETS[PERSON_FRAME].format(*(TARGETS[value] for value in RAW_TYPES[:3])),
			)
			self.assertEqual(filters, before)
		with self.assertRaises(frappe.ValidationError) as caught:
			person_execute({})
		self.assertEqual(str(caught.exception), TARGETS["Please select the document type first"])

	def test_person_allowed_columns_keep_raw_link_types(self):
		for doctype in RAW_TYPES[:3]:
			filters = {"doc_type": doctype, "company": "Sales Invoice"}
			before = deepcopy(filters)
			validate_filters(filters)
			column = get_columns(filters)[0]
			self.assertEqual(column["label"], TARGETS[doctype])
			self.assertEqual(column["options"], doctype)
			self.assertEqual(column["fieldname"], frappe.scrub(doctype))
			self.assertEqual(filters, before)

	def test_english_fallback_preserves_original_type_list_and_conjunction(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			commission_execute({"doctype": "Custom Type"})
		self.assertEqual(str(caught.exception), PARTNER_FRAME.format(comma_or(RAW_TYPES)))
		with self.assertRaises(frappe.ValidationError) as caught:
			person_execute({"doc_type": "Custom Type"})
		self.assertEqual(str(caught.exception), PERSON_FRAME.format(*RAW_TYPES[:3]))
