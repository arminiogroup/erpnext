# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import copy, deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.controllers import item_variant
from erpnext.manufacturing.doctype.manufacturing_settings import manufacturing_settings
from erpnext.setup.doctype.employee import employee

LIMIT_SOURCE = "Select fewer than 600 combinations of attribute values at a time."
LIMIT_ITALIAN = "Seleziona meno di 600 combinazioni di valori degli attributi per volta."


def employee_meta():
	data = json.loads(
		Path(frappe.get_app_path("erpnext", "setup", "doctype", "employee", "employee.json")).read_text()
	)
	meta = Meta.__new__(Meta)
	meta.__dict__.update(
		name=data["name"],
		istable=data.get("istable", 0),
		fields=[frappe._dict(field) for field in data["fields"]],
		_fields={field["fieldname"]: frappe._dict(field) for field in data["fields"]},
	)
	return meta


class TestHelpMessageBoundaries(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		with Path(item_variant.__file__).open("rb") as source:
			keys = {
				message
				for _, message, _, context in extract(
					"frappe.gettext.extractors.python.extract",
					source,
					keywords=PYTHON_KEYWORDS,
				)
				if context is None
			}
		translations = {LIMIT_SOURCE: LIMIT_ITALIAN} if LIMIT_SOURCE in keys else {}
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: translations if lang == "it" else {},
			)
		)
		self.calls = []
		self.permission = self.enterContext(
			patch("frappe.has_permission", side_effect=self.record_permission)
		)
		self.enterContext(patch("frappe.enqueue", side_effect=self.record_queue))
		self.enterContext(
			patch.object(item_variant, "create_multiple_variants", side_effect=self.record_direct)
		)
		self.enterContext(patch.object(item_variant, "get_variant", side_effect=AssertionError("No lookup")))
		self.enterContext(patch.object(employee, "today", return_value="2026-10-21"))

	def record_permission(self, *args, **kwargs):
		self.calls.append(("permission", args, kwargs))
		return True

	def record_queue(self, *args, **kwargs):
		self.calls.append(("queue", args, kwargs))
		return None

	def record_direct(self, *args, **kwargs):
		self.calls.append(("direct", args, kwargs))
		return 7

	def assert_permission_first(self):
		self.assertEqual(self.calls[0], ("permission", ("Item",), {"ptype": "create", "throw": True}))

	def test_limit_message_matches_rejected_combinations(self):
		for args in (
			{"Colour": list(range(600))},
			{"Colour": list(range(601))},
			{"Colour": list(range(20)), "Size": list(range(30))},
		):
			with self.subTest(lengths=[len(values) for values in args.values()]):
				self.calls.clear()
				before = deepcopy(args)
				with self.assertRaises(frappe.ValidationError) as caught:
					item_variant.enqueue_multiple_variant_creation("Canonical Template", args, True)
				self.assertEqual(str(caught.exception), LIMIT_ITALIAN)
				self.assertEqual(args, before)
				self.assert_permission_first()
				self.assertEqual(len(self.calls), 1)

	def test_accepted_boundaries_preserve_dispatch_and_raw_arguments(self):
		for count in (1, 9, 10, 500, 501, 598, 599):
			for encoded in (False, True):
				with self.subTest(count=count, encoded=encoded):
					args = {"Colour": [str(value) for value in range(count)], "Empty": []}
					raw = json.dumps(args) if encoded else args
					before = deepcopy(raw)
					self.calls.clear()
					result = item_variant.enqueue_multiple_variant_creation("Canonical Template", raw, "true")
					self.assert_permission_first()
					if count < 10:
						self.assertEqual(result, 7)
						self.assertEqual(self.calls[1], ("direct", ("Canonical Template", before, True), {}))
					else:
						self.assertEqual(result, "queued")
						self.assertEqual(
							self.calls[1],
							(
								"queue",
								("erpnext.controllers.item_variant.create_multiple_variants",),
								{
									"item": "Canonical Template",
									"args": before,
									"use_template_image": True,
									"now": frappe.in_test,
								},
							),
						)
					self.assertEqual(len(self.calls), 2)
					self.assertEqual(raw, before)

	def test_empty_and_permission_guards_precede_any_creation(self):
		for args in ({}, {"Colour": [], "Size": []}):
			with self.subTest(args=args):
				self.calls.clear()
				with self.assertRaises(frappe.ValidationError) as caught:
					item_variant.enqueue_multiple_variant_creation("Canonical Template", args)
				self.assertEqual(str(caught.exception), "Please select at least one attribute value")
				self.assert_permission_first()
				self.assertEqual(len(self.calls), 1)
		self.calls.clear()
		self.permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			item_variant.enqueue_multiple_variant_creation("Canonical Template", {"Colour": ["Red"]})
		self.assertEqual(self.calls, [])

	def test_count_is_selected_combinations_before_existing_variant_lookup(self):
		for args in (
			{"Colour": ["Red"] * 600},
			{"Colour": ["Red"] * 20, "Size": ["Small"] * 30, "Empty": []},
		):
			with self.subTest(lengths=[len(values) for values in args.values()]):
				self.calls.clear()
				before = deepcopy(args)
				with self.assertRaises(frappe.ValidationError):
					item_variant.enqueue_multiple_variant_creation("Canonical Template", args)
				self.assertEqual(args, before)
				self.assertEqual(len(self.calls), 1)
		self.calls.clear()
		args = {"Colour": list(range(23)), "Size": list(range(26)), "Empty": []}
		self.assertEqual(item_variant.enqueue_multiple_variant_creation("Canonical Template", args), "queued")
		self.assertEqual(self.calls[1][2]["args"], args)

	def test_limit_message_has_complete_english_fallback(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			item_variant.enqueue_multiple_variant_creation("Canonical Template", {"Colour": list(range(600))})
		self.assertEqual(str(caught.exception), LIMIT_SOURCE)

	def make_employee(self, **values):
		doc = employee.Employee.__new__(employee.Employee)
		doc.__dict__.update(
			doctype="Employee",
			name="Memory Employee",
			date_of_birth="1990-05-08",
			date_of_joining="2026-01-01",
			date_of_retirement=None,
			relieving_date=None,
			contract_end_date=None,
			flags=frappe._dict(),
			meta=employee_meta(),
		)
		doc.__dict__.update(values)
		return doc

	def test_birth_date_checks_dates_without_imposing_a_minimum_age(self):
		for birth, joining, error in (
			("2026-10-21", "2026-10-21", None),
			("2025-10-21", "2026-10-21", None),
			("2026-10-22", "2026-10-22", frappe.ValidationError),
			("2026-10-20", "2026-10-19", frappe.exceptions.InvalidDates),
			("1990-05-08", "2020-01-01", None),
			("2020-01-01", None, None),
			(None, "2020-01-01", None),
		):
			with self.subTest(birth=birth, joining=joining):
				doc = self.make_employee(date_of_birth=birth, date_of_joining=joining)
				before = copy(doc.__dict__)
				if error:
					with self.assertRaises(error):
						doc.validate_date()
				else:
					doc.validate_date()
				self.assertEqual(doc.__dict__, before)

	def test_other_employee_date_ordering_and_equality_stay_unchanged(self):
		for fieldname in ("date_of_retirement", "relieving_date", "contract_end_date"):
			for end, rejected in (("2025-12-31", True), ("2026-01-01", False), (None, False)):
				with self.subTest(fieldname=fieldname, end=end):
					doc = self.make_employee(**{fieldname: end})
					before = copy(doc.__dict__)
					if rejected:
						with self.assertRaises(frappe.exceptions.InvalidDates):
							doc.validate_date()
					else:
						doc.validate_date()
					self.assertEqual(doc.__dict__, before)

	def test_material_consumption_setting_is_read_and_cached_without_changing_it(self):
		missing = object()
		original = getattr(frappe.local, "material_consumption", missing)

		def cleanup():
			if original is missing:
				if hasattr(frappe.local, "material_consumption"):
					delattr(frappe.local, "material_consumption")
			else:
				frappe.local.material_consumption = original

		self.addCleanup(cleanup)
		for enabled in (0, 1):
			with self.subTest(enabled=enabled):
				if hasattr(frappe.local, "material_consumption"):
					delattr(frappe.local, "material_consumption")
				reads = []
				with patch.object(
					frappe.local,
					"db",
					frappe._dict(get_single_value=lambda *args: reads.append(args) or enabled),
					create=True,
				):
					self.assertEqual(manufacturing_settings.is_material_consumption_enabled(), enabled)
					self.assertEqual(manufacturing_settings.is_material_consumption_enabled(), enabled)
				self.assertEqual(reads, [("Manufacturing Settings", "material_consumption")])
