from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.accounts.report.balance_sheet.balance_sheet import get_chart_data


class TestBalanceSheetChart(UnitTestCase):
	def test_assets_label_uses_accounting_context(self):
		translations = {"Assets": "Cespiti", "Assets:Balance Sheet": "Attività"}
		columns = [{"label": "2026", "fieldname": "year_2026"}]
		asset = [{"year_2026": 125}, {}]

		with patch("frappe.translate.get_all_translations", return_value=translations):
			chart = get_chart_data(frappe._dict(accumulated_values=0), columns, asset, [], [], "EUR")

		self.assertEqual(chart["data"]["datasets"], [{"name": "Attività", "values": [125]}])
		self.assertEqual(chart["currency"], "EUR")
