from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import getdate

from erpnext.buying.report.subcontracted_item_to_be_received import (
	subcontracted_item_to_be_received as received,
)
from erpnext.buying.report.subcontracted_raw_materials_to_be_transferred import (
	subcontracted_raw_materials_to_be_transferred as transferred,
)
from erpnext.selling.page.sales_funnel import sales_funnel
from erpnext.setup.doctype.vehicle.vehicle import Vehicle


class TestBoundaryWording(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.dict(frappe.flags, mute_messages=True))
		self.enterContext(patch("frappe.get_system_settings", return_value="UTC"))
		self.enterContext(
			patch("frappe.db", SimpleNamespace(db_type="mariadb", get_all=Mock(return_value=[])))
		)
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={
					"To Date must be greater than From Date": "La data finale deve essere successiva alla data iniziale.",
					"To Date cannot be before From Date": "La data di fine non può precedere la data di inizio.",
					"Insurance Start date should be less than Insurance End date": "La data di inizio deve precedere la data di fine dell’assicurazione.",
					"Insurance Start Date cannot be after Insurance End Date.": "La data di inizio dell’assicurazione non può essere successiva alla data di fine.",
					"Last carbon check date cannot be a future date": "L’ultimo controllo delle emissioni non può avere una data futura.",
					"Please select a Company": "Selezionare un’azienda.",
				},
			)
		)

	def vehicle(self, start, end, carbon=None):
		doc = Vehicle.__new__(Vehicle)
		doc.__dict__.update(start_date=start, end_date=end, carbon_check_date=carbon or getdate())
		return doc

	def test_vehicle_reversed_dates_describe_the_inclusive_boundary(self):
		doc = self.vehicle("2026-01-02", "2026-01-01")
		with self.assertRaises(frappe.ValidationError) as error:
			doc.validate()
		self.assertEqual(
			str(error.exception),
			"La data di inizio dell’assicurazione non può essere successiva alla data di fine.",
		)
		self.assertEqual((doc.start_date, doc.end_date), ("2026-01-02", "2026-01-01"))

	def test_vehicle_equal_and_ordered_dates_are_still_valid(self):
		for start, end in [("2026-01-01", "2026-01-01"), (date(2026, 1, 1), date(2026, 1, 2))]:
			with self.subTest(start=start, end=end):
				self.vehicle(start, end).validate()

	def test_vehicle_carbon_validation_and_insurance_error_precedence_remain(self):
		future = getdate() + timedelta(days=1)
		with self.assertRaises(frappe.ValidationError) as error:
			self.vehicle("2026-01-01", "2026-01-01", future).validate()
		self.assertEqual(
			str(error.exception), "L’ultimo controllo delle emissioni non può avere una data futura."
		)
		with self.assertRaises(frappe.ValidationError) as error:
			self.vehicle("2026-01-02", "2026-01-01", future).validate()
		self.assertEqual(
			str(error.exception),
			"La data di inizio dell’assicurazione non può essere successiva alla data di fine.",
		)

	def test_sales_funnel_reversed_range_explains_equality_is_allowed(self):
		with self.assertRaises(frappe.ValidationError) as error:
			sales_funnel.validate_filters("2026-01-02", "2026-01-01", "COMPANY-RAW")
		self.assertEqual(str(error.exception), "La data di fine non può precedere la data di inizio.")

	def test_sales_funnel_equal_ordered_and_absent_date_controls(self):
		for start, end in [("2026-01-01", "2026-01-01"), ("2026-01-01", "2026-01-02"), (None, None)]:
			with self.subTest(start=start, end=end):
				sales_funnel.validate_filters(start, end, "COMPANY-RAW")
		with self.assertRaises(frappe.ValidationError) as error:
			sales_funnel.validate_filters("2026-01-01", "2026-01-01", None)
		self.assertEqual(str(error.exception), "Selezionare un’azienda.")

	def test_subcontract_report_warnings_keep_the_inclusive_range_and_data_reads(self):
		for module in (received, transferred):
			for start, end in [
				("2026-01-02", "2026-01-01"),
				("2026-01-01", "2026-01-01"),
				("2026-01-01", "2026-01-02"),
			]:
				with self.subTest(module=module.__name__, start=start, end=end):
					filters = frappe._dict(from_date=start, to_date=end, supplier="SUPPLIER-RAW")
					with patch("frappe.msgprint") as warning, patch(
						"frappe.get_all", return_value=[]
					) as reads, patch.object(
						module, "get_allowed_companies_condition", return_value=None
					), patch("frappe.db.get_all", return_value=[]) as db_reads:
						columns, data = module.execute(filters)
						if start > end:
							warning.assert_called_once_with(
								"La data di fine non può precedere la data di inizio."
							)
						else:
							warning.assert_not_called()
						self.assertTrue(reads.called or db_reads.called)
					self.assertEqual(data, [])
					self.assertEqual(columns[0]["options"], "Subcontracting Order")
					self.assertEqual(
						(filters.from_date, filters.to_date, filters.supplier), (start, end, "SUPPLIER-RAW")
					)
