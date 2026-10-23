# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import datetime
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import data

from erpnext.manufacturing.page.shop_floor import shop_floor


class TestTodaySessionCaptions(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(setattr, frappe.local, "session", frappe.local.session)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.local.session = frappe._dict(user="translator@example.com", data=frappe._dict())
		frappe.local.lang = "it"
		self.requests = []
		self.rows = [
			frappe._dict(
				name="JC-OLD-CONFIRMED-UPDATED-TODAY",
				docstatus=1,
				status="Completed",
				finished_good="ITEM-EXAMPLE",
				production_item="ITEM-EXAMPLE",
				total_completed_qty=50.5,
				for_quantity=50.5,
				process_loss_qty=0,
				total_time_in_mins=10,
				modified="2026-10-21 00:30:00",
			)
		]
		self.enterContext(patch.object(frappe, "get_list", side_effect=self.read_list))
		self.enterContext(patch.object(frappe, "get_cached_value", return_value=None))
		self.enterContext(patch.object(frappe, "has_permission", return_value=True))
		self.enterContext(patch.object(frappe, "get_system_settings", return_value="Europe/Rome"))
		self.enterContext(
			patch.object(
				frappe.local,
				"db",
				SimpleNamespace(get_value=lambda *args: None, get_single_value=lambda *args: 0),
				create=True,
			)
		)
		for boundary in ("get_doc", "new_doc", "sendmail", "enqueue"):
			self.enterContext(
				patch("frappe." + boundary, side_effect=AssertionError("Unexpected I/O: " + boundary))
			)

	def read_list(self, doctype, **kwargs):
		self.assertEqual(doctype, "Job Card")
		self.assertNotIn("ignore_permissions", kwargs)
		self.requests.append(deepcopy(kwargs))
		return deepcopy(self.rows) if kwargs["fields"] == shop_floor.TODAY_SESSION_FIELDS else []

	def test_site_timezone_defines_last_modified_midnight_without_submission_date(self):
		instant = datetime.datetime(2026, 10, 20, 23, 30, tzinfo=datetime.UTC)

		class Clock(datetime.datetime):
			@classmethod
			def now(cls, tz=None):
				return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

		for zone, day in (("Europe/Rome", 21), ("America/New_York", 20)):
			with self.subTest(zone=zone):
				with (
					patch.object(data.datetime, "datetime", Clock),
					patch.object(frappe, "get_system_settings", return_value=zone),
				):
					filters = shop_floor._today_sessions_filters(None, "WO-EXAMPLE")
					self.assertEqual(data.now_datetime().day, day)
					self.assertEqual(filters["modified"], [">=", datetime.datetime(2026, 10, day)])
					self.assertEqual(set(filters), {"docstatus", "modified", "status", "work_order"})
					self.assertEqual(filters["docstatus"], 1)
					self.assertEqual(filters["status"], ["!=", "To Manufacture"])
		self.assertEqual(self.requests, [])

	def test_work_order_precedence_and_workstation_scope_remain_canonical(self):
		with patch.object(frappe, "get_system_settings", return_value="Europe/Rome"):
			order = shop_floor._today_sessions_filters("WS-IGNORED", "WO-EXAMPLE")
			station = shop_floor._today_sessions_filters("WS-EXAMPLE", None)
		self.assertEqual(order["work_order"], "WO-EXAMPLE")
		self.assertNotIn("workstation", order)
		self.assertEqual(station["workstation"], "WS-EXAMPLE")
		self.assertNotIn("work_order", station)

	def test_actual_get_data_delivers_job_card_documents_and_fractional_quantities(self):
		original = deepcopy(self.rows)
		with patch.object(frappe, "get_system_settings", return_value="Europe/Rome"):
			result = shop_floor.get_data(workstation="WS-IGNORED", work_order="WO-EXAMPLE")
		self.assertEqual(result["mode"], "work_order")
		self.assertEqual(result["job_cards"], [])
		self.assertEqual(result["today_sessions"][0].name, self.rows[0].name)
		self.assertEqual(result["today_sessions"][0].total_completed_qty, 50.5)
		self.assertEqual(result["today_sessions"][0].status, "Completed")
		self.assertEqual(result["today_sessions"][0].status_colour, "green")
		self.assertEqual(self.rows, original)
		self.assertEqual(len(self.requests), 2)
		request = self.requests[-1]
		self.assertEqual(request["fields"], shop_floor.TODAY_SESSION_FIELDS)
		self.assertEqual(request["limit"], 10)
		self.assertEqual(request["order_by"], "modified desc")
		self.assertEqual(request["filters"]["work_order"], "WO-EXAMPLE")
		self.assertNotIn("workstation", request["filters"])
		self.assertEqual(frappe.session.user, "translator@example.com")

	def test_no_selector_returns_no_documents_or_queries(self):
		self.assertEqual(shop_floor.get_today_sessions(None, None), [])
		self.assertEqual(shop_floor.get_data(), {"job_cards": [], "capacity": 1, "mode": None})
		self.assertEqual(self.requests, [])

	def test_denied_job_card_read_returns_before_any_query(self):
		with patch.object(frappe, "has_permission", return_value=False) as permission:
			result = shop_floor.get_data(work_order="WO-EXAMPLE")
		permission.assert_called_once_with("Job Card", "read")
		self.assertEqual(result, {"job_cards": [], "capacity": 1, "mode": None})
		self.assertEqual(self.requests, [])
