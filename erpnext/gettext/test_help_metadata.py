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

from erpnext.controllers.selling_controller import SellingController
from erpnext.selling.doctype.product_bundle.product_bundle import ProductBundle
from erpnext.setup.doctype.terms_and_conditions.terms_and_conditions import (
	TermsandConditions,
	get_terms_and_conditions,
)

HELP_FIELDS = (
	("selling/doctype/product_bundle/product_bundle.json", "about"),
	("setup/doctype/terms_and_conditions/terms_and_conditions.json", "terms_and_conditions_help"),
)


class NoDatabase:
	def __bool__(self):
		return False

	def __getattr__(self, name):
		def blocked(*args, **kwargs):
			raise AssertionError(f"Unexpected database operation: {name}")

		return blocked


def metadata(relative):
	return json.loads(Path(frappe.get_app_path("erpnext", relative)).read_text())


def field(data, name):
	return next(row for row in data["fields"] if row["fieldname"] == name)


class TestHelpMetadata(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		self.addCleanup(setattr, frappe.local, "db", getattr(frappe.local, "db", None))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.db = NoDatabase()
		self.enterContext(patch("frappe.translate.get_all_translations", return_value={}))
		self.enterContext(patch("frappe.get_hooks", return_value=[]))
		self.enterContext(patch("frappe.get_active_apps", return_value=["frappe", "erpnext"]))
		for key in ("jenv_restricted", "jenv_unrestricted"):
			self.addCleanup(setattr, frappe.local, key, getattr(frappe.local, key, None))
			setattr(frappe.local, key, None)

	def test_bundle_help_uses_actual_item_labels_and_sales_context(self):
		item = metadata("stock/doctype/item/item.json")
		guide = field(metadata(HELP_FIELDS[0][0]), "about")
		self.assertEqual(guide["fieldtype"], "HTML")
		for name in ("is_stock_item", "is_sales_item"):
			self.assertIn(f"<code>{field(item, name)['label']}</code>", guide["options"])
		self.assertIn("To sell the package", guide["options"])

	def test_terms_help_describes_the_actual_transaction_template(self):
		meta = metadata(HELP_FIELDS[1][0])
		guide = field(meta, "terms_and_conditions_help")
		self.assertEqual(field(meta, "terms")["fieldtype"], "Text Editor")
		self.assertEqual(guide["fieldtype"], "HTML")
		self.assertNotIn("email template", guide["options"])
		self.assertIn("transaction document", guide["options"])
		self.assertIn("Customize Form", guide["options"])
		self.assertIn("Sales Order", guide["options"])
		self.assertIn("https://jinja.palletsprojects.com/en/stable/templates/", guide["options"])
		for token in ("{{ name }}", "{{ transaction_date }}", "{{ delivery_date }}"):
			self.assertIn(token, guide["options"])

	def test_bundle_definition_keeps_nonstock_and_fixed_asset_guards(self):
		for stock, fixed in ((False, False), (True, False), (False, True)):
			with self.subTest(stock=stock, fixed_asset=fixed):
				calls = []
				values = {"is_stock_item": stock, "is_fixed_asset": fixed, "is_sales_item": False}

				def read_value(doctype, name, key):
					calls.append((doctype, name, key))
					return values[key]

				bundle = ProductBundle.__new__(ProductBundle)
				bundle.__dict__.update(new_item_code="KIT-MEMORY-ONLY", flags=frappe._dict())
				with patch.object(frappe.local, "db", SimpleNamespace(get_value=read_value)):
					if stock or fixed:
						with self.assertRaises(frappe.ValidationError):
							bundle.validate_main_item()
					else:
						bundle.validate_main_item()
				self.assertEqual(bundle.new_item_code, "KIT-MEMORY-ONLY")
				self.assertTrue(calls)
				self.assertTrue(all(row[:2] == ("Item", "KIT-MEMORY-ONLY") for row in calls))
				self.assertNotIn("is_sales_item", [row[2] for row in calls])

	def test_selling_still_requires_the_sales_flag(self):
		for enabled in (False, True):
			with self.subTest(enabled=enabled):
				doc = SimpleNamespace(items=[SimpleNamespace(item_code="KIT-MEMORY-ONLY")])
				with patch("frappe.get_all", return_value=[] if enabled else ["KIT-MEMORY-ONLY"]) as query:
					if enabled:
						SellingController.validate_items(doc)
					else:
						with self.assertRaises(frappe.ValidationError):
							SellingController.validate_items(doc)
				self.assertEqual(
					query.call_args.kwargs["filters"],
					{"name": ["in", ["KIT-MEMORY-ONLY"]], "is_sales_item": 0},
				)
				self.assertEqual(doc.items[0].item_code, "KIT-MEMORY-ONLY")

	def stored_terms(self, body):
		data = metadata(HELP_FIELDS[1][0])
		meta = Meta.__new__(Meta)
		meta.__dict__.update(
			name=data["name"],
			doctype="DocType",
			istable=data.get("istable", 0),
			fields=[frappe._dict(parent=data["name"], **row) for row in data["fields"]],
		)
		doc = TermsandConditions.__new__(TermsandConditions)
		doc.__dict__.update(
			doctype=data["name"],
			name="TERMS-MEMORY-ONLY",
			terms=body,
			meta=meta,
			flags=frappe._dict(),
		)
		return doc

	def test_saved_terms_render_transaction_fields_after_the_actual_read_check(self):
		body = "Consegna per {{ name }}: {{ transaction_date }} / {{ delivery_date }}"
		transaction = {
			"doctype": "Sales Order",
			"name": "SO-MEMORY-ONLY",
			"transaction_date": "2026-10-21",
			"delivery_date": "2026-10-28",
		}
		before = deepcopy(transaction)
		doc = self.stored_terms(body)
		with (
			patch("frappe.get_cached_doc", return_value=doc) as stored,
			patch("frappe.permissions.has_permission", return_value=True) as permission,
		):
			for payload in (transaction, json.dumps(transaction)):
				self.assertEqual(
					get_terms_and_conditions(doc.name, payload),
					"Consegna per SO-MEMORY-ONLY: 2026-10-21 / 2026-10-28",
				)
				self.assertEqual(transaction, before)
				self.assertEqual(doc.terms, body)
				self.assertEqual(stored.call_args.args, ("Terms and Conditions", doc.name))
				permission.assert_called_with("Terms and Conditions", "read", doc, debug=False, user=None)
			self.assertEqual(permission.call_count, 2)
		self.assertFalse(doc.flags.ignore_permissions)
		self.assertEqual(frappe.session.user, "translator@example.com")

	def test_read_denial_and_empty_terms_keep_the_existing_permission_order(self):
		doc = self.stored_terms("")
		with (
			patch("frappe.get_cached_doc", return_value=doc),
			patch("frappe.permissions.has_permission", return_value=True) as permission,
		):
			self.assertIsNone(get_terms_and_conditions(doc.name, {}))
			permission.assert_called_once_with("Terms and Conditions", "read", doc, debug=False, user=None)
		with (
			patch("frappe.get_cached_doc", return_value=doc),
			patch("frappe.permissions.has_permission", return_value=False),
			patch(
				"frappe.permissions.check_doctype_permission", side_effect=frappe.PermissionError
			) as denied,
		):
			with self.assertRaises(frappe.PermissionError):
				get_terms_and_conditions(doc.name, {})
			denied.assert_called_once_with("Terms and Conditions", "read")
		self.assertFalse(doc.flags.ignore_permissions)

	def test_configured_extraction_keeps_each_help_as_a_whole_message(self):
		methods = get_method_map("erpnext") + get_method_map("frappe")
		for relative, name in HELP_FIELDS:
			with self.subTest(field=name):
				path = Path(frappe.get_app_path("erpnext", relative))
				before = path.read_bytes()
				guide = field(json.loads(before), name)
				method = next(
					method for pattern, method in methods if pathmatch(pattern, f"erpnext/{relative}")
				)
				self.assertEqual(method, "frappe.gettext.extractors.doctype.extract")
				messages = list(extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS))
				self.assertEqual(sum(message == guide["options"] for _, message, _, _ in messages), 1)
				self.assertEqual(path.read_bytes(), before)
