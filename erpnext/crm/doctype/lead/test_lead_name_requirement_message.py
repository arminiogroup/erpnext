# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from copy import deepcopy
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.crm.doctype.lead.lead import Lead

REQUIREMENT = "A Lead requires a person's name, an organization's name, or an email address"
TARGET = "Un contatto commerciale richiede il nome di una persona, il nome di un'organizzazione o un indirizzo email."


class TestLeadNameRequirementMessage(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: {REQUIREMENT: TARGET} if lang == "it" else {},
			)
		)
		for boundary in ("get_doc", "get_cached_doc", "get_all", "new_doc", "sendmail"):
			self.enterContext(
				patch("frappe." + boundary, side_effect=AssertionError("Unexpected I/O: " + boundary))
			)

	def document(self, **values):
		lead = Lead.__new__(Lead)
		lead.__dict__.update(
			doctype="Lead",
			lead_name=None,
			company_name=None,
			email_id=None,
			first_name=None,
			middle_name=None,
			last_name=None,
			salutation=None,
			flags=frappe._dict(ignore_mandatory=False),
		)
		lead.__dict__.update(values)
		return lead

	def test_empty_naming_requires_all_three_supported_sources_in_whole_message(self):
		for blank in (None, ""):
			with self.subTest(blank=blank):
				lead = self.document(lead_name=blank, company_name=blank, email_id=blank)
				before = deepcopy(lead.__dict__)
				lead.set_full_name()
				with self.assertRaises(frappe.ValidationError) as caught:
					lead.set_lead_name()
				self.assertEqual(str(caught.exception), TARGET)
				self.assertEqual(lead.__dict__, before)

	def test_existing_name_and_person_organization_email_precedence_are_unchanged(self):
		for values, expected in (
			({"email_id": "lead@example.com"}, "lead"),
			({"company_name": "Lead"}, "Lead"),
			({"lead_name": "Contact"}, "Contact"),
			(
				{"first_name": "Lead", "middle_name": "e", "last_name": "Contact", "salutation": "Dr"},
				"Dr Lead e Contact",
			),
			({"company_name": "Lead", "email_id": "contact@example.com"}, "Lead"),
			({"lead_name": "Contact", "company_name": "Lead", "email_id": "lead@example.com"}, "Contact"),
			(
				{
					"first_name": "Contact",
					"lead_name": "Old name",
					"company_name": "Lead",
					"email_id": "lead@example.com",
				},
				"Contact",
			),
		):
			with self.subTest(values=values):
				lead = self.document(**values)
				before = deepcopy(lead.__dict__)
				self.assertIsNone(lead.set_full_name())
				self.assertIsNone(lead.set_lead_name())
				self.assertEqual(lead.lead_name, expected)
				self.assertEqual(
					{key: value for key, value in lead.__dict__.items() if key != "lead_name"},
					{key: value for key, value in before.items() if key != "lead_name"},
				)

	def test_ignore_mandatory_preserves_empty_name_without_none_email_crash(self):
		lead = self.document(flags=frappe._dict(ignore_mandatory=True))
		before = deepcopy(lead.__dict__)
		self.assertIsNone(lead.set_full_name())
		self.assertIsNone(lead.set_lead_name())
		self.assertEqual(lead.__dict__, before)

	def test_english_message_truthfully_includes_email_fallback(self):
		frappe.local.lang = "en"
		with self.assertRaises(frappe.ValidationError) as caught:
			self.document().set_lead_name()
		self.assertEqual(str(caught.exception), REQUIREMENT)
