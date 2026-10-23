from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.stock.doctype.item.item import Item


class TestItemValidationMessages(UnitTestCase):
	def test_default_bom_error_identifies_the_mismatched_bom_and_item(self):
		doc = frappe._dict(name="ITEM-A", variant_of="ITEM-TEMPLATE", default_bom="BOM-WRONG")
		database = frappe._dict(get_value=Mock(return_value="ITEM-OTHER"))
		with (
			patch.object(frappe.local, "db", database, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "BOM-WRONG.*ITEM-OTHER"):
				Item.check_for_active_boms(doc)
		database.get_value.assert_called_once_with("BOM", "BOM-WRONG", "item")

	def test_default_bom_for_the_item_or_template_remains_accepted(self):
		doc = frappe._dict(name="ITEM-A", variant_of="ITEM-TEMPLATE", default_bom="BOM-VALID")
		for item in (doc.name, doc.variant_of):
			with self.subTest(item=item):
				database = frappe._dict(get_value=Mock(return_value=item))
				with patch.object(frappe.local, "db", database, create=True):
					Item.check_for_active_boms(doc)
				database.get_value.assert_called_once_with("BOM", "BOM-VALID", "item")

	def test_no_default_bom_does_not_query_for_one(self):
		database = frappe._dict(get_value=Mock())
		with patch.object(frappe.local, "db", database, create=True):
			Item.check_for_active_boms(frappe._dict(default_bom=None))
		database.get_value.assert_not_called()
