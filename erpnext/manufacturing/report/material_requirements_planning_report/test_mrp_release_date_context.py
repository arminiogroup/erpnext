# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.tests import UnitTestCase

from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice
from erpnext.buying.doctype.supplier.supplier import Supplier
from erpnext.manufacturing.report.material_requirements_planning_report import (
	material_requirements_planning_report as mrp,
)
from erpnext.selling.doctype.customer import customer as customer_module


class TestMrpReleaseDateContext(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: {
					"Release Date": "Data di sblocco",
					"Release Date:Material Requirements Planning Report": "Data di avvio",
				}
				if lang == "it"
				else {},
			)
		)
		self.enterContext(
			patch.object(frappe, "get_system_settings", return_value="Banker's Rounding (legacy)")
		)

	def test_actual_column_uses_planning_context_without_changing_date_field(self):
		for language, label in (("it", "Data di avvio"), ("en", "Release Date")):
			with self.subTest(language=language):
				frappe.local.lang = language
				report = mrp.MaterialRequirementsPlanningReport(frappe._dict(show_in_bucket_view=0))
				column = next(row for row in report.get_columns() if row["fieldname"] == "release_date")
				self.assertEqual(column["label"], label)
				self.assertEqual(column["fieldtype"], "Date")
				self.assertEqual(column, {"fieldname": "release_date", "label": label, "fieldtype": "Date"})

	def test_existing_noop_marker_registers_the_same_dynamic_option_context(self):
		registry = Path(frappe.get_app_path("erpnext", "gettext", "contextual_messages.py"))
		messages = {
			(message, context)
			for _, message, _, context in extract(
				"frappe.gettext.extractors.python.extract",
				BytesIO(registry.read_bytes()),
				keywords=PYTHON_KEYWORDS,
			)
		}
		self.assertIn(("Release Date", "Material Requirements Planning Report"), messages)

	def test_actual_lead_time_chain_preserves_calendar_math_and_raw_inputs(self):
		for material, quantity, details, expected, days in (
			(
				"Manufacture",
				2,
				dict(manufacturing_time_in_mins=1440, purchase_time=3, buffer_time=1.5),
				"2026-10-17",
				4,
			),
			(
				"Purchase",
				2,
				dict(manufacturing_time_in_mins=1440, purchase_time=3, buffer_time=0.5),
				"2026-10-17",
				4,
			),
			(
				"Purchase",
				2,
				dict(manufacturing_time_in_mins=1440, purchase_time=None, buffer_time=5),
				"2026-10-21",
				0,
			),
			(
				"Manufacture",
				0,
				dict(manufacturing_time_in_mins=1440, purchase_time=3, buffer_time=5),
				"2026-10-21",
				0,
			),
		):
			with self.subTest(material=material, quantity=quantity, purchase_time=details["purchase_time"]):
				reads = []
				item_code = f"ITEM-{material}-{quantity}-{details['purchase_time']}"

				def get_value(doctype, filters, fields, **kwargs):
					reads.append((doctype, filters, fields, kwargs))
					return frappe._dict(details)

				row = frappe._dict(
					item_code=item_code,
					type_of_material=material,
					required_qty=quantity,
					delivery_date="2026-10-21",
				)
				original = deepcopy(row)
				report = mrp.MaterialRequirementsPlanningReport(frappe._dict(show_in_bucket_view=0))
				with patch.object(frappe, "db", SimpleNamespace(get_value=get_value)):
					report.set_lead_time(row)
				self.assertEqual(str(row.release_date), expected)
				self.assertEqual(row.lead_time, days)
				self.assertEqual({key: row[key] for key in original}, original)
				self.assertEqual(
					reads,
					[
						(
							"Item Lead Time",
							{"item_code": item_code},
							["manufacturing_time_in_mins", "purchase_time", "buffer_time"],
							{"as_dict": True},
						)
					],
				)

	def test_global_hold_meaning_and_existing_equality_boundaries_are_unchanged(self):
		translate = frappe._
		self.assertEqual(translate("Release Date"), "Data di sblocco")
		for release, expected in (
			(date(2026, 10, 20), [False, False, False]),
			(date(2026, 10, 21), [False, False, True]),
			(date(2026, 10, 22), [True, True, True]),
			(None, [True, True, True]),
		):
			with self.subTest(release=release):
				invoice = SimpleNamespace(on_hold=1, release_date=release)
				supplier = SimpleNamespace(on_hold=1, hold_type="All", release_date=release)
				with patch(
					"erpnext.accounts.doctype.purchase_invoice.purchase_invoice.nowdate",
					return_value="2026-10-21",
				), patch(
					"erpnext.buying.doctype.supplier.supplier.nowdate", return_value="2026-10-21"
				), patch.object(customer_module, "today", return_value="2026-10-21"), patch.object(
					frappe, "db", SimpleNamespace(get_value=lambda *args, **kwargs: (1, release))
				):
					actual = [
						bool(PurchaseInvoice.invoice_is_blocked(invoice)),
						Supplier.is_blocked_for(supplier, "Invoices"),
						customer_module.is_customer_blocked("CUSTOMER-RAW"),
					]
				self.assertEqual(actual, expected)
