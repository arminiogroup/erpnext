# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.core.doctype.docfield.docfield import DocField
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.modules.import_file import delete_old_doc, import_file_by_path
from frappe.printing.doctype.print_format.classic_converter import uses_beta_renderer
from frappe.printing.layout import iter_fields, iter_zones
from frappe.tests import UnitTestCase

import erpnext
from erpnext.gettext import test_standard_print_format_translation as print_helpers

FORMAT_NAMES = {
	"detailed_tax_invoice": "2024-10-02 14:53:28.661507",
	"tax_invoice": "2024-10-02 14:53:13.878610",
}
TRANSLATIONS = {
	"Project": "Progetto",
	"Project:Dashboard": "Progetti",
	"PROJECT-RAW": "Never translate the business identifier",
	"Authored project caption": "<b>Codice & progetto</b>",
}


class ImportBoundaryReached(Exception):
	pass


class TestRegionalProjectPrintCaption(UnitTestCase):
	def setUp(self):
		self.fixture = print_helpers.TestStandardPrintFormatTranslation()
		self.fixture.setUp()
		self.addCleanup(self.fixture.doCleanups)
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TRANSLATIONS if lang == "it" else {},
			)
		)

	def formats(self):
		for slug, old_modified in FORMAT_NAMES.items():
			path = Path(erpnext.__file__).parent / f"regional/print_format/{slug}/{slug}.json"
			yield path, json.loads(path.read_text()), old_modified

	def generator(self, definition):
		generator = self.fixture.generator(definition)
		doc = Document.__new__(Document)
		doc.__dict__.update(generator.doc.__dict__)
		doc.project = "PROJECT-RAW"
		generator.doc = doc
		return generator

	def project_field(self, layout):
		return next(
			field
			for _, zone in iter_zones(layout)
			for field in iter_fields(zone)
			if field.get("fieldname") == "project"
		)

	def normalised_layout(self, layout):
		layout = deepcopy(layout)
		for _, zone in iter_zones(layout):
			for field in iter_fields(zone):
				self.assertIs(field["section"], zone)
				del field["section"]
		return layout

	def document_state(self, doc):
		state = {}
		for key, value in doc.__dict__.items():
			if key == "items":
				state[key] = list(value)
			elif isinstance(value, dict | list):
				state[key] = deepcopy(value)
			else:
				state[key] = value
		return state

	def test_real_converter_and_default_renderers_name_the_project(self):
		for path, definition, _ in self.formats():
			original_bytes = path.read_bytes()
			generator = self.generator(definition)
			meta_field = generator.doc.meta.get_field("project")
			self.assertEqual(
				(meta_field.label, meta_field.fieldtype, meta_field.options), ("Project", "Link", "Project")
			)
			self.assertEqual(meta_field.print_hide, 1)
			self.assertTrue(uses_beta_renderer(generator.print_format))
			layout = generator.get_layout(generator.print_format)
			field = self.project_field(layout)
			self.assertEqual(
				(field["fieldtype"], field["options"], field["renderer"]), ("Link", "Project", "Link")
			)
			state = deepcopy((generator.doc.project, generator.doc.items[0].rate, generator.doc.docstatus))
			self.assertEqual(state[1], 12.5)
			for restricted in (True, False):
				for lang, caption in (("it", "Progetto"), ("en", "Project")):
					with self.subTest(format=definition["name"], restricted=restricted, language=lang):
						html = self.fixture.render(field, generator.doc, restricted, lang)
						self.assertIn(f'<div class="label">{caption}</div>', html)
						self.assertIn("PROJECT-RAW", html)
						self.assertNotIn("Never translate", html)
						self.assertNotIn("Progetti", html)
						self.assertEqual(
							(generator.doc.project, generator.doc.items[0].rate, generator.doc.docstatus),
							state,
						)
			self.assertEqual(path.read_bytes(), original_bytes)
			self.assertEqual(definition["disabled"], 1)

	def test_complete_processed_layout_preserves_every_other_field(self):
		for _, definition, _ in self.formats():
			current = self.generator(definition)
			old_definition = deepcopy(definition)
			old_layout = json.loads(old_definition["format_data"])
			old_layout[7]["label"] = "Reverse Charge Applicable"
			old_definition["format_data"] = json.dumps(old_layout)
			old = self.generator(old_definition)
			states = [
				(
					generator.doc,
					self.document_state(generator.doc),
					[(row, self.document_state(row)) for row in generator.doc.items],
				)
				for generator in (current, old)
			]
			current_layout = current.get_layout(current.print_format)
			self.project_field(current_layout)["label"] = "Reverse Charge Applicable"
			self.assertEqual(
				self.normalised_layout(current_layout),
				self.normalised_layout(old.get_layout(old.print_format)),
			)
			for doc, state, rows in states:
				self.assertEqual(self.document_state(doc), state)
				for row, row_state in rows:
					self.assertEqual(self.document_state(row), row_state)

	def test_blank_value_authored_caption_and_escaping_keep_existing_behavior(self):
		for _, definition, _ in self.formats():
			generator = self.generator(definition)
			field = self.project_field(generator.get_layout(generator.print_format))
			for restricted in (True, False):
				generator.doc.project = ""
				self.assertEqual(self.fixture.render(field, generator.doc, restricted).strip(), "")
				generator.doc.project = "PROJECT-RAW"
				custom = deepcopy(field)
				custom["label"] = "Authored project caption"
				html = self.fixture.render(custom, generator.doc, restricted)
				self.assertIn("&lt;b&gt;Codice &amp; progetto&lt;/b&gt;", html)
				self.assertIn("PROJECT-RAW", html)
				self.assertNotIn("<b>Codice", html)
				custom["label"] = "Untranslated authored caption"
				self.assertIn(
					"Untranslated authored caption", self.fixture.render(custom, generator.doc, restricted)
				)

	def test_actual_permission_filter_still_drops_and_accepts_the_same_field(self):
		for _, definition, _ in self.formats():
			generator = self.generator(definition)
			meta = generator.doc.meta
			meta.get_field("project").permlevel = 1
			for read in (0, 1):
				meta.permissions = [frappe._dict(role="Translator", read=read, permlevel=1)]
				layout = generator.get_layout(generator.print_format)
				fields = [
					field.get("fieldname") for _, zone in iter_zones(layout) for field in iter_fields(zone)
				]
				self.assertEqual("project" in fields, bool(read))
				self.assertEqual(generator.doc.project, "PROJECT-RAW")

	def test_normal_import_timestamp_guard_stops_before_persistence(self):
		for path, definition, old_modified in self.formats():
			for db_modified in (definition["modified"], "2099-01-01 00:00:00"):
				db = SimpleNamespace(get_value=Mock(return_value=db_modified))
				with patch.object(frappe.local, "db", db), patch(
					"frappe.modules.import_file.import_doc"
				) as import_doc:
					self.assertFalse(import_file_by_path(str(path)))
					import_doc.assert_not_called()
					db.get_value.assert_called_once_with("Print Format", definition["name"], "modified")
			with self.subTest(format=definition["name"]):
				db = SimpleNamespace(get_value=Mock(return_value=old_modified))
				with patch.object(frappe.local, "db", db), patch(
					"frappe.modules.import_file.import_doc", side_effect=ImportBoundaryReached
				) as import_doc:
					with self.assertRaises(ImportBoundaryReached):
						import_file_by_path(str(path))
					self.assertEqual(import_doc.call_args.kwargs["docdict"], definition)
					db.get_value.assert_called_once_with("Print Format", definition["name"], "modified")

	def test_actual_reload_retains_existing_disabled_value(self):
		meta_data = json.loads(
			(Path(frappe.__file__).parent / "printing/doctype/print_format/print_format.json").read_text()
		)
		meta = Meta.__new__(Meta)
		meta.__dict__.update(name="Print Format", doctype="DocType", fields=[])
		for field in meta_data["fields"]:
			df = DocField.__new__(DocField)
			df.__dict__.update(field)
			meta.fields.append(df)
		meta._non_computed_table_fields = [
			field for field in meta.fields if field.fieldtype in ("Table", "Table MultiSelect")
		]
		for _, definition, _ in self.formats():
			for disabled in (0, 1):
				old = Document.__new__(Document)
				old.__dict__.update(doctype="Print Format", name=definition["name"], disabled=disabled)
				new = Document.__new__(Document)
				new.__dict__.update(deepcopy(definition), meta=meta, flags=frappe._dict())
				state = dict(new.__dict__, flags=frappe._dict(ignore_children_type=[]), disabled=disabled)
				with patch("frappe.get_doc", return_value=old) as get_doc, patch(
					"frappe.delete_doc"
				) as delete_doc:
					delete_old_doc(new, reset_permissions=False)
					get_doc.assert_called_once_with("Print Format", definition["name"])
					delete_doc.assert_called_once_with(
						"Print Format", definition["name"], force=1, ignore_doctypes=[], for_reload=True
					)
				self.assertIs(new.__dict__["_table_fieldnames"], meta._table_doctypes)
				self.assertEqual(
					meta._table_doctypes, {field.fieldname: field.options for field in meta._table_fields}
				)
				state["_table_fieldnames"] = meta._table_doctypes
				self.assertEqual(new.__dict__, state)
				self.assertEqual(definition["disabled"], 1)

	def test_actual_configured_extractor_tracks_the_project_caption(self):
		methods = get_method_map("erpnext") + get_method_map("frappe")
		for path, _, _ in self.formats():
			filename = str(path.relative_to(Path(erpnext.__file__).parent.parent))
			method = next(method for pattern, method in methods if pathmatch(pattern, filename))
			self.assertEqual(method, "frappe.gettext.extractors.print_format.extract")
			messages = list(extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS))
			with self.subTest(format=filename):
				captions = [message for _, message, _, _ in messages]
				self.assertIn("Project", captions)
				self.assertNotIn("Reverse Charge Applicable", captions)
		self.assertEqual(
			list(
				extract_from_file(
					"frappe.gettext.extractors.python.extract", __file__, keywords=PYTHON_KEYWORDS
				)
			),
			[],
		)
