from pathlib import Path
from unittest.mock import patch

import frappe
from babel.messages.extract import extract
from frappe.core.doctype.docfield.docfield import DocField
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase
from frappe.utils.formatters import format_value

from erpnext.support.doctype.service_level_agreement import service_level_agreement as sla


class TestSLASelectContext(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={
					"Failed": "Non riuscito",
					"Failed:Select Option: agreement_status": "Non rispettato",
				},
			)
		)

	def metadata(self, target):
		meta = Meta.__new__(Meta)
		meta.__dict__.update(name=target, doctype="DocType", fields=[])
		for index, field in enumerate(sla.get_service_level_agreement_fields(target)):
			child = DocField.__new__(DocField)
			child.__dict__.update(field, doctype="DocField", name=f"memory-sla-{index}")
			meta.append("fields", child)
		return meta

	def test_actual_extractor_registers_the_shared_field_meaning(self):
		with Path(__file__).with_name("contextual_messages.py").open("rb") as stream:
			messages = {
				(message, context)
				for _, message, _, context in extract(
					"frappe.gettext.extractors.python.extract", stream, keywords=PYTHON_KEYWORDS
				)
			}
		self.assertIn(("Failed", "Select Option: agreement_status"), messages)

	def test_real_generated_fields_and_meta_parent_insertion_support_arbitrary_targets(self):
		for target in ("Lead", "Task", "Example Support Request"):
			with self.subTest(target=target):
				meta = self.metadata(target)
				field = meta.get_field("agreement_status")
				self.assertEqual(field.parent, target)
				self.assertEqual(field.options, "First Response Due\nResolution Due\nFulfilled\nFailed")
				self.assertIsNone(field.get("context"))
				for translated in (False, True):
					self.assertEqual(format_value("Failed", field, translated=translated), "Non rispettato")

	def test_actual_deadline_classification_and_pending_fulfilled_values_remain_canonical(self):
		meta = self.metadata("Example Support Request")
		field = meta.get_field("agreement_status")
		cases = (
			({}, "Failed"),
			({"first_responded_on": "2026-10-20 10:30:00"}, "Failed"),
			({"first_responded_on": "2026-10-20 09:30:00"}, "Fulfilled"),
			({"response_by": "2026-10-20 12:00:00"}, "First Response Due"),
		)
		with patch.object(sla, "get_current_time", return_value="2026-10-20 11:00:00"):
			for values, expected in cases:
				with self.subTest(values=values):
					doc = frappe._dict({"meta": meta, "response_by": "2026-10-20 10:00:00", **values})
					sla.update_agreement_status(doc, False)
					self.assertEqual(doc.agreement_status, expected)
					self.assertEqual(
						format_value(doc.agreement_status, field),
						"Non rispettato" if expected == "Failed" else expected,
					)
		self.assertEqual(field.default, "First Response Due")
