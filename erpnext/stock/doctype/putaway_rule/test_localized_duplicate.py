from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.stock.doctype.putaway_rule.putaway_rule import PutawayRule


class TestLocalizedPutawayDuplicate(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		for name in ("lang", "message_log", "response"):
			self.addCleanup(setattr, frappe.local, name, getattr(frappe.local, name))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.message_log = []
		frappe.local.response = frappe._dict()
		self.enterContext(patch.dict(frappe.flags, {"mute_messages": False}))

	def test_duplicate_rule_error_keeps_message_and_query_with_contextual_title(self):
		self.assertEqual(frappe.session.user, "translator@example.com")
		rule = PutawayRule.__new__(PutawayRule)
		rule.__dict__.update(name="RULE-DEMO", item_code="ITEM-DEMO", warehouse="Rack Demo - EX")
		exists = Mock(return_value="RULE-EXISTING")
		message = "Putaway Rule already exists for Item {0} in Warehouse {1}."
		translations = {
			message: "Esiste già una regola di stoccaggio per l’articolo {0} nel magazzino {1}.",
			"Duplicate": "Duplica",
			"Duplicate:Putaway Rule": "Regola duplicata",
		}
		with (
			patch("frappe.db", SimpleNamespace(exists=exists)),
			patch("frappe.translate.get_all_translations", return_value=translations),
		):
			with self.assertRaises(frappe.ValidationError) as error:
				rule.validate_duplicate_rule()
			self.assertEqual(frappe._("Duplicate"), "Duplica")
		exists.assert_called_once_with(
			"Putaway Rule", {"item_code": "ITEM-DEMO", "warehouse": "Rack Demo - EX"}
		)
		self.assertIs(type(error.exception), frappe.ValidationError)
		self.assertEqual(
			str(error.exception),
			translations[message].format(frappe.bold(rule.item_code), frappe.bold(rule.warehouse)),
		)
		self.assertEqual(frappe.local.message_log[-1].title, "Regola duplicata")
		self.assertEqual(rule.name, "RULE-DEMO")
		self.assertEqual(rule.item_code, "ITEM-DEMO")
		self.assertEqual(rule.warehouse, "Rack Demo - EX")

	def test_missing_and_self_rules_keep_the_existing_non_error_branches(self):
		self.assertEqual(frappe.session.user, "translator@example.com")
		for result in (None, "RULE-DEMO"):
			with self.subTest(existing=result):
				rule = PutawayRule.__new__(PutawayRule)
				rule.__dict__.update(name="RULE-DEMO", item_code="ITEM-DEMO", warehouse="Rack Demo - EX")
				exists = Mock(return_value=result)
				with patch("frappe.db", SimpleNamespace(exists=exists)):
					self.assertIsNone(rule.validate_duplicate_rule())
				exists.assert_called_once_with(
					"Putaway Rule", {"item_code": "ITEM-DEMO", "warehouse": "Rack Demo - EX"}
				)
		self.assertEqual(frappe.local.message_log, [])
