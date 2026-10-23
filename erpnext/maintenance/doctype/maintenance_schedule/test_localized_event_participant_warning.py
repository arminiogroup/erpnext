# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import ast
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

from erpnext.maintenance.doctype.maintenance_schedule import maintenance_schedule as producer
from erpnext.setup.doctype.sales_person.sales_person import SalesPerson

FRAMES = (
	"The following sales persons will not be added as event participants because their linked employees have no User ID:{0}",
)
TARGETS = {
	"The following sales persons will not be added as event participants because their linked employees have no User ID:{0}": "I seguenti venditori non verranno aggiunti come partecipanti agli eventi perché i dipendenti collegati non hanno un ID utente:{0}",
	"SALES-PERSON-RAW": "DO NOT TRANSLATE BUSINESS NAME",
}


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

	def test_actual_missing_user_warning_valid_and_no_employee_controls(self):
		class Boundary(Exception):
			pass

		for scenario in ("missing-user", "valid-user", "no-employee"):
			with self.subTest(scenario=scenario):
				row = frappe._dict(
					item_code="ITEM-RAW", name="ROW-RAW", serial_no="", sales_person="SALES-PERSON-RAW"
				)
				doc = producer.MaintenanceSchedule.__new__(producer.MaintenanceSchedule)
				doc.__dict__.update(
					doctype="Maintenance Schedule",
					name="SCHEDULE-RAW",
					owner="DIFFERENT-OWNER@example.com",
					customer="CUSTOMER-RAW",
					items=[row],
					schedules=[frappe._dict(item_code="ITEM-RAW", serial_no="")],
				)
				before = deepcopy(doc.__dict__)
				sp = SalesPerson.__new__(SalesPerson)
				sp.__dict__.update(employee=None if scenario == "no-employee" else "EMPLOYEE-RAW")
				events = []
				original = frappe.msgprint

				def value(dt, name, field):
					events.append((dt, name, field))
					return None if scenario == "missing-user" else "employee@example.com"

				def date_boundary(*a, **kw):
					events.append(("before-event", a, kw))
					raise Boundary

				def get_doc(*a, **kw):
					self.assertEqual(a, ("Sales Person", "SALES-PERSON-RAW"))
					return sp

				def notice(*a, **kw):
					if kw.get("raise_exception"):
						return original(*a, **kw)
					events.append(("warning", a, kw))
					raise Boundary

				self._database(get_value=value, get_all=date_boundary)
				with patch.object(frappe, "get_doc", side_effect=get_doc), patch.object(
					frappe, "msgprint", side_effect=notice
				):
					with self.assertRaises(Boundary):
						doc.on_submit()
				warnings = [e for e in events if e[0] == "warning"]
				self.assertEqual(len(warnings), int(scenario == "missing-user"))
				if warnings:
					self.assertEqual(warnings[0][1], (TARGETS[FRAMES[0]].format("<br>SALES-PERSON-RAW"),))
					self.assertNotIn(doc.owner, warnings[0][1][0])
				self._state(doc, before)
