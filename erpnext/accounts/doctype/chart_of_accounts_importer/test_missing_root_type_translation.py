# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import itertools
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

from erpnext.accounts.doctype.chart_of_accounts_importer import chart_of_accounts_importer as importer

FRAME = "Please add Root Account for - {0}"
TARGETS = {
	FRAME: "Aggiungere un conto radice per: {0}.",
	"Asset": "Cespite",
	"Asset:Account": "Attività",
	"Liability": "Passività",
	"Expense": "Costi",
	"Income": "Ricavi",
	"Equity": "Patrimonio netto",
}


class TestMissingRootTypeTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(patch.object(frappe.local, "message_log", []))
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(frappe, "db", SimpleNamespace(db_type="mariadb")))
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: TARGETS if lang == "it" else {},
			)
		)
		for name in ("get_all", "get_doc", "get_cached_value", "new_doc", "enqueue"):
			self.enterContext(patch("frappe." + name, side_effect=AssertionError("Unexpected I/O: " + name)))
		data = json.loads(
			Path(frappe.get_app_path("erpnext", "accounts/doctype/account/account.json")).read_text()
		)
		fields = [frappe._dict(parent=data["name"], **field) for field in data["fields"]]
		self.meta = Meta.__new__(Meta)
		self.meta.__dict__.update(
			name="Account",
			doctype="DocType",
			fields=fields,
			_fields={field.fieldname: field for field in fields},
		)
		self.reads = []
		self.enterContext(patch("frappe.get_meta", side_effect=self.get_meta))

	def get_meta(self, doctype, *args, **kwargs):
		self.assertEqual((doctype, args, kwargs), ("Account", (), {}))
		self.reads.append(doctype)
		return self.meta

	def roots(self, included):
		return [frappe._dict(root_type=value, account_name="RAW-" + value) for value in included]

	def test_all_missing_subsets_use_actual_select_display_and_preserve_input(self):
		types = importer.get_root_types()
		self.assertEqual(types, ("Asset", "Liability", "Expense", "Income", "Equity"))
		for count in range(5):
			for included in itertools.combinations(types, count):
				with self.subTest(included=included):
					self.reads.clear()
					roots = self.roots(included)
					before = deepcopy(roots)
					missing = list(set(types) - set(included))
					labels = ["Attività" if value == "Asset" else TARGETS[value] for value in missing]
					with self.assertRaises(frappe.ValidationError) as caught:
						importer.validate_missing_roots(roots)
					self.assertEqual(str(caught.exception), TARGETS[FRAME].format(" , ".join(labels)))
					self.assertEqual(roots, before)
					self.assertEqual(self.reads, ["Account"])

	def test_complete_roots_skip_metadata_and_keep_unknown_and_duplicate_records(self):
		roots = self.roots(importer.get_root_types()) + self.roots(("Asset", "Custom Type"))
		before = deepcopy(roots)
		self.assertIsNone(importer.validate_missing_roots(roots))
		self.assertEqual(roots, before)
		self.assertEqual(self.reads, [])

	def test_account_context_wins_over_physical_asset_global(self):
		roots = self.roots(("Liability", "Expense", "Income", "Equity"))
		with self.assertRaises(frappe.ValidationError) as caught:
			importer.validate_missing_roots(roots)
		self.assertEqual(str(caught.exception), "Aggiungere un conto radice per: Attività.")
		root_type = "Asset"
		self.assertEqual(frappe._(root_type), "Cespite")
		self.assertEqual(
			frappe.format_value("Asset", self.meta.get_field("root_type"), translated=True), "Attività"
		)

	def test_english_fallback_keeps_original_order_and_delimiter(self):
		frappe.local.lang = "en"
		roots = self.roots(("Income",))
		before = deepcopy(roots)
		missing = list(set(importer.get_root_types()) - {"Income"})
		with self.assertRaises(frappe.ValidationError) as caught:
			importer.validate_missing_roots(roots)
		self.assertEqual(str(caught.exception), FRAME.format(" , ".join(missing)))
		self.assertEqual(roots, before)

	def test_actual_parent_validator_keeps_root_names_and_missing_type_precedence(self):
		accounts = {str(index): root for index, root in enumerate(self.roots(("Asset", "Expense")))}
		accounts["invalid"] = frappe._dict(account_name="RAW-CUSTOM", root_type="Custom Type")
		before = deepcopy(accounts)
		missing = list(set(importer.get_root_types()) - {"Asset", "Expense", "Custom Type"})
		with self.assertRaises(frappe.ValidationError) as caught:
			importer.validate_root(accounts)
		self.assertEqual(
			str(caught.exception), TARGETS[FRAME].format(" , ".join(TARGETS[value] for value in missing))
		)
		self.assertEqual(accounts, before)

	def test_frame_and_account_context_are_configured_extraction_keys(self):
		keys = set()
		for path in (
			Path(importer.__file__),
			Path(frappe.get_app_path("erpnext", "gettext/contextual_messages.py")),
		):
			root = Path(frappe.get_app_path("erpnext")).parent
			relative = str(path.relative_to(root))
			method = next(
				method
				for pattern, method in get_method_map("erpnext") + get_method_map("frappe")
				if pathmatch(pattern, relative)
			)
			keys.update(
				(message, context)
				for _, message, _, context in extract_from_file(method, str(path), keywords=PYTHON_KEYWORDS)
			)
		self.assertIn((FRAME, None), keys)
		self.assertIn(("Asset", "Account"), keys)
