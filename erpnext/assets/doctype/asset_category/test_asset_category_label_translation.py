# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from pathlib import Path
from unittest.mock import Mock, call, patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase
from frappe.utils import get_link_to_form

from erpnext.assets.doctype.asset_category import asset_category


class TestAssetCategoryLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.metadata = {
			name: self.read_meta(module, name)
			for module, name in (
				("assets", "Asset Category Account"),
				("assets", "Asset Finance Book"),
				("accounts", "Account"),
			)
		}
		self.enterContext(patch("frappe.get_meta", side_effect=self.metadata.__getitem__))
		self.enterContext(patch("frappe.get_system_settings", return_value="Banker's Rounding"))
		self.enterContext(patch("frappe.throw", side_effect=self.raise_message))
		self.enterContext(
			patch("frappe.utils.data.get_url", side_effect=lambda uri: "https://example.invalid" + uri)
		)
		self.db = self.enterContext(patch("frappe.db", new=Mock()))
		targets = {
			"Banking": "Servizi bancari",
			"Account Type": "Tipo di conto",
			"Fixed Asset": "Cespite",
			"Accumulated Depreciation": "Fondo ammortamento",
			"Depreciation": "Ammortamento",
			"Capital Work in Progress": "Immobilizzazioni in corso",
			"Fixed Asset Account": "Conto dei cespiti",
			"Accumulated Depreciation Account": "Conto fondo ammortamento",
			"Depreciation Expense Account": "Conto di costo per ammortamento",
			"Capital Work In Progress Account": "Conto immobilizzazioni in corso",
			"Total Number of Depreciations": "Numero totale di ammortamenti",
			"Frequency of Depreciation (Months)": "Periodicità dell'ammortamento (mesi)",
		}
		keys = self.extracted_keys()
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={key: value for key, value in targets.items() if key in keys},
			)
		)

	@staticmethod
	def raise_message(message, exc=frappe.ValidationError, **kwargs):
		raise exc(message)

	@staticmethod
	def make_doc(cls=Document, **values):
		doc = cls.__new__(cls)
		doc.__dict__.update(flags=frappe._dict(), **values)
		return doc

	@staticmethod
	def metadata_path(module, doctype):
		slug = frappe.scrub(doctype)
		return Path(frappe.get_app_path("erpnext", module, "doctype", slug, f"{slug}.json"))

	def read_meta(self, module, doctype):
		data = json.loads(self.metadata_path(module, doctype).read_text())
		meta = Meta.__new__(Meta)
		meta.__dict__.update(
			name=data["name"], _fields={field["fieldname"]: frappe._dict(field) for field in data["fields"]}
		)
		return meta

	def extracted_keys(self):
		keys = set()
		for module, doctype in (
			("assets", "Asset Category Account"),
			("assets", "Asset Finance Book"),
			("accounts", "Account"),
		):
			with self.metadata_path(module, doctype).open("rb") as source:
				keys.update(
					message
					for _, message, _, context in extract(
						"frappe.gettext.extractors.doctype.extract", source, keywords=PYTHON_KEYWORDS
					)
					if context is None
				)
		return keys

	def category(self, **values):
		return self.make_doc(asset_category.AssetCategory, doctype="Asset Category", name="Banking", **values)

	def account_row(self, field, account="Banking"):
		return self.make_doc(
			doctype="Asset Category Account",
			idx=3,
			company_name="COMPANY-RAW",
			meta=self.metadata["Asset Category Account"],
			**{field: account},
		)

	def assert_labels(self, action, *labels, exception=frappe.ValidationError):
		with self.assertRaises(exception) as error:
			action()
		for label in labels:
			self.assertIn(label, str(error.exception))
		return str(error.exception)

	def test_finance_book_errors_use_both_real_metadata_labels(self):
		for field in ("total_number_of_depreciations", "frequency_of_depreciation"):
			with self.subTest(field=field):
				row = self.make_doc(
					doctype="Asset Finance Book",
					idx=2,
					total_number_of_depreciations=1,
					frequency_of_depreciation=1,
					meta=self.metadata["Asset Finance Book"],
				)
				row.set(field, 0)
				before = dict(row.__dict__)
				self.assert_labels(
					self.category(finance_books=[row]).validate_finance_books,
					row.meta.get_translated_label(field),
					exception=frappe.MandatoryError,
				)
				self.assertEqual(row.__dict__, before)
		self.assertEqual(
			self.metadata["Asset Finance Book"].get_label("frequency_of_depreciation"),
			"Frequency of Depreciation (Months)",
		)

	def test_valid_finance_books_and_existing_numeric_guards_remain_unchanged(self):
		row = self.make_doc(
			doctype="Asset Finance Book",
			meta=self.metadata["Asset Finance Book"],
			idx=1,
			total_number_of_depreciations=2,
			frequency_of_depreciation=3,
			salvage_value_percentage=100,
			rate_of_depreciation=0,
		)
		category = self.category(finance_books=[row])
		category.validate_finance_books()
		for field, value in (
			("salvage_value_percentage", -1),
			("salvage_value_percentage", 101),
			("rate_of_depreciation", -1),
		):
			with self.subTest(field=field, value=value):
				original = row.get(field)
				row.set(field, value)
				with self.assertRaises(frappe.ValidationError):
					category.validate_finance_books()
				row.set(field, original)

	def test_all_account_currency_errors_use_actual_field_labels_and_raw_names(self):
		for field in asset_category.ACCOUNT_FIELDS:
			with self.subTest(field=field):
				row = self.account_row(field)
				before = dict(row.__dict__)
				with patch("frappe.get_value", side_effect=["EUR", "USD"]) as read:
					message = self.assert_labels(
						self.category(accounts=[row]).validate_account_currency,
						frappe.bold(row.meta.get_translated_label(field)),
						frappe.bold("Banking"),
					)
					self.assertEqual(
						read.call_args_list,
						[
							call("Company", "COMPANY-RAW", "default_currency"),
							call("Account", "Banking", "account_currency"),
						],
					)
				self.assertNotIn("Servizi bancari", message)
				self.assertEqual(row.__dict__, before)

	def test_matching_account_currencies_and_absent_fields_are_valid(self):
		for field in asset_category.ACCOUNT_FIELDS:
			with patch("frappe.get_value", side_effect=["EUR", "EUR"]):
				self.category(accounts=[self.account_row(field)]).validate_account_currency()
		with patch("frappe.get_value", return_value="EUR") as read:
			self.category(
				accounts=[self.account_row("fixed_asset_account", None)]
			).validate_account_currency()
			read.assert_called_once_with("Company", "COMPANY-RAW", "default_currency")

	def test_account_type_errors_translate_field_and_enum_with_canonical_queries(self):
		for field, account_type in zip(
			asset_category.ACCOUNT_FIELDS,
			("Fixed Asset", "Accumulated Depreciation", "Depreciation", "Capital Work in Progress"),
			strict=True,
		):
			with self.subTest(field=field):
				row = self.account_row(field)
				before = dict(row.__dict__)
				self.db.get_value.return_value = "OTHER-TYPE"
				message = self.assert_labels(
					self.category(accounts=[row]).validate_account_types,
					"Tipo di conto",
					frappe.bold(frappe._(account_type)),
					frappe.bold("Banking"),
				)
				self.db.get_value.assert_called_once_with("Account", "Banking", "account_type")
				self.db.reset_mock()
				self.assertNotIn("Servizi bancari", message)
				self.assertEqual(row.__dict__, before)

	def test_valid_account_types_still_check_company_group_and_disabled(self):
		for field, account_type in zip(
			asset_category.ACCOUNT_FIELDS,
			("Fixed Asset", "Accumulated Depreciation", "Depreciation", "Capital Work in Progress"),
			strict=True,
		):
			row = self.account_row(field)
			category = self.category(accounts=[row])
			self.db.get_value.side_effect = [account_type, ("COMPANY-RAW", 0, 0)]
			category.validate_account_types()
			self.assertEqual(
				self.db.get_value.call_args_list,
				[
					call("Account", "Banking", "account_type"),
					call("Account", "Banking", ["company", "is_group", "disabled"]),
				],
			)
			self.db.reset_mock()
			for detail in (("OTHER-COMPANY", 0, 0), ("COMPANY-RAW", 1, 0), ("COMPANY-RAW", 0, 1)):
				self.db.get_value.side_effect = [account_type, detail]
				with self.assertRaises(frappe.ValidationError):
					category.validate_account_types()
				self.db.reset_mock()

	def test_missing_depreciation_accounts_translate_actual_labels_and_keep_links(self):
		row = self.account_row("fixed_asset_account")
		category = self.category(accounts=[row])
		self.db.get_all.return_value = ["COMPANY-RAW"]
		with patch("frappe.get_cached_value", return_value=frappe._dict()) as read:
			message = self.assert_labels(
				category.validate_depreciation_accounts,
				"Conto fondo ammortamento",
				"Conto di costo per ammortamento",
				get_link_to_form("Company", "COMPANY-RAW"),
			)
			read.assert_called_once_with(
				"Company",
				"COMPANY-RAW",
				["accumulated_depreciation_account", "depreciation_expense_account"],
				as_dict=True,
			)
		self.assertIn("<b>", message)
		self.db.get_all.assert_called_once_with(
			"Asset",
			{
				"calculate_depreciation": 1,
				"asset_category": "Banking",
				"status": ["in", asset_category.DEPRECIATING_ASSET_STATUSES],
			},
			pluck="company",
			distinct=True,
		)

	def test_depreciation_defaults_no_assets_and_no_account_row_controls(self):
		row = self.account_row("fixed_asset_account")
		category = self.category(accounts=[row])
		for defaults in (
			frappe._dict(
				accumulated_depreciation_account="ACCOUNT-A", depreciation_expense_account="ACCOUNT-B"
			),
		):
			self.db.get_all.return_value = ["COMPANY-RAW"]
			with patch("frappe.get_cached_value", return_value=defaults):
				category.validate_depreciation_accounts()
		self.db.get_all.return_value = []
		with patch("frappe.get_cached_value") as read:
			category.validate_depreciation_accounts()
			read.assert_not_called()
		self.db.get_all.return_value = ["COMPANY-RAW"]
		with patch("frappe.get_cached_value", return_value=frappe._dict()):
			self.assert_labels(
				self.category(accounts=[]).validate_depreciation_accounts,
				"Missing account configuration",
				"COMPANY-RAW",
			)
