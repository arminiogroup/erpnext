# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.core.doctype.docfield.docfield import DocField
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.regional.united_arab_emirates import setup

PARENTS = ("Purchase Invoice", "Purchase Order", "Purchase Receipt")
SOURCE = "Reverse Charge Applicable"
TARGET = "Inversione contabile applicabile"
REVERSE_CHARGE_FIELD = {
	"fieldname": "reverse_charge",
	"label": SOURCE,
	"fieldtype": "Select",
	"insert_after": "recoverable_standard_rated_expenses",
	"print_hide": 1,
	"options": "Y\nN",
	"default": "N",
}


class NoDatabase:
	def __bool__(self):
		return False

	def __getattr__(self, name):
		def blocked(*args, **kwargs):
			raise AssertionError(f"Unexpected database operation: {name}")

		return blocked


class TestUAEReverseChargeTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		self.addCleanup(setattr, frappe.local, "db", getattr(frappe.local, "db", None))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.db = NoDatabase()

	def extracted(self, path):
		methods = get_method_map("erpnext") + get_method_map("frappe")
		relative = str(Path(setup.__file__).relative_to(Path(frappe.get_app_path("erpnext")).parent))
		method = next(method for pattern, method in methods if pathmatch(pattern, relative))
		self.assertEqual(method, "frappe.gettext.extractors.python.extract")
		return list(extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS))

	def available_dictionary(self):
		return {
			message: TARGET
			for _, message, _, context in self.extracted(Path(setup.__file__))
			if message == SOURCE and context is None
		}

	def factory(self, language="en"):
		with (
			patch.object(frappe.local, "lang", language),
			patch("frappe.translate.get_all_translations", return_value={SOURCE: TARGET}) as translator,
			patch.object(setup, "create_custom_fields") as sink,
		):
			setup.make_custom_fields()
			sink.assert_called_once()
			self.assertEqual(sink.call_args.kwargs, {"ignore_validate": True})
			translator.assert_not_called()
			return sink.call_args.args[0]

	def alias_signature(self, payload):
		lists, fields, result = {}, {}, {}
		for parent, rows in payload.items():
			lists.setdefault(id(rows), parent)
			result[parent] = {"list": lists[id(rows)], "fields": []}
			for index, row in enumerate(rows):
				fields.setdefault(id(row), [parent, index])
				result[parent]["fields"].append(fields[id(row)])
		return result

	def prepare_docfield_cache(self):
		controllers = frappe.controllers.setdefault(frappe.local.site, {})
		missing = object()
		previous = controllers.get("DocField", missing)
		controllers["DocField"] = DocField

		def restore_controller():
			if previous is missing:
				controllers.pop("DocField", None)
			else:
				controllers["DocField"] = previous

		self.addCleanup(restore_controller)
		columns = frappe.local.valid_columns
		previous_columns = columns.get("DocField", missing)
		metadata = json.loads(
			Path(frappe.get_app_path("frappe", "core/doctype/docfield/docfield.json")).read_text()
		)
		columns["DocField"] = [
			"name",
			"owner",
			"creation",
			"modified",
			"modified_by",
			"docstatus",
			"idx",
			"parent",
			"parenttype",
			"parentfield",
			*(df["fieldname"] for df in metadata["fields"] if df.get("fieldname")),
		]

		def restore_columns():
			if previous_columns is missing:
				columns.pop("DocField", None)
			else:
				columns["DocField"] = previous_columns

		self.addCleanup(restore_columns)

	def test_configured_extraction_registers_the_legitimate_field_label(self):
		messages = self.extracted(Path(setup.__file__))
		self.assertEqual([(message, context) for _, message, _, context in messages], [(SOURCE, None)])
		self.assertEqual(self.extracted(Path(__file__)), [])

	def test_all_factory_payloads_stay_raw_and_identical_across_languages(self):
		english = self.factory("en")
		italian = self.factory("it")
		self.assertEqual(len(english), 20)
		self.assertEqual(sum(map(len, english.values())), 93)
		self.assertEqual(english, italian)
		self.assertEqual(self.alias_signature(english), self.alias_signature(italian))
		for fields in italian.values():
			for field in fields:
				if "label" in field:
					self.assertIs(type(field["label"]), str)
					self.assertNotEqual(field["label"], TARGET)

	def test_aliases_and_fresh_factory_results_are_preserved(self):
		first, second = self.factory(), self.factory()
		self.assertEqual(first, second)
		self.assertEqual(self.alias_signature(first), self.alias_signature(second))
		for parent in first:
			self.assertIsNot(first[parent], second[parent])
			for old, fresh in zip(first[parent], second[parent], strict=True):
				self.assertIsNot(old, fresh)
		reverse_fields = [
			next(df for df in first[parent] if df["fieldname"] == "reverse_charge") for parent in PARENTS
		]
		self.assertIs(reverse_fields[0], reverse_fields[1])
		self.assertIs(reverse_fields[0], reverse_fields[2])

	def test_canonical_options_defaults_and_purchase_side_ownership_stay_exact(self):
		payload = self.factory()
		owners = []
		for parent, fields in payload.items():
			for field in fields:
				if field["fieldname"] == "reverse_charge":
					owners.append(parent)
					self.assertEqual(field, REVERSE_CHARGE_FIELD)
		self.assertEqual(tuple(owners), PARENTS)
		for parent in PARENTS:
			recovery = next(df for df in payload[parent] if df["fieldname"] == "recoverable_reverse_charge")
			self.assertEqual(recovery["depends_on"], "eval:doc.reverse_charge=='Y'")
			self.assertEqual(recovery["default"], "100.000")

	def test_actual_meta_translates_extracted_label_and_serializes_raw_schema(self):
		self.prepare_docfield_cache()
		payload = self.factory()
		lookup = {
			parent: next(df for df in payload[parent] if df["fieldname"] == "reverse_charge")
			for parent in PARENTS
		}
		reads, wire = [], {}

		class ReadOnlyFields:
			def table_exists(_self, doctype):
				self.assertEqual(doctype, "Custom Field")
				reads.append(("table_exists", doctype))
				return True

			def get_values(_self, doctype, **kwargs):
				parent = kwargs["filters"]["dt"]
				self.assertIn(parent, PARENTS)
				self.assertEqual(doctype, "Custom Field")
				self.assertEqual(
					kwargs,
					{
						"filters": {"dt": parent},
						"fieldname": "*",
						"as_dict": True,
						"order_by": "idx",
						"update": {"is_custom_field": 1},
					},
				)
				reads.append(("get_values", parent))
				return [frappe._dict({**deepcopy(lookup[parent]), "is_custom_field": 1})]

		for language in ("en", "it"):
			wire[language] = {}
			with (
				patch.object(frappe.local, "lang", language),
				patch.object(frappe, "db", ReadOnlyFields()),
				patch("frappe.get_disabled_apps", return_value=[]),
				patch(
					"frappe.translate.get_all_translations",
					return_value=self.available_dictionary() if language == "it" else {},
				),
			):
				for parent in PARENTS:
					meta = Meta.__new__(Meta)
					meta.__dict__.update(doctype="DocType", name=parent, fields=[], flags=frappe._dict())
					meta.add_custom_fields()
					meta._fields = {field.fieldname: field for field in meta.fields}
					field = meta.get_field("reverse_charge")
					self.assertIsInstance(field, DocField)
					self.assertEqual(field.parent, parent)
					self.assertEqual(meta.get_label("reverse_charge"), SOURCE)
					wire[language][parent] = [
						{key: value for key, value in df.items() if key != "__temporary_name"}
						for df in meta.as_dict().fields
					]
					with self.subTest(parent=parent, language=language):
						self.assertEqual(
							meta.get_translated_label("reverse_charge"),
							TARGET if language == "it" else SOURCE,
						)
		self.assertEqual(wire["en"], wire["it"])
		self.assertEqual(
			reads,
			[
				(method, parent)
				for _ in ("en", "it")
				for parent in PARENTS
				for method, parent in (("table_exists", "Custom Field"), ("get_values", parent))
			],
		)
