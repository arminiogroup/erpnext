import copy
import json
from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.manufacturing.report.job_card_summary import job_card_summary as job_report
from erpnext.support.report.issue_summary.issue_summary import IssueSummary


class TestStatusSummaryContexts(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.statuses = ["Open", "Replied", "On Hold", "Resolved", "Closed"]
		self.issue_labels = [
			"Segnalazioni aperte",
			"Segnalazioni con risposta",
			"Segnalazioni in sospeso",
			"Segnalazioni risolte",
			"Segnalazioni chiuse",
		]
		self.translations = {
			"Open": "Aperto",
			"Completed": "Completato",
			"Open:Job Card Summary": "Schede non completate",
			"Completed:Job Card Summary": "Schede completate",
			"Oct": "Ott",
			"Nov": "Nov",
			**{
				f"{status}:Issue Summary": label
				for status, label in zip(self.statuses, self.issue_labels, strict=True)
			},
		}
		self.enterContext(patch("frappe.translate.get_all_translations", return_value=self.translations))

	def test_issue_counts_keep_columns_chart_summary_and_queries_consistent(self):
		self.assertEqual(frappe.session.user, "translator@example.com")
		for count in (0, 1, 2):
			with self.subTest(count=count):
				entries = [
					frappe._dict(
						name=f"ISSUE-{status}-{number}",
						status=status,
						_assign=json.dumps(["operator@example.com"]),
						opening_date="2026-10-20",
					)
					for status in self.statuses
					for number in range(count)
				]
				before = copy.deepcopy(entries)
				provider = Mock(return_value=entries)
				report = IssueSummary(
					{
						"based_on": "Assigned To",
						"from_date": "2026-10-01",
						"to_date": "2026-10-31",
						"company": "Company Demo",
					}
				)
				with patch("frappe.get_list", provider):
					columns, data, _, chart, summary = report.run()
				self.assertEqual(report.statuses, self.statuses)
				self.assertEqual(
					[col["fieldname"] for col in columns[1:6]],
					[frappe.scrub(status) for status in self.statuses],
				)
				self.assertEqual([entry["value"] for entry in summary], [count] * 5)
				self.assertEqual(
					[entry["indicator"] for entry in summary], ["Red", "Grey", "Grey", "Green", "Green"]
				)
				self.assertEqual([entry["datatype"] for entry in summary], ["Int"] * 5)
				self.assertEqual(chart["data"]["labels"], ["operator@example.com"] if count else [])
				self.assertEqual(
					[series["values"] for series in chart["data"]["datasets"]], [[count] if count else []] * 5
				)
				self.assertEqual(chart["type"], "bar")
				self.assertEqual(chart["barOptions"], {"stacked": True})
				if count:
					self.assertEqual([data[0][frappe.scrub(status)] for status in self.statuses], [count] * 5)
					self.assertEqual(data[0]["total_issues"], count * 5)
				self.assertEqual(provider.call_args.args, ("Issue",))
				self.assertEqual(provider.call_args.kwargs["fields"][0], "_assign")
				self.assertEqual(
					provider.call_args.kwargs["filters"],
					{"opening_date": ("between", ["2026-10-01", "2026-10-31"]), "company": "Company Demo"},
				)
				self.assertEqual(entries, before)
				self.assertEqual(frappe._("Open"), "Aperto")
				self.assertEqual([col["label"] for col in columns[1:6]], self.issue_labels)
				self.assertEqual([series["name"] for series in chart["data"]["datasets"]], self.issue_labels)
				self.assertEqual([entry["label"] for entry in summary], self.issue_labels)

	def test_issue_chart_keeps_thirty_entity_limit_and_unmodified_source_rows(self):
		report = IssueSummary({"based_on": "Customer"})
		report.get_columns()
		report.field_map = {"Customer": "customer"}
		report.data = [
			{"customer": f"Set {number}", **{frappe.scrub(status): number for status in self.statuses}}
			for number in range(35)
		]
		before = copy.deepcopy(report.data)
		report.get_chart_data()
		report.get_report_summary()
		self.assertEqual(report.chart["data"]["labels"], [f"Set {number}" for number in range(30)])
		self.assertEqual(
			[series["values"] for series in report.chart["data"]["datasets"]], [list(range(30))] * 5
		)
		self.assertEqual([entry["value"] for entry in report.report_summary], [sum(range(35))] * 5)
		self.assertEqual(report.data, before)
		self.assertEqual([series["name"] for series in report.chart["data"]["datasets"]], self.issue_labels)

	def test_actual_job_report_keeps_noncompleted_predicate_and_excludes_cancelled(self):
		for count in (0, 1, 2):
			with self.subTest(count=count):
				rows = [
					frappe._dict(
						name=f"JC-{status}-{number}",
						status=status,
						posting_date="2026-10-20",
						docstatus=2 if status == "Cancelled" else 1,
					)
					for status in [
						"Open",
						"Work In Progress",
						"On Hold",
						"Submitted",
						"To Manufacture",
						"Completed",
						"Cancelled",
					]
					for number in range(count)
				]
				before = copy.deepcopy(rows)

				def read_job_cards(doctype, fields, filters):
					self.assertEqual(doctype, "Job Card")
					self.assertEqual(
						filters,
						{"docstatus": ("<", 2), "posting_date": ("between", ["2026-10-01", "2026-11-30"])},
					)
					self.assertIn("status", fields)
					return [row for row in rows if row.docstatus < 2]

				logs = Mock(return_value=[])
				filters = frappe._dict(from_date="2026-10-01", to_date="2026-11-30", range="Weekly")
				with patch("frappe.get_list", side_effect=read_job_cards), patch("frappe.get_all", logs):
					columns, data, _, chart = job_report.execute(filters)
				self.assertEqual(filters.range, "Monthly")
				self.assertEqual(
					[row.status for row in data], [row.status for row in rows if row.docstatus < 2]
				)
				self.assertTrue(all(row.status != "Cancelled" for row in data))
				self.assertEqual(chart["data"]["labels"], ["Ott 2026", "Nov 2026"])
				self.assertEqual(
					[entry["values"] for entry in chart["data"]["datasets"]], [[count * 5, 0], [count, 0]]
				)
				self.assertEqual(chart["type"], "bar")
				self.assertIn("status", [column["fieldname"] for column in columns])
				self.assertEqual(rows, before)
				if count:
					self.assertEqual(logs.call_args.args, ("Job Card Time Log",))
					self.assertEqual(logs.call_args.kwargs["filters"]["docstatus"], ("<", 2))
				else:
					logs.assert_not_called()
				self.assertEqual(
					[entry["name"] for entry in chart["data"]["datasets"]],
					["Schede non completate", "Schede completate"],
				)
