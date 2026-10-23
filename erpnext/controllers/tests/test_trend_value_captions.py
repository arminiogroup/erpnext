from copy import deepcopy
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.buying.report.purchase_order_trends import purchase_order_trends
from erpnext.selling.report.quotation_trends import quotation_trends
from erpnext.selling.report.sales_order_trends import sales_order_trends


class TestTrendValueCaptions(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		self.reports = (
			(
				purchase_order_trends,
				"Purchase Order",
				"Supplier",
				"Net purchase order value ({0})",
				"Valore netto degli ordini di acquisto ({0})",
			),
			(
				sales_order_trends,
				"Sales Order",
				"Customer",
				"Net sales order value ({0})",
				"Valore netto degli ordini di vendita ({0})",
			),
			(
				quotation_trends,
				"Quotation",
				"Customer",
				"Net quotation value ({0})",
				"Valore netto dei preventivi ({0})",
			),
		)
		self.translations = {
			"Monthly": "Mensile",
			"Quarterly": "Trimestrale",
			"Half-Yearly": "Semestrale",
			"Yearly": "Annuale",
			"Total": "Totale",
			"Purchase Value": "Valore degli acquisti",
			"Sales Value": "Valore delle vendite",
			"Quoted Amount": "Importo preventivato",
			**{source: target for _module, _doctype, _party, source, target in self.reports},
		}
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.translations if lang == "it" else {},
			)
		)

	def fixture(self, based_on, period, grouped):
		count = 2 if based_on == "Item" else 3 if based_on in ("Supplier", "Customer") else 1
		prefix = [f"Identity {i}" for i in range(count)] + ["Currency"]
		columns = list(prefix)
		if grouped:
			columns.append("Breakdown:Data:100")
		buckets = 1 if period == "Yearly" else 2
		for index in range(buckets):
			columns.extend((f"P{index + 1} (Qty):Float:120", f"P{index + 1} (Amt):Currency:120"))
		columns.extend(("Total(Qty):Float:120", "Total(Amt):Currency:120"))
		conditions = {"columns": columns, "company_currency": "EUR", "grbc": ["Breakdown:Data:100"]}
		identity = ["RECORD-MEMORY"] + ["Business name Rate"] * (count - 1) + ["EUR"]
		if grouped:
			identity.append("DETAIL-MEMORY")
		first = [*identity, 2, 40, *([3, None] if buckets == 2 else []), 5, 40]
		second = [*identity, 1, -5, *([0, 0] if buckets == 2 else []), 1, -5]
		total = list(first)
		total_label = "Total"
		total[0] = f"'{frappe._(total_label)}'"
		data = [first, second, total]
		if grouped:
			group_total = list(first)
			group_total[columns.index("Breakdown:Data:100")] = ""
			data.insert(0, group_total)
		filters = {"based_on": based_on, "period": period, "group_by": "Item" if grouped else None}
		return data, conditions, filters, [35, *([0] if buckets == 2 else [])]

	def test_whole_captions_preserve_chart_data_across_periods_grouping_and_languages(self):
		self.assertEqual(frappe.session.user, "translator@example.com")
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for module, _doctype, party, source, target in self.reports:
				for period in ("Monthly", "Quarterly", "Half-Yearly", "Yearly"):
					for based_on, grouped in (("Item", False), (party, False), ("Item", True)):
						with self.subTest(
							report=module.__name__,
							lang=lang,
							period=period,
							based_on=based_on,
							grouped=grouped,
						):
							data, conditions, filters, values = self.fixture(based_on, period, grouped)
							before = deepcopy((data, conditions, filters))
							chart = module.get_chart_data(data, conditions, filters)
							dataset = chart["data"]["datasets"][0]
							self.assertEqual(dataset["values"], values)
							self.assertEqual(
								chart["data"]["labels"], [f"P{i + 1}" for i in range(len(values))]
							)
							self.assertEqual(
								{key: value for key, value in chart.items() if key != "data"},
								{
									"type": "line",
									"lineOptions": {"regionFill": 1},
									"fieldtype": "Currency",
									"options": "currency",
									"currency": "EUR",
								},
							)
							self.assertEqual((data, conditions, filters), before)
							period_label = self.translations[period] if lang == "it" else period
							self.assertEqual(
								dataset["name"], (target if lang == "it" else source).format(period_label)
							)

	def test_execute_uses_actual_chart_and_preserves_provider_data_and_empty_contract(self):
		frappe.local.lang = "it"
		for module, doctype, _party, _source, target in self.reports:
			with self.subTest(report=module.__name__):
				data, conditions, filters, values = self.fixture("Item", "Monthly", False)
				before = deepcopy((data, conditions, filters))
				with patch.object(module, "get_columns", return_value=conditions) as columns, patch.object(
					module, "get_data", return_value=data
				) as records:
					result = module.execute(filters)
				columns.assert_called_once_with(filters, doctype)
				records.assert_called_once_with(filters, conditions)
				self.assertIs(result[0], conditions["columns"])
				self.assertIs(result[1], data)
				self.assertIsNone(result[2])
				self.assertEqual(result[3]["data"]["datasets"][0]["values"], values)
				self.assertEqual((data, conditions, filters), before)
				self.assertEqual(result[3]["data"]["datasets"][0]["name"], target.format("Mensile"))
				for data_input, condition_input in (
					([], conditions),
					(None, conditions),
					(data, None),
					(data, {}),
				):
					self.assertEqual(module.get_chart_data(data_input, condition_input, filters), [])
