# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

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

from erpnext.accounts.doctype.sales_invoice.services import inter_company as producer

FRAME = "{0}: transactions with company {1} are not allowed. Change the company or add it to {2} in the {0} record."
FRAMES = (FRAME,)
TARGETS = {
	FRAME: "{0}: non è consentito effettuare transazioni con l’azienda {1}. Cambiare azienda oppure aggiungerla in {2} nel record {0}.",
	"Customer": "Cliente",
	"Supplier": "Fornitore",
	"Allowed to transact with": "Autorizzato a effettuare transazioni con",
	"COMPANY-RAW": "DO NOT TRANSLATE RECORD",
}


class TestIntercompanyPartyGuidance(UnitTestCase):
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
		return self.db

	def _meta(self, doctype):
		if doctype not in self.metadata:
			slug = frappe.scrub(doctype)
			paths = list(Path(frappe.get_app_path("erpnext")).rglob(slug + ".json"))
			path = next(p for p in paths if p.parent.name == slug)
			data = json.loads(path.read_text())
			meta = Meta.__new__(Meta)
			meta.__dict__.update(data)
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

	def test_configured_production_keys(self):
		path = Path(producer.__file__)
		relative = str(path.relative_to(Path(frappe.get_app_path("erpnext")).parent))
		method = next(
			m
			for pattern, m in get_method_map("erpnext") + get_method_map("frappe")
			if pathmatch(pattern, relative)
		)
		keys = {
			(message, context)
			for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
		}
		for frame in FRAMES:
			self.assertIn((frame, None), keys)

	def test_four_dispatches_and_actual_owner_label(self):
		for dtype in ("Sales Invoice", "Sales Order", "Purchase Invoice", "Purchase Order"):
			partytype = "Customer" if dtype in ("Sales Invoice", "Sales Order") else "Supplier"
			for scenario in ("denied", "allowed", "external", "empty"):
				with self.subTest(doctype=dtype, scenario=scenario):
					events = []
					self.meta_reads.clear()

					def value(dt, filters, field):
						events.append(("party", dt, filters, field))
						return "PARTY-RAW" if scenario != "external" else None

					def rows(dt, **kw):
						events.append(("companies", dt, kw))
						self.assertEqual(
							kw,
							{
								"fields": ["company"],
								"filters": {"parenttype": partytype, "parent": "PARTY-RAW"},
							},
						)
						return [frappe._dict(company="COMPANY-RAW" if scenario == "allowed" else "OTHER-RAW")]

					self._database(get_value=value)
					with patch.object(frappe, "get_all", side_effect=rows):
						if scenario == "denied":
							with self.assertRaises(frappe.ValidationError) as caught:
								producer.validate_inter_company_party(dtype, "PARTY-RAW", "COMPANY-RAW", None)
							self.assertEqual(
								str(caught.exception),
								TARGETS[FRAME].format(
									TARGETS[partytype], "COMPANY-RAW", TARGETS["Allowed to transact with"]
								),
							)
							self.assertEqual(self.meta_reads, [partytype])
						else:
							self.assertIsNone(
								producer.validate_inter_company_party(
									dtype, "" if scenario == "empty" else "PARTY-RAW", "COMPANY-RAW", None
								)
							)
							self.assertEqual(self.meta_reads, [])
					self.assertEqual(
						len(events), 0 if scenario == "empty" else 1 if scenario == "external" else 2
					)

	def test_reference_branch_retains_disabled_eligibility(self):
		for dtype, partytype, opposite in (
			("Sales Invoice", "Customer", "Supplier"),
			("Purchase Order", "Supplier", "Customer"),
		):
			calls = []

			def value(dt, filters, field):
				calls.append((dt, filters, field))
				self.assertEqual(filters, {"represents_company": "REF-COMPANY", "disabled": 0})
				return "PARTY-RAW"

			self._database(get_value=value)
			with patch.object(
				frappe,
				"get_doc",
				return_value=frappe._dict(
					company="REF-COMPANY", supplier="REF-SUPPLIER", customer="REF-CUSTOMER"
				),
			), patch.object(frappe, "get_cached_value", return_value="COMPANY-RAW") as cached:
				producer.validate_inter_company_party(dtype, "PARTY-RAW", "COMPANY-RAW", "REF-RAW")
			self.assertEqual(calls[0][0], partytype)
			self.assertEqual(cached.call_args.args[0], opposite)
			self.assertEqual(self.meta_reads, [])

	def test_custom_owner_context_and_empty_dictionary(self):
		self._meta("Supplier").get_field("companies").label = "Custom companies"
		self.messages["Custom companies:Supplier"] = "Aziende ammesse personalizzate"
		self._database()
		with patch.object(frappe, "get_all", return_value=[]):
			with self.assertRaises(frappe.ValidationError) as caught:
				producer._validate_internal_party_company("Supplier", "PARTY-RAW", "COMPANY-RAW")
		self.assertEqual(
			str(caught.exception),
			TARGETS[FRAME].format("Fornitore", "COMPANY-RAW", "Aziende ammesse personalizzate"),
		)
		self.messages.clear()
		with patch.object(frappe, "get_all", return_value=[]):
			with self.assertRaises(frappe.ValidationError) as caught:
				producer._validate_internal_party_company("Supplier", "PARTY-RAW", "COMPANY-RAW")
		self.assertEqual(str(caught.exception), FRAME.format("Supplier", "COMPANY-RAW", "Custom companies"))
