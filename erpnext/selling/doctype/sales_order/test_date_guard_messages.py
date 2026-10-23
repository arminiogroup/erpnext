# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import datetime
import hashlib
import itertools
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.tax_withholding_category import tax_withholding_category
from erpnext.selling.doctype.sales_order import sales_order

DELIVERY = "Expected Delivery Date cannot be before Sales Order Date."
PERIOD = "Row #{0}: From Date must be before To Date."
TARGETS = {
	DELIVERY: "La data di consegna prevista non può precedere la data dell’ordine di vendita.",
	PERIOD: "Riga n. {0}: la data di inizio deve precedere la data di fine.",
	"Invalid Delivery Date": "Data di consegna non valida",
	"Please enter Delivery Date": "Inserire la data di consegna",
	"Row #{0}: Dates overlapping with other row in group {1}": "Riga n. {0}: le date si sovrappongono a quelle di un’altra riga del gruppo {1}",
	"No Tax Withholding data found for the current posting date.": "Nessun dato sulle ritenute trovato per la data di registrazione corrente.",
}


def extracted_keys():
	keys = set()
	root = Path(frappe.get_app_path("erpnext")).parent
	for module in (sales_order, tax_withholding_category):
		path = Path(module.__file__)
		relative = str(path.relative_to(root))
		method = next(
			method
			for pattern, method in get_method_map("erpnext") + get_method_map("frappe")
			if pathmatch(pattern, relative)
		)
		keys.update(
			message
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
			if isinstance(message, str) and not context
		)
	return keys


def order(delivery_date="2026-10-20", dates=("2026-10-20",), **values):
	doc = sales_order.SalesOrder.__new__(sales_order.SalesOrder)
	doc.__dict__.update(
		doctype="Sales Order",
		name="SO-RAW",
		flags=frappe._dict(),
		order_type="Sales",
		transaction_date="2026-10-20",
		delivery_date=delivery_date,
		skip_delivery_note=0,
		items=[
			frappe._dict(
				doctype="Sales Order Item",
				name=f"ROW-{idx}",
				idx=idx,
				delivery_date=date,
				skip_delivery=0,
				prevdoc_docname=None,
				qty=12.5,
				item_code=f"ITEM-{idx}",
			)
			for idx, date in enumerate(dates, 1)
		],
	)
	doc.__dict__.update(values)
	return doc


def category(rows):
	doc = tax_withholding_category.TaxWithholdingCategory.__new__(
		tax_withholding_category.TaxWithholdingCategory
	)
	doc.__dict__.update(
		doctype="Tax Withholding Category",
		name="Category <raw>",
		flags=frappe._dict(),
		rates=[
			frappe._dict(
				doctype="Tax Withholding Rate",
				name=f"RATE-{idx}",
				idx=idx,
				from_date=start,
				to_date=end,
				tax_withholding_group=group,
				tax_withholding_rate=12.5,
			)
			for idx, (start, end, group) in enumerate(rows, 1)
		],
	)
	return doc


class TestDateGuardMessages(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		self.addCleanup(setattr, frappe.local, "message_log", getattr(frappe.local, "message_log", None))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.message_log = []
		keys = extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: {key: value for key, value in TARGETS.items() if key in keys}
				if lang == "it"
				else {},
			)
		)
		self.enterContext(
			patch("frappe.utils.data.now_datetime", return_value=datetime.datetime(2026, 10, 20, 12))
		)
		self.queries = []
		self.valid_quotations = []
		self.enterContext(patch("frappe.get_all", side_effect=self.get_all))

	def get_all(self, doctype, **kwargs):
		self.assertEqual(doctype, "Quotation")
		self.queries.append((doctype, deepcopy(kwargs)))
		return self.valid_quotations

	def test_delivery_rejection_is_a_complete_truthful_italian_message(self):
		doc = order(dates=("2026-10-19",))
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.validate_delivery_date()
		self.assertEqual(str(caught.exception), TARGETS[DELIVERY])
		self.assertEqual(frappe.local.message_log[-1].title, TARGETS["Invalid Delivery Date"])
		self.assertEqual(frappe.local.message_log[-1].indicator, "orange")
		self.assertEqual(doc.delivery_date, "2026-10-19")
		self.assertEqual(doc.items[0].delivery_date, "2026-10-19")
		self.assertEqual(self.queries, [])

	def test_delivery_english_fallback_and_equal_date_acceptance(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			order(dates=("2026-10-19",)).validate_delivery_date()
		self.assertEqual(str(caught.exception), DELIVERY)
		for value in ("2026-10-20", "2026-10-21", datetime.date(2026, 10, 20)):
			with self.subTest(value=value):
				doc = order(delivery_date=value, dates=(value,))
				before = deepcopy(doc.__dict__)
				doc.validate_delivery_date()
				self.assertEqual(doc.__dict__, before)

	def test_delivery_header_maximum_and_blank_child_defaulting(self):
		doc = order(delivery_date=None, dates=(None, "2026-10-21", "2026-10-23"))
		doc.validate_delivery_date()
		self.assertEqual(doc.delivery_date, "2026-10-23")
		self.assertEqual([row.delivery_date for row in doc.items], ["2026-10-23", "2026-10-21", "2026-10-23"])
		self.assertEqual([row.qty for row in doc.items], [12.5] * 3)
		failed = order(delivery_date="2026-10-18", dates=(None, "2026-10-19", None))
		with self.assertRaises(frappe.ValidationError):
			failed.validate_delivery_date()
		self.assertEqual(failed.delivery_date, "2026-10-19")
		self.assertEqual([row.delivery_date for row in failed.items], ["2026-10-19", "2026-10-19", None])

	def test_missing_date_and_absent_transaction_date_keep_existing_behavior(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			order(delivery_date=None, dates=(None,)).validate_delivery_date()
		self.assertEqual(str(caught.exception), TARGETS["Please enter Delivery Date"])
		doc = order(transaction_date=None)
		doc.validate_delivery_date()
		self.assertIsNone(doc.transaction_date)
		with self.assertRaises(frappe.ValidationError) as caught:
			order(transaction_date=None, dates=("2026-10-19",)).validate_delivery_date()
		self.assertEqual(str(caught.exception), TARGETS[DELIVERY])

	def test_actual_skip_and_maintenance_paths_still_validate_quotations(self):
		for mode in ("all_rows", "legacy", "maintenance"):
			with self.subTest(mode=mode):
				doc = order(delivery_date=None, dates=(None,))
				if mode == "all_rows":
					doc.items[0].skip_delivery = "1"
				elif mode == "legacy":
					doc.skip_delivery_note = "1"
				else:
					doc.order_type = "Maintenance"
				doc.items[0].prevdoc_docname = "QUOTE <raw>"
				self.valid_quotations = ["QUOTE <raw>"]
				before = deepcopy(doc.__dict__)
				doc.validate_delivery_date()
				self.assertEqual(doc.__dict__, before)
				self.assertEqual(
					self.queries[-1],
					(
						"Quotation",
						{
							"filters": {"name": ["in", ["QUOTE <raw>"]], "order_type": doc.order_type},
							"pluck": "name",
						},
					),
				)
		mixed = order(delivery_date=None, dates=(None, None))
		mixed.items[0].skip_delivery = 1
		with self.assertRaises(frappe.ValidationError):
			mixed.validate_delivery_date()

	def test_tax_period_equal_or_reversed_endpoints_have_truthful_italian_message(self):
		for end in ("2026-10-19", "2026-10-20"):
			with self.subTest(end=end):
				doc = category([("2026-10-20", end, "Default")])
				before = deepcopy(doc.__dict__)
				with self.assertRaises(frappe.ValidationError) as caught:
					doc.validate_dates()
				self.assertEqual(str(caught.exception), TARGETS[PERIOD].format(1))
				self.assertEqual(doc.__dict__, before)

	def test_tax_period_english_fallback_and_valid_strict_interval(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			category([("2026-10-20", "2026-10-20", None)]).validate_dates()
		self.assertEqual(str(caught.exception), PERIOD.format(1))
		doc = category([("2026-10-20", "2026-10-21", "A <raw>")])
		before = deepcopy(doc.__dict__)
		doc.validate_dates()
		self.assertEqual(doc.__dict__, before)

	def test_group_sorting_overlap_and_default_literal_remain_unchanged(self):
		for groups, rejected in (
			((None, ""), True),
			(("A", "A"), True),
			((None, "Default"), False),
			(("A", "B"), False),
		):
			with self.subTest(groups=groups):
				doc = category(
					[("2026-10-21", "2026-10-24", groups[0]), ("2026-10-20", "2026-10-22", groups[1])]
				)
				before = deepcopy(doc.__dict__)
				if rejected:
					with self.assertRaises(frappe.ValidationError) as caught:
						doc.validate_dates()
					self.assertEqual(
						str(caught.exception),
						TARGETS["Row #{0}: Dates overlapping with other row in group {1}"].format(
							1, groups[0] or "Default"
						),
					)
				else:
					doc.validate_dates()
				self.assertEqual(doc.__dict__, before)

	def test_touching_periods_and_applicable_endpoints_remain_inclusive(self):
		doc = category([("2026-10-21", "2026-10-23", None), ("2026-10-19", "2026-10-21", "")])
		before = deepcopy(doc.__dict__)
		doc.validate_dates()
		for date, row in (("2026-10-19", 1), ("2026-10-21", 0), ("2026-10-23", 0)):
			self.assertIs(doc.get_applicable_tax_row(date, ""), doc.rates[row])
		with self.assertRaises(frappe.ValidationError):
			doc.get_applicable_tax_row("2026-10-24", None)
		self.assertEqual(doc.__dict__, before)

	def test_full_date_decision_and_post_state_matrix(self):
		records = []
		values = (None, "", "2026-10-19", "2026-10-20", "2026-10-21")
		frappe.local.lang = "en"
		for header, first, second, mode in itertools.product(
			values, values, values, ("normal", "skip", "maintenance")
		):
			doc = order(delivery_date=header, dates=(first, second))
			if mode == "skip":
				doc.skip_delivery_note = 1
			elif mode == "maintenance":
				doc.order_type = "Maintenance"
			records.append(
				self.outcome("sales", [header, first, second, mode], doc, doc.validate_delivery_date)
			)
		for start, end, group in itertools.product(values, values, (None, "", "Default", "A <raw>")):
			doc = category([(start, end, group)])
			records.append(self.outcome("period", [start, end, group], doc, doc.validate_dates))
		for left, right in itertools.product((None, "", "Default", "A"), repeat=2):
			for second_start in ("2026-10-20", "2026-10-21", "2026-10-22"):
				doc = category([("2026-10-19", "2026-10-21", left), (second_start, "2026-10-23", right)])
				records.append(self.outcome("overlap", [left, right, second_start], doc, doc.validate_dates))
		serialised = json.dumps(records, sort_keys=True, ensure_ascii=False, default=str)
		self.parity_evidence = {"cases": records, "sha256": hashlib.sha256(serialised.encode()).hexdigest()}

	def outcome(self, kind, inputs, doc, method):
		frappe.local.message_log = []
		before = deepcopy(doc.__dict__)
		exception = None
		try:
			method()
		except frappe.ValidationError as error:
			exception = type(error).__name__
		messages = [
			{key: value for key, value in entry.items() if key != "message" and key != "__frappe_exc_id"}
			for entry in frappe.local.message_log
		]
		return {
			"kind": kind,
			"inputs": inputs,
			"exception": exception,
			"before": before,
			"after": deepcopy(doc.__dict__),
			"message_properties": messages,
			"queries": deepcopy(self.queries),
		}
