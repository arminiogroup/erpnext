import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import frappe

from erpnext.telephony.doctype.incoming_call_settings.incoming_call_settings import IncomingCallSettings


class TestIncomingCallWeekdayLocalization(unittest.TestCase):
	def setUp(self):
		for key, value in {
			"session": frappe._dict(user="translator@example.com"),
			"lang": "it",
			"response": frappe._dict(),
			"message_log": [],
		}.items():
			previous = getattr(frappe.local, key, None)
			setattr(frappe.local, key, value)
			self.addCleanup(setattr, frappe.local, key, previous)
		self.weekdays = dict(
			zip(
				["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
				["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
				strict=True,
			)
		)
		self.messages = {
			"Please fix overlapping time slots for {0}.": "Correggere le fasce orarie sovrapposte per {0}.",
			**self.weekdays,
		}
		translations = patch("frappe.translate.get_all_translations", return_value=self.messages)
		translations.start()
		self.addCleanup(translations.stop)

	def validate(self, slots):
		schedule = [
			frappe._dict(
				idx=index + 1, day_of_week=day, from_time=start, to_time=end, agent_group="RAW-GROUP"
			)
			for index, (day, start, end) in enumerate(slots)
		]
		before = deepcopy(schedule)
		doc = IncomingCallSettings.__new__(IncomingCallSettings)
		doc.__dict__.update(call_handling_schedule=schedule)
		try:
			doc.validate()
		except frappe.ValidationError as error:
			return str(error)
		finally:
			self.assertEqual(schedule, before)

	def test_each_weekday_is_translated_inside_the_existing_rejection(self):
		for day, target in self.weekdays.items():
			with self.subTest(day=day):
				self.assertEqual(
					self.validate([(day, "09:00:00", "12:00:00"), (day, "11:00:00", "13:00:00")]),
					f"Correggere le fasce orarie sovrapposte per {target}.",
				)

	def test_database_timedeltas_use_the_same_translated_day(self):
		self.assertEqual(
			self.validate(
				[
					("Monday", timedelta(hours=9), timedelta(hours=12)),
					("Monday", timedelta(hours=11), timedelta(hours=13)),
				]
			),
			"Correggere le fasce orarie sovrapposte per Lunedì.",
		)

	def test_custom_empty_target_and_english_fallback(self):
		for day in ["Custom Day", "Monday"]:
			if day == "Monday":
				self.messages[day] = ""
			self.assertEqual(
				self.validate([(day, "09:00:00", "12:00:00"), (day, "11:00:00", "13:00:00")]),
				f"Correggere le fasce orarie sovrapposte per {day}.",
			)
		self.messages.clear()
		self.assertEqual(
			self.validate([("Monday", "09:00:00", "12:00:00"), ("Monday", "11:00:00", "13:00:00")]),
			"Please fix overlapping time slots for Monday.",
		)

	def test_nonoverlapping_same_or_different_days_remain_valid(self):
		for slots in [
			[],
			[("Monday", "09:00:00", "12:00:00"), ("Monday", "12:00:00", "13:00:00")],
			[("Monday", "09:00:00", "10:00:00"), ("Monday", "11:00:00", "12:00:00")],
			[("Monday", "09:00:00", "12:00:00"), ("Tuesday", "09:00:00", "12:00:00")],
		]:
			with self.subTest(slots=slots):
				self.assertIsNone(self.validate(slots))

	def test_duration_guards_and_multiday_grouping_remain_unchanged(self):
		for start, end in [("09:00:00", "09:00:00"), ("10:00:00", "09:00:00")]:
			self.assertEqual(
				self.validate([("Monday", start, end)]),
				"Call Schedule Row 1: To time slot should always be ahead of From time slot. Split overnight slots by day.",
			)
		self.assertEqual(
			self.validate(
				[
					("Tuesday", "09:00:00", "10:00:00"),
					("Monday", "09:00:00", "12:00:00"),
					("Monday", "11:00:00", "13:00:00"),
				]
			),
			"Correggere le fasce orarie sovrapposte per Lunedì.",
		)
