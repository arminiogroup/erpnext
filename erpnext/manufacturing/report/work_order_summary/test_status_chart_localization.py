# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.manufacturing.report.work_order_summary import work_order_summary as report

TRANSLATIONS = {
	"Work Orders": "Ordini di produzione",
	"Draft": "Bozza",
	"Submitted": "Confermato",
	"Not Started": "Non iniziato",
	"In Process": "In lavorazione",
	"Stock Reserved": "Giacenze riservate",
	"Stock Partially Reserved": "Giacenze parzialmente riservate",
	"Completed": "Completato",
	"Stopped": "Fermato",
	"Closed": "Chiuso",
	"Cancelled": "Annullato",
	"Pending": "Da completare",
	"0-30 Days": "0-30 giorni",
	"30-60 Days": "31-60 giorni",
	"60-90 Days": "61-90 giorni",
	"90 Above": "Oltre 90 giorni",
}


class TestStatusChartLocalization(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.translations = dict(TRANSLATIONS)
		self.rows = []
		self.fg_rows = []
		self.alternative_fg = False
		self.events = []
		data = json.loads(
			(
				Path(frappe.get_app_path("erpnext")) / "manufacturing/doctype/work_order/work_order.json"
			).read_text()
		)
		self.fields = {row["fieldname"]: frappe._dict(row, parent=data["name"]) for row in data["fields"]}
		self.meta = Meta.__new__(Meta)
		self.meta.__dict__.update(name=data["name"], _fields=self.fields)
		self.options = self.meta.get_options("status").split("\n")
		self.options.remove("")
		self.enterContext(patch.object(frappe, "get_meta", side_effect=self.get_meta))
		self.enterContext(patch.object(frappe, "get_list", side_effect=self.get_list))
		self.enterContext(patch.object(report, "today", return_value="2026-02-15"))
		self.enterContext(patch.object(frappe, "cache", SimpleNamespace(hget=self.cached_translations)))
		self.enterContext(
			patch.object(
				frappe.local,
				"db",
				SimpleNamespace(db_type="mariadb", get_single_value=self.get_setting, sql=self.sql),
				create=True,
			)
		)
		for boundary in ("get_all", "get_doc", "new_doc", "sendmail", "enqueue"):
			self.enterContext(
				patch("frappe." + boundary, side_effect=AssertionError("Unexpected I/O: " + boundary))
			)

	def cached_translations(self, key, language, **kwargs):
		self.assertEqual(key, "merged_translations")
		return self.translations if language == "it" else {}

	def get_meta(self, doctype):
		self.assertEqual(doctype, "Work Order")
		return self.meta

	def get_setting(self, doctype, fieldname):
		self.assertEqual((doctype, fieldname), ("Manufacturing Settings", "allow_alternative_finished_goods"))
		self.events.append(("setting", doctype, fieldname))
		return self.alternative_fg

	def get_list(self, doctype, **kwargs):
		self.assertEqual(doctype, "Work Order")
		self.assertNotIn("ignore_permissions", kwargs)
		self.events.append(("get_list", doctype, deepcopy(kwargs)))
		return deepcopy(self.rows)

	def sql(self, query, params, **kwargs):
		self.assertTrue(query.startswith("SELECT "), query)
		self.assertIn("`tabStock Entry`", query)
		self.assertIn("`tabStock Entry Detail`", query)
		self.assertEqual(kwargs, {"as_dict": True})
		self.events.append(("fg_query", query, dict(params)))
		return deepcopy(self.fg_rows)

	def row(self, status="Not Started", **kwargs):
		return frappe._dict(
			{
				"name": "Memory WO",
				"status": status,
				"qty": 1000,
				"produced_qty": 999,
				"process_loss_qty": 0,
				"production_item": "Memory FG",
				"creation": "2026-02-01",
				"planned_start_date": "2026-02-01",
				"planned_end_date": "2026-02-20",
				"actual_start_date": None,
				"actual_end_date": None,
				**kwargs,
			}
		)

	def filters(self, **kwargs):
		return frappe._dict(
			company="Memory Company",
			from_date="2026-02-01",
			to_date="2026-02-28",
			charts_based_on="Status",
			**kwargs,
		)

	def assert_status_chart(self, data, chart, options=None):
		options = options if options is not None else self.options
		self.assertEqual(chart["data"]["labels"], [frappe._(status) for status in options])
		self.assertEqual(
			chart["data"]["datasets"],
			[
				{
					"name": "Ordini di produzione" if frappe.local.lang == "it" else "Work Orders",
					"values": [sum(row.status == status for row in data) for status in options],
				}
			],
		)
		self.assertEqual((chart["type"], chart["height"]), ("donut", 300))

	def test_all_actual_metadata_statuses_count_rows_before_translating(self):
		data = [
			self.row(status, name=f"Memory {status} {i}")
			for n, status in enumerate(self.options, 1)
			for i in range(n)
		]
		before = deepcopy(data)
		meta_before = deepcopy(self.fields)
		for language in ("en", "it"):
			with self.subTest(language=language):
				frappe.local.lang = language
				chart = report.get_chart_data(data, self.filters())
				self.assert_status_chart(data, chart)
				self.assertEqual(data, before)
				self.assertEqual(self.fields, meta_before)

	def test_custom_partial_and_colliding_translations_preserve_raw_option_order(self):
		options = ["Draft", "Completed", "Closed", "Memory Custom", "Memory Untranslated"]
		self.fields["status"].options = "\n" + "\n".join(options)
		self.translations.update(
			{"Completed": "Terminato", "Closed": "Terminato", "Memory Custom": "Stato personalizzato"}
		)
		data = [self.row(status) for n, status in enumerate(options, 1) for _ in range(n)]
		data += [self.row("Memory unknown input")]
		before = deepcopy(data)
		chart = report.get_chart_data(data, self.filters())
		self.assert_status_chart(data, chart, options)
		self.assertEqual(
			chart["data"]["labels"],
			["Bozza", "Terminato", "Terminato", "Stato personalizzato", "Memory Untranslated"],
		)
		self.assertEqual(data, before)
		self.assertTrue(self.meta.get_options("status").startswith("\nDraft\n"))

	def test_execute_preserves_raw_query_filters_rows_and_read_order(self):
		self.rows = [
			self.row("Not Started"),
			self.row("Completed"),
			self.row("Completed"),
			self.row("Closed"),
		]
		original = deepcopy(self.rows)
		for language in ("en", "it"):
			for mode in ("Creation Date", "Planned Date", "Actual Date"):
				with self.subTest(language=language, mode=mode):
					self.events.clear()
					frappe.local.lang = language
					filters = self.filters(
						based_on=mode, sales_order=["Memory SO"], production_item=["Memory FG"]
					)
					before = deepcopy(filters)
					columns, data, message, chart = report.execute(filters)
					self.assert_status_chart(data, chart)
					self.assertIsNone(message)
					self.assertEqual(filters, frappe._dict(before, age=0))
					self.assertEqual([event[0] for event in self.events], ["setting", "get_list"])
					query = self.events[1][2]
					expected = {
						"docstatus": ("<", 2),
						"sales_order": ("in", ["Memory SO"]),
						"production_item": ("in", ["Memory FG"]),
						"company": "Memory Company",
					}
					if mode == "Creation Date":
						expected["creation"] = ("between", ["2026-02-01", "2026-02-28"])
					else:
						prefix = "planned" if mode == "Planned Date" else "actual"
						expected[prefix + "_start_date"] = (">=", "2026-02-01")
						expected[prefix + "_end_date"] = ("<", "2026-03-01")
					self.assertEqual(query["filters"], expected)
					self.assertEqual(query["order_by"], "planned_start_date asc")
					self.assertIn("process_loss_qty", query["fields"])
					self.assertIn("status", query["fields"])
					self.assertEqual([row.status for row in data], [row.status for row in original])
					self.assertEqual([row.age for row in data], [14, 0, 0, 14])
					for row, source in zip(data, original, strict=True):
						self.assertEqual({k: v for k, v in row.items() if k != "age"}, source)
					self.assertIn("status", [column["fieldname"] for column in columns])
					self.assertEqual(self.rows, original)

	def test_empty_and_identity_translation_controls(self):
		for language in ("en", "it"):
			frappe.local.lang = language
			self.assert_status_chart([], report.get_chart_data([], self.filters()))
			columns, data, message, chart = report.execute(self.filters())
			self.assertEqual(data, [])
			self.assert_status_chart(data, chart)
			self.assertIsNone(message)
			self.assertTrue(columns)
		self.fields["status"].options = "\nMemory One\nMemory Two"
		data = [self.row("Memory One"), self.row("Memory Two"), self.row("Other")]
		self.assert_status_chart(
			data, report.get_chart_data(data, self.filters()), ["Memory One", "Memory Two"]
		)

	def test_actual_age_and_quantity_branches_remain_independent(self):
		self.rows = [
			self.row("Completed", qty=10, produced_qty=10),
			self.row("Not Started", qty=10, produced_qty=2, process_loss_qty=1),
			self.row("Closed", qty=10, produced_qty=3, planned_start_date="2025-12-30"),
			self.row("Stopped", qty=10, produced_qty=4, planned_start_date="2025-11-30"),
			self.row("In Process", qty=10, produced_qty=1, planned_start_date="2025-10-30"),
			self.row("Not Started", qty=10, produced_qty=0, planned_start_date="2026-03-01"),
		]
		for language in ("en", "it"):
			with self.subTest(language=language):
				frappe.local.lang = language
				filters = self.filters()
				filters.charts_based_on = "Age"
				_columns, data, _message, chart = report.execute(filters)
				self.assertEqual(chart["data"]["datasets"][0]["values"], [2, 1, 1, 1])
				self.assertEqual(
					chart["data"]["datasets"][0]["name"],
					"Ordini di produzione" if language == "it" else "Work Orders",
				)
				self.assertEqual([row.age for row in data], [0, 14, 47, 77, 108, 0])
				filters = self.filters()
				filters.charts_based_on = "Quantity"
				_columns, data, _message, chart = report.execute(filters)
				self.assertEqual(filters.range, "Monthly")
				pending, completed = (frappe._(status) for status in ("Pending", "Completed"))
				self.assertEqual(
					chart["data"]["datasets"],
					[
						{"name": pending, "values": [7.0]},
						{"name": completed, "values": [12.0]},
					],
				)

	def test_actual_finished_goods_query_and_enrichment_are_preserved(self):
		self.alternative_fg = True
		self.rows = [self.row("In Process", qty=10, produced_qty=8)]
		self.fg_rows = [
			frappe._dict(work_order="Memory WO", item_code="Memory FG", is_finished_item=0, transfer_qty=2),
			frappe._dict(
				work_order="Memory WO", item_code="Memory Alternative", is_finished_item=1, transfer_qty=2
			),
		]
		for language in ("en", "it"):
			with self.subTest(language=language):
				frappe.local.lang = language
				self.events.clear()
				columns, data, _message, chart = report.execute(self.filters())
				self.assert_status_chart(data, chart)
				self.assertEqual([e[0] for e in self.events], ["setting", "get_list", "fg_query"])
				self.assertEqual(list(self.events[2][2].values()), ["Memory WO"])
				self.assertEqual(data[0].actual_finished_goods, "Memory FG: 6.0, Memory Alternative: 2.0")
				self.assertIn("actual_finished_goods", [column["fieldname"] for column in columns])
				self.assertNotIn("actual_finished_goods", self.rows[0])

	def test_finished_goods_no_output_and_no_conversion_controls(self):
		frappe.local.lang = "en"
		self.alternative_fg = True
		self.rows = [self.row(produced_qty=0)]
		columns, data, _message, chart = report.execute(self.filters())
		self.assert_status_chart(data, chart)
		self.assertEqual([e[0] for e in self.events], ["setting", "get_list"])
		self.assertNotIn("actual_finished_goods", data[0])
		self.assertIn("actual_finished_goods", [column["fieldname"] for column in columns])
		self.rows[0].produced_qty = 5
		self.events.clear()
		_columns, data, _message, chart = report.execute(self.filters())
		self.assert_status_chart(data, chart)
		self.assertEqual([e[0] for e in self.events], ["setting", "get_list", "fg_query"])
		self.assertNotIn("actual_finished_goods", data[0])
