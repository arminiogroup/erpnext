# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import ast
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase


class TestQualificationTranslationContext(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.root = Path(frappe.get_app_path("erpnext"))
		self.translations = {"Qualification": "Qualificazione"}
		with (self.root / "gettext/contextual_messages.py").open("rb") as stream:
			self.markers = {
				(message, context)
				for _line, message, _comments, context in extract(
					"frappe.gettext.extractors.python.extract", stream, keywords=PYTHON_KEYWORDS
				)
			}
		if ("Qualification", "Employee Education") in self.markers:
			self.translations["Qualification:Employee Education"] = "Titolo di studio"
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.translations if lang == "it" else {},
			)
		)

	def metadata(self, module, name):
		path = self.root / module / "doctype" / name / (name + ".json")
		data = json.loads(path.read_text())
		fields = {row["fieldname"]: frappe._dict(row, parent=data["name"]) for row in data["fields"]}
		meta = Meta.__new__(Meta)
		meta.__dict__.update(name=data["name"], _fields=fields)
		return data, meta, fields

	def test_actual_employee_child_metadata_translates_academic_label_only(self):
		_data, meta, fields = self.metadata("setup", "employee_education")
		before = deepcopy(fields["qualification"])
		self.assertEqual(fields["qualification"].fieldtype, "Data")
		self.assertEqual(meta.get_label("qualification"), "Qualification")
		self.assertEqual(meta.get_translated_label("qualification"), "Titolo di studio")
		self.assertEqual(fields["qualification"], before)
		_data, _meta, parent_fields = self.metadata("setup", "employee")
		self.assertEqual(
			(parent_fields["education"].fieldtype, parent_fields["education"].options),
			("Table", "Employee Education"),
		)
		frappe.local.lang = "en"
		self.assertEqual(meta.get_translated_label("qualification"), "Qualification")

	def test_actual_crm_section_and_translated_sales_stage_keep_commercial_meaning(self):
		_data, meta, fields = self.metadata("crm", "lead")
		self.assertEqual(fields["qualification_tab"].fieldtype, "Section Break")
		self.assertEqual(meta.get_translated_label("qualification_tab"), "Qualificazione")
		stage, _meta, stage_fields = self.metadata("crm", "sales_stage")
		self.assertEqual(stage["translated_doctype"], 1)
		self.assertEqual(stage["autoname"], "field:stage_name")
		self.assertEqual(stage_fields["stage_name"].fieldtype, "Data")
		value = "Qualification"
		self.assertEqual(frappe._(value, context=stage["name"]), "Qualificazione")
		self.assertEqual(value, "Qualification")
		custom = "Custom qualification"
		self.assertEqual(frappe._(custom, context=stage["name"]), custom)

	def test_both_fixture_sources_remain_canonical_noop_markers(self):
		stages = (self.root / "setup/setup_wizard/data/sales_stage.txt").read_text().splitlines()
		self.assertIn("Qualification", stages)
		self.assertNotIn("Qualificazione", stages)
		canonical = stages[stages.index("Qualification")]
		self.assertEqual(frappe.N_(canonical, context="Employee Education"), "Qualification")
		tree = ast.parse((self.root / "setup/setup_wizard/operations/install_fixtures.py").read_text())
		self.assertTrue(
			any(
				isinstance(node, ast.ImportFrom)
				and node.module == "frappe"
				and any(alias.name == "N_" and alias.asname == "_" for alias in node.names)
				for node in tree.body
			)
		)
		function = next(
			node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_sale_stages"
		)
		self.assertTrue(
			any(
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "_"
				and len(node.args) == 1
				and isinstance(node.args[0], ast.Constant)
				and node.args[0].value == "Qualification"
				for node in ast.walk(function)
			)
		)
