# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, call, patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase
from frappe.utils import get_link_to_form

from erpnext.accounts.doctype.sales_invoice.services import fixed_assets
from erpnext.assets.doctype.asset import asset, mapper
from erpnext.assets.doctype.asset_capitalization import asset_capitalization
from erpnext.assets.report.fixed_asset_register import fixed_asset_register
from erpnext.selling.doctype.proforma_invoice import proforma_invoice

RATE_MESSAGE = "Row #{0}: Service rate must be a positive number"
NEW_SPLIT_NOTE = "This schedule was created when Asset {0} was split to create Asset {1}."
UPDATED_SPLIT_NOTE = (
	"This schedule was created when Asset {0} was updated after being split to create Asset {1}."
)
FIXED_ACCOUNT_MESSAGE = "Set {0} in the asset category for company {1}."


class TestAssetLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.metadata = {
			name: self.read_meta(module, name)
			for module, name in (
				("assets", "Asset"),
				("assets", "Asset Category Account"),
				("setup", "Company"),
			)
		}
		self.enterContext(patch("frappe.get_meta", side_effect=self.metadata.__getitem__))
		self.enterContext(
			patch(
				"frappe.get_system_settings",
				side_effect=lambda key: {"rounding_method": "Banker's Rounding", "time_zone": "UTC"}.get(key),
			)
		)
		self.enterContext(patch("frappe.throw", side_effect=self.raise_message))
		self.enterContext(
			patch("frappe.utils.data.get_url", side_effect=lambda uri: "https://example.invalid" + uri)
		)
		self.db = self.enterContext(patch("frappe.db", new=Mock()))
		targets = {
			"returned": "restituito",
			"sold": "venduto",
			"Scrapped": "Rottamato",
			"Cancelled": "Annullato",
			"Capitalized": "Capitalizzato",
			"Sold": "Venduto",
			"Draft": "Bozza",
			"Location": "Posizione",
			"Asset Category": "Categoria cespiti",
			"Purchase Receipt": "Ricevimento merci",
			"Purchase Invoice": "Fattura di acquisto",
			"Fixed Asset Account": "Conto dei cespiti",
			"Capital Work In Progress Account": "Conto immobilizzazioni in corso",
			RATE_MESSAGE: "Riga n. {0}: il costo unitario del servizio deve essere maggiore di zero.",
			NEW_SPLIT_NOTE: "Piano creato quando il cespite {0} è stato frazionato per creare il cespite {1}.",
			UPDATED_SPLIT_NOTE: "Piano creato dopo l’aggiornamento del cespite {0}, frazionato per creare il cespite {1}.",
			FIXED_ACCOUNT_MESSAGE: "Impostare {0} nella categoria cespiti per l’azienda {1}.",
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
		for module in (
			asset,
			mapper,
			asset_capitalization,
			fixed_assets,
			fixed_asset_register,
			proforma_invoice,
		):
			with Path(module.__file__).open("rb") as source:
				keys.update(
					message
					for _, message, _, context in extract(
						"frappe.gettext.extractors.python.extract", source, keywords=PYTHON_KEYWORDS
					)
					if context is None
				)
		for module, doctype in (
			("assets", "Asset"),
			("assets", "Asset Category Account"),
			("setup", "Company"),
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

	def assert_labels(self, action, *labels, exception=frappe.ValidationError):
		with self.assertRaises(exception) as error:
			action()
		for label in labels:
			self.assertIn(label, str(error.exception))
		return str(error.exception)

	def sales_doc(self, **values):
		doc = self.make_doc(
			doctype="Sales Invoice",
			is_return=0,
			return_against=None,
			update_stock=0,
			posting_date="2026-10-20",
			items=[frappe._dict(idx=2, is_fixed_asset=1, asset="Location", item_code="ITEM-RAW")],
		)
		doc.__dict__.update(values)
		return doc

	def test_asset_sale_rejected_status_labels_are_translated(self):
		for status in ("Scrapped", "Cancelled", "Capitalized"):
			with self.subTest(status=status):
				doc = self.sales_doc()
				before = deepcopy(doc.items)
				self.db.get_value.return_value = (status, 1)
				message = self.assert_labels(
					fixed_assets.FixedAssetService(doc).validate_fixed_asset, frappe._(status), "Location"
				)
				self.db.get_value.assert_called_once_with("Asset", "Location", ["status", "docstatus"])
				self.db.reset_mock()
				self.assertNotIn("Posizione", message)
				self.assertEqual(doc.items, before)

	def test_asset_sale_draft_sold_return_stock_and_date_controls(self):
		for status, docstatus in (("Draft", 0), ("Sold", 1)):
			self.db.get_value.return_value = (status, docstatus)
			with self.assertRaises(frappe.ValidationError):
				fixed_assets.FixedAssetService(self.sales_doc()).validate_fixed_asset()
		self.db.get_value.return_value = ("Submitted", 1)
		with patch.object(fixed_assets, "get_last_depreciation_date", return_value=None) as date:
			fixed_assets.FixedAssetService(self.sales_doc()).validate_fixed_asset()
			date.assert_called_once_with("Location")
		with patch.object(fixed_assets, "get_last_depreciation_date", return_value=None) as date:
			fixed_assets.FixedAssetService(
				self.sales_doc(is_return=1, return_against="ORIGINAL-RAW")
			).validate_fixed_asset()
			date.assert_not_called()
		for doc in (self.sales_doc(update_stock=1), self.sales_doc(is_return=1)):
			with self.assertRaises(frappe.ValidationError):
				fixed_assets.FixedAssetService(doc).validate_fixed_asset()

	def capitalization(self, **values):
		doc = self.make_doc(
			asset_capitalization.AssetCapitalization,
			doctype="Asset Capitalization",
			company="COMPANY-RAW",
			target_asset="TARGET-RAW",
			target_item_code="ITEM-RAW",
			asset_items=[],
			service_items=[],
			stock_items=[],
		)
		doc.__dict__.update(values)
		return doc

	def validation_asset(self, **values):
		result = frappe._dict(
			name="Location",
			item_code="ITEM-RAW",
			company="COMPANY-RAW",
			status="Work In Progress",
			docstatus=0,
			asset_type="Composite Asset",
		)
		result.update(values)
		return result

	def test_target_asset_status_labels_use_asset_display_context(self):
		for status in ("Scrapped", "Sold", "Capitalized"):
			with self.subTest(status=status):
				self.db.get_value.return_value = self.validation_asset(status=status)
				self.assert_labels(self.capitalization().validate_target_asset, frappe._(status), "Location")
				self.db.get_value.assert_called_once_with(
					"Asset",
					"TARGET-RAW",
					["name", "item_code", "company", "status", "docstatus", "asset_type"],
					as_dict=1,
				)
				self.db.reset_mock()

	def test_consumed_asset_status_labels_keep_raw_asset_queries(self):
		for status in ("Draft", "Scrapped", "Sold", "Capitalized"):
			with self.subTest(status=status):
				self.db.get_value.return_value = self.validation_asset(status=status, docstatus=1)
				doc = self.capitalization(asset_items=[frappe._dict(idx=3, asset="Location")])
				before = deepcopy(doc.asset_items)
				message = self.assert_labels(doc.validate_consumed_asset_item, frappe._(status), "Location")
				self.assertNotIn("Posizione", message)
				self.db.get_value.assert_called_once_with(
					"Asset",
					"Location",
					["name", "item_code", "company", "status", "docstatus", "asset_type"],
					as_dict=1,
				)
				self.db.reset_mock()
				self.assertEqual(doc.asset_items, before)

	def test_target_and_consumed_asset_eligibility_controls(self):
		self.db.get_value.return_value = self.validation_asset()
		self.capitalization().validate_target_asset()
		for overrides in (
			{"asset_type": "Standalone"},
			{"item_code": "OTHER"},
			{"docstatus": 1},
			{"docstatus": 2},
			{"company": "OTHER"},
		):
			self.db.get_value.return_value = self.validation_asset(**overrides)
			with self.assertRaises(frappe.ValidationError):
				self.capitalization().validate_target_asset()
		with self.assertRaises(frappe.MandatoryError):
			self.capitalization(target_asset=None).validate_target_asset()
		doc = self.capitalization(asset_items=[frappe._dict(idx=1, asset="CONSUMED-RAW")])
		self.db.get_value.return_value = self.validation_asset(status="Submitted", docstatus=1)
		doc.validate_consumed_asset_item()
		for overrides in ({"docstatus": 0}, {"docstatus": 2}, {"company": "OTHER"}):
			self.db.get_value.return_value = self.validation_asset(status="Submitted", **overrides)
			with self.assertRaises(frappe.ValidationError):
				doc.validate_consumed_asset_item()

	def test_service_rate_error_names_the_actual_validated_value(self):
		for rate in (0, -2):
			with self.subTest(rate=rate), patch(
				"frappe.get_cached_doc", return_value=frappe._dict(is_stock_item=0, is_fixed_asset=0)
			):
				row = frappe._dict(idx=4, item_code="SERVICE-RAW", qty=1, rate=rate, amount=99)
				before = row.copy()
				self.assert_labels(
					self.capitalization(service_items=[row]).validate_service_item,
					"costo unitario del servizio",
					"4",
				)
				self.assertEqual(row, before)

	def test_service_positive_values_qty_precedence_and_item_type_controls(self):
		item = frappe._dict(
			name="SERVICE-RAW", is_stock_item=0, is_fixed_asset=0, end_of_life="2999-01-01", disabled=0
		)
		row = self.make_doc(
			doctype="Asset Capitalization Service Item",
			idx=4,
			item_code="SERVICE-RAW",
			qty=3,
			rate=12.5,
			cost_center=None,
			_precision=frappe._dict(main=frappe._dict(amount=2)),
		)
		doc = self.capitalization(
			service_items=[row],
			_precision=frappe._dict(
				main=frappe._dict(
					stock_items_total=2, asset_items_total=2, service_items_total=2, total_value=2
				)
			),
		)
		with patch("frappe.get_cached_doc", return_value=item), patch(
			"frappe.get_cached_value", return_value="COST-CENTER-RAW"
		) as read:
			doc.validate_service_item()
			read.assert_called_once_with("Company", "COMPANY-RAW", "cost_center")
			doc.calculate_totals()
			self.assertEqual(
				(row.amount, doc.service_items_total, doc.total_value, doc.target_incoming_rate),
				(37.5, 37.5, 37.5, 37.5),
			)
			self.assertEqual((row.qty, row.rate, row.cost_center), (3, 12.5, "COST-CENTER-RAW"))
			row.qty = 0
			self.assert_labels(doc.validate_service_item, "Qty must be a positive number")
		for key in ("is_stock_item", "is_fixed_asset"):
			with patch("frappe.get_cached_doc", return_value=frappe._dict({key: 1})):
				self.assert_labels(
					self.capitalization(
						service_items=[frappe._dict(idx=1, item_code="ITEM-RAW")]
					).validate_service_item,
					"not a service item",
				)

	def test_proforma_amount_and_quantity_arithmetic_stay_unchanged(self):
		invoice = self.make_doc(proforma_invoice.ProformaInvoice, based_on="Amount")
		for amount in (0, -10):
			self.assert_labels(
				lambda: invoice.set_rate_and_amount(frappe._dict(idx=5, qty=2, amount=amount), 99),
				"Amount must be a positive number",
			)
		row = frappe._dict(idx=1, qty=4, amount=100)
		invoice.set_rate_and_amount(row, 99)
		self.assertEqual((row.qty, row.amount, row.rate), (4, 100, 25))
		invoice.based_on = "Quantity"
		invoice.set_rate_and_amount(row, 12)
		self.assertEqual((row.qty, row.amount, row.rate), (4, 48, 12))
		self.assert_labels(
			lambda: invoice.set_rate_and_amount(frappe._dict(idx=1, qty=0, amount=100), 12),
			"Qty must be a positive number",
		)

	def account_read(self, result):
		def get_value(doctype, name=None, fieldname=None, **kwargs):
			if doctype == "Asset":
				return ("CATEGORY-RAW", "COMPANY-RAW")
			self.assertEqual((doctype, fieldname), ("Asset Category Account", "fixed_asset_account"))
			self.assertEqual(kwargs, {"filters": {"parent": "CATEGORY-RAW", "company_name": "COMPANY-RAW"}})
			return result

		return get_value

	def test_missing_fixed_asset_account_directs_to_actual_category_not_company_field(self):
		self.assertIsNone(self.metadata["Company"].get_field("fixed_asset_account"))
		self.db.get_value.side_effect = self.account_read(None)
		with patch("frappe.get_cached_value", return_value=None) as company:
			message = self.assert_labels(
				lambda: asset.get_asset_account(
					"fixed_asset_account", asset="ASSET-RAW", company="COMPANY-RAW"
				),
				"Conto dei cespiti",
				"categoria cespiti",
				"COMPANY-RAW",
			)
			self.assertNotIn("in company", message)
			company.assert_called_once_with("Company", "COMPANY-RAW", "fixed_asset_account")
		with patch("frappe.get_cached_value", return_value=None):
			self.assert_labels(
				lambda: asset.get_asset_account(
					"fixed_asset_account", asset_category="CATEGORY-RAW", company="COMPANY-RAW"
				),
				"Conto dei cespiti",
				"CATEGORY-RAW",
				"COMPANY-RAW",
			)

	def test_missing_cwip_account_uses_metadata_for_real_company_category_fields(self):
		self.db.get_value.side_effect = None
		self.db.get_value.return_value = None
		for category in (None, "CATEGORY-RAW"):
			with self.subTest(category=category), patch(
				"frappe.get_cached_value", return_value=None
			) as company:
				self.assert_labels(
					lambda: asset.get_asset_account(
						"capital_work_in_progress_account", asset_category=category, company="COMPANY-RAW"
					),
					"Conto immobilizzazioni in corso",
					"COMPANY-RAW",
				)
				company.assert_called_once_with("Company", "COMPANY-RAW", "capital_work_in_progress_account")

	def test_account_category_and_company_success_fallbacks_keep_raw_results(self):
		self.db.get_value.side_effect = self.account_read("Location")
		with patch("frappe.get_cached_value") as company:
			self.assertEqual(
				asset.get_asset_account("fixed_asset_account", asset="ASSET-RAW", company="COMPANY-RAW"),
				"Location",
			)
			company.assert_not_called()
		self.db.get_value.side_effect = self.account_read(None)
		with patch("frappe.get_cached_value", return_value="COMPANY-ACCOUNT-RAW"):
			self.assertEqual(
				asset.get_asset_account("fixed_asset_account", asset="ASSET-RAW", company="COMPANY-RAW"),
				"COMPANY-ACCOUNT-RAW",
			)

	def purchase_doc(self, **values):
		doc = self.make_doc(
			company="COMPANY-RAW",
			posting_date="2026-10-20",
			cost_center="PARENT-CC",
			items=[
				frappe._dict(
					name="ROW-RAW",
					item_code="ITEM-RAW",
					valuation_rate=12.5,
					qty=4,
					cost_center=None,
					asset_location="LOCATION-RAW",
				)
			],
		)
		doc.__dict__.update(values)
		return doc

	def test_purchase_source_missing_item_translates_only_doctype(self):
		for doctype in ("Purchase Receipt", "Purchase Invoice"):
			with self.subTest(doctype=doctype), patch(
				"frappe.has_permission", return_value=True
			) as permission, patch("frappe.get_doc", return_value=self.purchase_doc(items=[])) as read, patch(
				"erpnext.stock.doctype.company_restriction.company_restriction.get_allowed_companies",
				return_value=None,
			):
				self.assert_labels(
					lambda: asset.get_values_from_purchase_doc("SOURCE-RAW", "Location", doctype),
					frappe._(doctype),
					"Location",
				)
				permission.assert_called_once_with("Asset", "write", throw=True)
				read.assert_called_once_with(doctype, "SOURCE-RAW", check_permission="read")

	def test_purchase_source_payload_permissions_allowlist_and_company_controls(self):
		for doctype in ("Purchase Receipt", "Purchase Invoice"):
			with patch("frappe.has_permission", return_value=True), patch(
				"frappe.get_doc", return_value=self.purchase_doc()
			), patch(
				"erpnext.stock.doctype.company_restriction.company_restriction.get_allowed_companies",
				return_value=None,
			):
				result = asset.get_values_from_purchase_doc("SOURCE-RAW", "ITEM-RAW", doctype)
				self.assertEqual(
					result,
					{
						"company": "COMPANY-RAW",
						"purchase_date": "2026-10-20",
						"net_purchase_amount": 50,
						"asset_quantity": 4,
						"cost_center": "PARENT-CC",
						"asset_location": "LOCATION-RAW",
						"purchase_receipt_item": "ROW-RAW" if doctype == "Purchase Receipt" else None,
						"purchase_invoice_item": "ROW-RAW" if doctype == "Purchase Invoice" else None,
					},
				)
		with patch("frappe.get_doc") as read:
			with self.assertRaises(frappe.PermissionError):
				asset.get_values_from_purchase_doc("SOURCE-RAW", "ITEM-RAW", "Sales Invoice")
			read.assert_not_called()
		with patch("frappe.has_permission", side_effect=frappe.PermissionError), patch(
			"frappe.get_doc"
		) as read:
			with self.assertRaises(frappe.PermissionError):
				asset.get_values_from_purchase_doc("SOURCE-RAW", "ITEM-RAW", "Purchase Invoice")
			read.assert_not_called()
		with patch("frappe.has_permission", return_value=True), patch(
			"frappe.get_doc", side_effect=frappe.PermissionError
		):
			with self.assertRaises(frappe.PermissionError):
				asset.get_values_from_purchase_doc("SOURCE-RAW", "ITEM-RAW", "Purchase Invoice")
		with patch("frappe.has_permission", return_value=True), patch(
			"frappe.get_doc", return_value=self.purchase_doc()
		), patch(
			"erpnext.stock.doctype.company_restriction.company_restriction.get_allowed_companies",
			return_value=["OTHER-COMPANY"],
		):
			with self.assertRaises(frappe.PermissionError):
				asset.get_values_from_purchase_doc("SOURCE-RAW", "ITEM-RAW", "Purchase Invoice")

	def test_existing_sale_and_return_note_translations_remain_correct(self):
		asset_doc = frappe._dict(doctype="Asset", name="Location")
		for is_return, participle in ((False, "venduto"), (True, "restituito")):
			with self.subTest(is_return=is_return):
				doc = self.sales_doc(is_return=is_return, name="INVOICE-RAW")
				note = fixed_assets.FixedAssetService(doc)._get_note_for_asset_sale(asset_doc)
				self.assertIn(participle, note)
				self.assertIn(get_link_to_form("Asset", "Location"), note)
				self.assertIn(get_link_to_form("Sales Invoice", "INVOICE-RAW"), note)
				self.assertNotIn("Posizione", note)

	def test_both_split_notes_are_complete_translated_templates_and_keep_links(self):
		existing = frappe._dict(doctype="Asset", name="Location")
		new = frappe._dict(doctype="Asset", name="NEW-RAW")
		for is_new, phrase in ((True, "è stato frazionato"), (False, "dopo l’aggiornamento")):
			with self.subTest(is_new=is_new):
				schedule = self.make_doc()
				mapper.add_depr_schedule_notes(schedule, existing, new, is_new)
				self.assertIn(phrase, schedule.notes)
				self.assertIn(get_link_to_form("Asset", "Location"), schedule.notes)
				self.assertIn(get_link_to_form("Asset", "NEW-RAW"), schedule.notes)
				self.assertNotIn("Posizione", schedule.notes)

	def test_split_status_labels_and_unchanged_quantity_guards(self):
		for status, docstatus in (
			("Sold", 1),
			("Scrapped", 1),
			("Capitalized", 1),
			("Draft", 0),
			("Cancelled", 2),
		):
			with self.subTest(status=status):
				existing = frappe._dict(name="Location", status=status, docstatus=docstatus, asset_quantity=8)
				before = existing.copy()
				self.assert_labels(
					lambda: mapper.validate_asset_split(existing, 2), frappe._(status), "Location"
				)
				self.assertEqual(existing, before)
		existing = frappe._dict(name="Location", status="Submitted", docstatus=1, asset_quantity=8)
		mapper.validate_asset_split(existing, 2)
		for qty in (-1, 0, 8, 9):
			with self.assertRaises(frappe.ValidationError):
				mapper.validate_asset_split(existing, qty)

	def test_split_financial_scaling_and_depreciation_arithmetic_remain_exact(self):
		row = frappe._dict(
			value_after_depreciation=800, expected_value_after_useful_life=200, value_adjustment_amount=40
		)
		existing = self.make_doc(
			name="ORIGINAL-RAW",
			net_purchase_amount=1000,
			additional_asset_cost=200,
			opening_accumulated_depreciation=100,
			value_after_depreciation=800,
			value_adjustment_amount=120,
			asset_quantity=8,
			finance_books=[row],
		)
		new = self.make_doc(finance_books=[deepcopy(row)])
		mapper.set_split_asset_values(new, 0.25, 2, existing, True)
		self.assertEqual(
			(
				new.net_purchase_amount,
				new.purchase_amount,
				new.additional_asset_cost,
				new.total_asset_cost,
				new.opening_accumulated_depreciation,
				new.value_after_depreciation,
				new.value_adjustment_amount,
				new.asset_quantity,
				new.split_from,
			),
			(250, 250, 50, 300, 25, 200, 30, 2, "ORIGINAL-RAW"),
		)
		self.assertEqual(
			new.finance_books[0],
			{
				"value_after_depreciation": 200,
				"expected_value_after_useful_life": 50,
				"value_adjustment_amount": 10,
			},
		)
		term = self.make_doc(
			depreciation_amount=500, _precision=frappe._dict(main=frappe._dict(depreciation_amount=2))
		)
		schedule = self.make_doc(opening_accumulated_depreciation=1000, depreciation_schedule=[term])
		mapper.update_depreciation_terms(schedule, 0.25)
		self.assertEqual((term.depreciation_amount, term.accumulated_depreciation_amount), (125, 1125))

	def test_grouped_headers_translate_actual_values_and_keep_canonical_column_payloads(self):
		for group, label in (("Asset Category", "Categoria cespiti"), ("Location", "Posizione")):
			with self.subTest(group=group):
				filters = frappe._dict(group_by=group)
				columns = fixed_asset_register.get_columns(filters)
				self.assertEqual(
					columns[0],
					{
						"label": label,
						"fieldtype": "Link",
						"fieldname": frappe.scrub(group),
						"options": group,
						"width": 216,
					},
				)
				self.assertEqual(filters.group_by, group)
				self.assertEqual(
					[column["fieldname"] for column in columns[1:]],
					[
						"net_purchase_amount",
						"opening_accumulated_depreciation",
						"depreciated_amount",
						"asset_value",
						"company",
					],
				)
				self.assertTrue(
					all(column["options"] == "Company:company:default_currency" for column in columns[1:5])
				)

	def test_ungrouped_register_and_existing_disposal_value_controls(self):
		columns = fixed_asset_register.get_columns(frappe._dict())
		self.assertEqual((columns[0]["fieldname"], columns[0]["options"]), ("asset_id", "Asset"))
		value = frappe._dict(
			status="Sold",
			disposal_date="2026-10-20",
			net_purchase_amount=1000,
			opening_accumulated_depreciation=100,
		)
		self.assertEqual(fixed_asset_register.get_asset_value(value, 200, 50, "2026-10-19"), 750)
		self.assertEqual(fixed_asset_register.get_asset_value(value, 200, 50, "2026-10-20"), 0)

	def test_new_messages_are_extracted_and_obsolete_fragments_retire(self):
		keys = self.extracted_keys()
		for source in (RATE_MESSAGE, NEW_SPLIT_NOTE, UPDATED_SPLIT_NOTE, FIXED_ACCOUNT_MESSAGE):
			with self.subTest(source=source):
				self.assertIn(source, keys)
		self.assertNotIn("This schedule was created when Asset {0} was {1} into new Asset {2}.", keys)
		self.assertNotIn("{}", keys)
		self.assertIn("Row #{0}: Amount must be a positive number", keys)
