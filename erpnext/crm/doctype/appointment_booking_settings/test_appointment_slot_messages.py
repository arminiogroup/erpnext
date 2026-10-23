import datetime
from copy import deepcopy
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.crm.doctype.appointment_booking_settings.appointment_booking_settings import (
	AppointmentBookingSettings,
)


class TestAppointmentSlotMessages(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		for name in ("lang", "message_log", "response"):
			self.addCleanup(setattr, frappe.local, name, getattr(frappe.local, name))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.message_log = []
		frappe.local.response = frappe._dict()
		self.enterContext(patch.dict(frappe.flags, {"mute_messages": False, "print_messages": False}))
		self.source = "<b>From Time</b> cannot be later than <b>To Time</b> for {0}"
		self.target = "Per {0}, l’<b>ora di inizio</b> non può essere successiva all’<b>ora di fine</b>."
		self.days = dict(
			zip(
				("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"),
				("Domenica", "Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato"),
				strict=True,
			)
		)
		self.messages = {self.source: self.target, **self.days}
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.messages if lang == "it" else {},
			)
		)

	def make_settings(self, rows=()):
		self.assertEqual(frappe.session.user, "translator@example.com")
		settings = AppointmentBookingSettings.__new__(AppointmentBookingSettings)
		settings.__dict__.update(
			doctype="Appointment Booking Settings",
			appointment_duration=30,
			availability_of_slots=[frappe._dict(row) for row in rows],
		)
		return settings

	def timestamp(self, value):
		return datetime.datetime.strptime("1970-01-01 " + value, "%Y-%m-%d %H:%M:%S")

	def test_reversed_slot_localizes_weekday_without_changing_canonical_values(self):
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for day, translated in self.days.items():
				with self.subTest(lang=lang, day=day):
					row = frappe._dict(day_of_week=day, from_time="18:00:00", to_time="09:00:00", idx=4)
					before = deepcopy(row)
					settings = self.make_settings()
					with self.assertRaises(frappe.ValidationError) as error:
						settings.validate_from_and_to_time(
							self.timestamp(row.from_time), self.timestamp(row.to_time), row
						)
					self.assertIs(type(error.exception), frappe.ValidationError)
					self.assertEqual(
						str(error.exception),
						(self.target if lang == "it" else self.source).format(
							translated if lang == "it" else day
						),
					)
					self.assertEqual(row, before)

	def test_availability_pipeline_preserves_row_and_reports_translated_day(self):
		rows = [
			{"day_of_week": "Monday", "from_time": "09:00:00", "to_time": "10:00:00", "idx": 1},
			{"day_of_week": "Tuesday", "from_time": "11:00:00", "to_time": "10:00:00", "idx": 2},
		]
		settings = self.make_settings(rows)
		before = deepcopy(settings.availability_of_slots)
		with self.assertRaises(frappe.ValidationError) as error:
			settings.validate_availability_of_slots()
		self.assertEqual(str(error.exception), self.target.format("Martedì"))
		self.assertEqual(settings.availability_of_slots, before)

	def test_equal_and_forward_slots_keep_existing_inclusive_decision(self):
		for to_time in ("09:00:00", "09:30:00", "10:00:00"):
			with self.subTest(to_time=to_time):
				settings = self.make_settings(
					[{"day_of_week": "Monday", "from_time": "09:00:00", "to_time": to_time, "idx": 1}]
				)
				before = deepcopy(settings.availability_of_slots)
				self.assertIsNone(settings.validate_availability_of_slots())
				self.assertEqual(settings.availability_of_slots, before)

	def test_duration_and_overlap_guards_keep_original_decisions(self):
		settings = self.make_settings()
		with self.assertRaises(frappe.ValidationError) as error:
			settings.duration_is_divisible(self.timestamp("09:00:00"), self.timestamp("09:45:00"))
		self.assertEqual(
			str(error.exception),
			"The difference between from time and To Time must be a multiple of Appointment",
		)
		settings.availability_of_slots = [
			frappe._dict(day_of_week="Monday", from_time="09:00:00", to_time="10:00:00", idx=1),
			frappe._dict(day_of_week="Monday", from_time="09:30:00", to_time="10:30:00", idx=2),
		]
		with self.assertRaises(frappe.ValidationError) as error:
			settings.validate_no_overlapping_slots()
		self.assertEqual(str(error.exception), "Availability slots for Lunedì cannot overlap: rows 1 and 2")
