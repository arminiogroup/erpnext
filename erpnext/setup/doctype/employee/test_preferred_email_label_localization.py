import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.model.meta import Meta

from erpnext.setup.doctype.employee.employee import Employee


class TestPreferredEmailLabelLocalization(unittest.TestCase):
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
		self.messages = {
			"Please enter {0}": "Compilare il campo {0}.",
			"Company Email": "Email aziendale",
			"Personal Email": "Email personale",
			"User ID": "ID utente",
		}
		translations = patch("frappe.translate.get_all_translations", return_value=self.messages)
		translations.start()
		self.addCleanup(translations.stop)
		metadata = json.loads(Path(__file__).with_name("employee.json").read_text())
		self.meta = Meta.__new__(Meta)
		self.meta.__dict__.update(metadata)
		self.meta.fields = [frappe._dict(field) for field in metadata["fields"]]

	def validate_preference(self, preference, value=None):
		employee = Employee.__new__(Employee)
		employee.__dict__.update(
			doctype="Employee",
			prefered_contact_email=preference,
			company_email=None,
			personal_email=None,
			user_id=None,
			custom_mailbox=None,
		)
		field = frappe.scrub(preference)
		if field:
			setattr(employee, field, value)
		before = deepcopy(employee.__dict__)
		with patch.object(frappe, "msgprint") as notice:
			employee.set_preferred_email()
			employee.validate_preferred_email()
		self.assertEqual({key: employee.__dict__[key] for key in before}, before)
		self.assertEqual(employee.prefered_email, value if preference else None)
		return notice.call_args_list

	def test_missing_canonical_preference_uses_its_visible_field_caption(self):
		for preference, field in [
			("Company Email", "company_email"),
			("Personal Email", "personal_email"),
			("User ID", "user_id"),
		]:
			with self.subTest(preference=preference):
				calls = self.validate_preference(preference)
				self.assertEqual(len(calls), 1)
				self.assertEqual(
					calls[0].args, (f"Compilare il campo {self.meta.get_translated_label(field)}.",)
				)

	def test_parent_context_precedes_global_caption(self):
		self.messages["Company Email:Employee"] = "Posta aziendale del dipendente"
		calls = self.validate_preference("Company Email")
		self.assertEqual(calls[0].args, ("Compilare il campo Posta aziendale del dipendente.",))

	def test_empty_context_target_falls_back_to_global_then_source(self):
		self.messages["Company Email:Employee"] = ""
		self.assertEqual(
			self.validate_preference("Company Email")[0].args, ("Compilare il campo Email aziendale.",)
		)
		self.messages["Company Email"] = ""
		self.assertEqual(
			self.validate_preference("Company Email")[0].args, ("Compilare il campo Company Email.",)
		)

	def test_existing_raw_email_and_user_values_suppress_warning(self):
		for preference in ["Company Email", "Personal Email", "User ID", "Custom Mailbox", "", None]:
			with self.subTest(preference=preference):
				self.assertEqual(self.validate_preference(preference, "raw.employee@example.com"), [])

	def test_custom_missing_preference_and_empty_preferences_keep_fallback(self):
		self.assertEqual(
			self.validate_preference("Custom Mailbox")[0].args, ("Compilare il campo Custom Mailbox.",)
		)
		for preference in ["", None]:
			self.assertEqual(self.validate_preference(preference), [])
		self.messages.clear()
		self.assertEqual(self.validate_preference("Company Email")[0].args, ("Please enter Company Email",))

	def test_actual_select_options_match_the_canonical_selected_fields(self):
		self.assertEqual(
			self.meta.get_field("prefered_contact_email").options.splitlines(),
			["", "Company Email", "Personal Email", "User ID"],
		)
		for field in ["company_email", "personal_email", "user_id"]:
			self.assertEqual(frappe.scrub(self.meta.get_field(field).label), field)
