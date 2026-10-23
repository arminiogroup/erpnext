# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import ast
import inspect
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from babel.messages.extract import extract_from_file
from babel.util import pathmatch
from frappe.gettext.translate import PYTHON_KEYWORDS, get_method_map
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase

from erpnext.controllers import status_updater as producer

FRAMES = ("Status must be one of {0}",)
TARGETS = {
	"Status must be one of {0}": "Lo stato deve essere uno dei seguenti: {0}.",
	"Left:Employee": "Cessato",
	"Left": "Sinistra",
	"Active": "Attivo",
	"Issued:Material Request": "Materiale scaricato",
	"Draft": "Bozza",
	"Submitted": "Confermato",
	"Cancelled": "Annullato",
}

GLOBAL_LEFT_SOURCE = "Left"


class TestLocalizedMessages(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.messages = dict(TARGETS)
		self.metadata = {}
		self.meta_reads = []
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(frappe.local, "conf", frappe._dict(host_name="http://memory.invalid")))
		self.enterContext(
			patch.object(
				frappe.local,
				"system_settings",
				frappe._dict(rounding_method="Banker's Rounding"),
				create=True,
			)
		)
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.messages if lang == "it" else {},
			)
		)
		self.enterContext(patch.object(frappe, "get_meta", side_effect=self._get_meta))
		for name in ("get_all", "get_doc", "get_cached_value", "new_doc", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))
		self.addCleanup(setattr, frappe.local, "db", getattr(frappe.local, "db", None))
		self.db = SimpleNamespace(db_type="mariadb")
		self.enterContext(patch.object(frappe, "db", self.db))
		frappe.local.db = self.db

	def _database(self, **providers):
		self.db.__dict__.clear()
		self.db.__dict__.update(db_type="mariadb", **providers)

	def _meta(self, doctype):
		if doctype not in self.metadata:
			slug = frappe.scrub(doctype)
			path = next(
				p for p in Path(frappe.get_app_path("erpnext")).rglob(slug + ".json") if p.parent.name == slug
			)
			data = json.loads(path.read_text())
			meta = Meta.__new__(Meta)
			meta.__dict__.update(data)
			meta.__dict__.setdefault("istable", 0)
			meta.__dict__.setdefault("is_virtual", 0)
			meta.fields = [
				frappe._dict({**df, "parent": doctype, "parenttype": "DocType", "parentfield": "fields"})
				for df in data["fields"]
			]
			self.metadata[doctype] = meta
		return self.metadata[doctype]

	def _get_meta(self, doctype, *args, **kwargs):
		self.assertEqual((args, kwargs), ((), {}))
		self.meta_reads.append(doctype)
		return self._meta(doctype)

	def _state(self, doc, before, owner=None):
		expected = deepcopy(before)
		if owner and "meta" not in before and "meta" in doc.__dict__:
			self.assertIs(doc.__dict__["meta"], self.metadata[owner])
			self.assertEqual(doc.__dict__["meta"].name, owner)
			expected["meta"] = self.metadata[owner]
		self.assertEqual(doc.__dict__, expected)

	def _keys(self, path):
		relative = str(path.relative_to(Path(frappe.get_app_path("erpnext")).parent))
		method = next(
			m
			for pattern, m in get_method_map("erpnext") + get_method_map("frappe")
			if pathmatch(pattern, relative)
		)
		return {
			(message, context)
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
		}

	def test_configured_production_keys(self):
		keys = self._keys(Path(producer.__file__))
		for frame in FRAMES:
			self.assertIn((frame, None), keys)

	def _validate(self, status, options, doc=None):
		if "doc" in inspect.signature(producer.validate_status).parameters:
			return producer.validate_status(status, options, doc)
		return producer.validate_status(status, options)

	def test_marker_and_exact_three_callers(self):
		root = Path(frappe.get_app_path("erpnext"))
		self.assertIn(("Left", "Employee"), self._keys(root / "gettext/contextual_messages.py"))
		paths = (
			"setup/doctype/employee/employee.py",
			"buying/doctype/supplier_quotation/supplier_quotation.py",
			"stock/doctype/material_request/material_request.py",
		)
		expected = (
			("Active", "Inactive", "Suspended", "Left"),
			("Draft", "Submitted", "Partially Ordered", "Ordered", "Stopped", "Cancelled", "Expired"),
			(
				"Draft",
				"Submitted",
				"Stopped",
				"Cancelled",
				"Pending",
				"Partially Ordered",
				"Ordered",
				"Issued",
				"Transferred",
				"Received",
			),
		)
		for path, options in zip(paths, expected, strict=True):
			calls = [
				n
				for n in ast.walk(ast.parse((root / path).read_text()))
				if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "validate_status"
			]
			self.assertEqual(len(calls), 1)
			self.assertEqual(len(calls[0].args), 3)
			self.assertEqual(tuple(ast.literal_eval(calls[0].args[1])), options)
			self.assertEqual(ast.dump(calls[0].args[2]), "Name(id='self', ctx=Load())")

	def test_owner_display_lists_and_exact_canonical_membership(self):
		for dtype, options in (
			("Employee", ["Active", "Inactive", "Suspended", "Left"]),
			(
				"Supplier Quotation",
				["Draft", "Submitted", "Partially Ordered", "Ordered", "Stopped", "Cancelled", "Expired"],
			),
			(
				"Material Request",
				[
					"Draft",
					"Submitted",
					"Stopped",
					"Cancelled",
					"Pending",
					"Partially Ordered",
					"Ordered",
					"Issued",
					"Transferred",
					"Received",
				],
			),
		):
			with self.subTest(dtype=dtype):
				doc = SimpleNamespace(meta=self._meta(dtype))
				before = list(options)
				for valid in options:
					self.assertIsNone(self._validate(valid, options, doc))
				with self.assertRaises(frappe.ValidationError) as error:
					self._validate("INVALID-RAW", options, doc)
				display = [
					frappe.format_value(v, doc.meta.get_field("status"), translated=True) for v in options
				]
				self.assertEqual(str(error.exception), TARGETS[FRAMES[0]].format(producer.comma_or(display)))
				self.assertEqual(options, before)
				if dtype == "Employee":
					self.assertIn("Cessato", str(error.exception))
					self.assertNotIn("Sinistra", str(error.exception))
				if dtype == "Material Request":
					self.assertIn("Materiale scaricato", str(error.exception))
					with self.assertRaises(frappe.ValidationError):
						self._validate("Partially Received", options, doc)
		self.assertEqual(frappe._(GLOBAL_LEFT_SOURCE), "Sinistra")

	def test_legacy_two_argument_and_no_metadata_on_accepted(self):
		options = ["Left", "Active"]
		self.assertIsNone(producer.validate_status("Left", options))
		self.assertEqual(self.meta_reads, [])
		with self.assertRaises(frappe.ValidationError) as error:
			producer.validate_status("INVALID", options)
		self.assertEqual(
			str(error.exception), TARGETS[FRAMES[0]].format(producer.comma_or(["Sinistra", "Attivo"]))
		)
		self.messages.clear()
		with self.assertRaises(frappe.ValidationError) as error:
			self._validate("INVALID", options, SimpleNamespace(meta=self._meta("Employee")))
		self.assertEqual(str(error.exception), FRAMES[0].format(producer.comma_or(options)))
