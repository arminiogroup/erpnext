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

from erpnext.accounts import party
from erpnext.accounts.doctype.gl_entry import gl_entry
from erpnext.accounts.doctype.party_link.party_link import PartyLink
from erpnext.accounts.services.party_validation import PartyValidator

CURRENCY_MESSAGE = "{0} {1} has accounting entries in currency {2} for company {3}. Please select an account with currency {2}."
LINK_MESSAGE = "{0} {1} is already linked to another party."


class TestPartyLabelTranslation(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(setattr, frappe.local, "lang", frappe.local.lang)
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				return_value={
					"Customer": "Cliente",
					"Supplier": "Fornitore",
					"Employee": "Dipendente",
					"Shareholder": "Azionista",
					"Banking": "Servizi bancari",
					"Income Account": "Conto ricavi",
					"Expense Head": "Conto costi",
					"Debit To": "Conto da addebitare",
					"Credit To": "Conto da accreditare",
					CURRENCY_MESSAGE: "Per {0} {1} sono già presenti registrazioni contabili in valuta {2} nell’azienda {3}. Selezionare un conto in valuta {2}.",
					LINK_MESSAGE: "{0} {1} ha già un collegamento con un’altra controparte.",
				},
			)
		)
		self.enterContext(patch("frappe.throw", side_effect=self.raise_message))
		self.db = self.enterContext(patch("frappe.db", new=Mock()))
		self.enterContext(patch.object(frappe.local, "party_account_types", {}, create=True))
		self.enterContext(patch.dict(frappe.flags, ignore_party_validation=False))

	@staticmethod
	def raise_message(message, exc=frappe.ValidationError, **kwargs):
		raise exc(message)

	@staticmethod
	def make_doc(cls=Document, **values):
		doc = cls.__new__(cls)
		doc.__dict__.update(flags=frappe._dict(), **values)
		return doc

	@staticmethod
	def read_meta(doctype):
		folder = frappe.scrub(doctype)
		source = json.loads(
			Path(frappe.get_app_path("erpnext", "accounts", "doctype", folder, f"{folder}.json")).read_text()
		)
		meta = Meta.__new__(Meta)
		meta.__dict__.update(
			name=source["name"],
			_fields={field["fieldname"]: frappe._dict(field) for field in source["fields"]},
		)
		return meta

	def assert_labels(self, action, *labels, exception=frappe.ValidationError):
		with self.assertRaises(exception) as error:
			action()
		for label in labels:
			self.assertIn(label, str(error.exception))
		return str(error.exception)

	def test_invoice_account_diagnostics_use_real_parent_and_child_metadata(self):
		for doctype, child_field, parent_field, child_label, parent_label in (
			("Sales Invoice", "income_account", "debit_to", "Income Account", "Debit To"),
			("Purchase Invoice", "expense_account", "credit_to", "Expense Head", "Credit To"),
		):
			with self.subTest(doctype=doctype):
				item = self.make_doc(
					doctype=f"{doctype} Item",
					idx=2,
					meta=self.read_meta(f"{doctype} Item"),
					**{child_field: "Customer"},
				)
				doc = self.make_doc(
					doctype=doctype, items=[item], meta=self.read_meta(doctype), **{parent_field: "Customer"}
				)
				self.assertEqual(item.meta.get_label(child_field), child_label)
				self.assertEqual(doc.meta.get_label(parent_field), parent_label)
				message = self.assert_labels(
					PartyValidator(doc).validate_party_accounts,
					frappe.bold(frappe._(child_label)),
					frappe.bold(frappe._(parent_label)),
					"Customer",
				)
				self.assertNotIn("Cliente", message)
				self.assertEqual(item.get(child_field), "Customer")
				self.assertEqual(doc.get(parent_field), "Customer")

	def test_different_invoice_accounts_and_noninvoice_documents_remain_valid(self):
		for doctype, child_field, parent_field in (
			("Sales Invoice", "income_account", "debit_to"),
			("Purchase Invoice", "expense_account", "credit_to"),
		):
			item = self.make_doc(doctype=f"{doctype} Item", **{child_field: "INCOME-OR-EXPENSE-RAW"})
			doc = self.make_doc(doctype=doctype, items=[item], **{parent_field: "PARTY-ACCOUNT-RAW"})
			PartyValidator(doc).validate_party_accounts()
		PartyValidator(self.make_doc(doctype="Sales Order")).validate_party_accounts()
		PartyValidator(self.make_doc(doctype="Purchase Order")).validate_party_accounts()

	def account_cache(self, account_type):
		def get_cached_value(doctype, name, field):
			if doctype == "Account":
				return {
					"account_currency": "EUR",
					"company": "COMPANY-RAW",
					"is_group": 0,
					"account_type": account_type,
				}[field]
			self.assertEqual((doctype, field), ("Company", "default_currency"))
			return "EUR"

		return get_cached_value

	def account_doc(self, party_type, companies):
		return self.make_doc(
			doctype=party_type,
			name="Customer",
			default_currency="EUR",
			accounts=[
				frappe._dict(idx=i, company=company, account="ACCOUNT-RAW", advance_account="ADVANCE-RAW")
				for i, company in enumerate(companies, 1)
			],
		)

	def test_duplicate_company_account_error_translates_only_the_party_doctype(self):
		for party_type, account_type in (("Customer", "Receivable"), ("Supplier", "Payable")):
			with self.subTest(party_type=party_type):
				doc = self.account_doc(party_type, ["COMPANY-RAW", "COMPANY-RAW"])
				before = deepcopy(doc.accounts)
				self.db.get_default.return_value = None
				self.db.get_value.return_value = account_type
				with patch(
					"frappe.get_cached_value", side_effect=self.account_cache(account_type)
				) as cache, patch.object(party, "get_party_gle_currency", return_value=None) as currency:
					message = self.assert_labels(
						lambda: party.validate_party_accounts(doc),
						frappe._(party_type),
						"Customer",
						exception=party.DuplicatePartyAccountError,
					)
					currency.assert_called_once_with(party_type, "Customer", "COMPANY-RAW")
					for account in ("ACCOUNT-RAW", "ADVANCE-RAW"):
						self.assertIn(call("Account", account, "company"), cache.call_args_list)
						self.assertIn(call("Account", account, "account_type"), cache.call_args_list)
				self.assertEqual(doc.accounts, before)
				self.assertEqual(doc.doctype, party_type)
				self.assertEqual(doc.name, "Customer")
				self.assertNotIn("Cliente Cliente", message)

	def test_distinct_company_main_and_advance_accounts_keep_their_validation(self):
		for party_type, account_type in (("Customer", "Receivable"), ("Supplier", "Payable")):
			with self.subTest(party_type=party_type):
				doc = self.account_doc(party_type, ["COMPANY-RAW", "OTHER-COMPANY"])
				doc.accounts[1].account = doc.accounts[1].advance_account = None
				before = deepcopy(doc.accounts)
				self.db.get_default.return_value = None
				self.db.get_value.return_value = account_type
				with patch(
					"frappe.get_cached_value", side_effect=self.account_cache(account_type)
				), patch.object(party, "get_party_gle_currency", return_value=None) as currency:
					party.validate_party_accounts(doc)
					self.assertEqual(
						currency.call_args_list,
						[
							call(party_type, "Customer", "COMPANY-RAW"),
							call(party_type, "Customer", "OTHER-COMPANY"),
						],
					)
				self.assertEqual(doc.accounts, before)

	def test_existing_gl_currency_mismatch_uses_neutral_account_instruction(self):
		for party_type in ("Customer", "Supplier", "Shareholder"):
			with self.subTest(party_type=party_type), patch.object(
				party, "get_party_gle_currency", return_value="USD"
			) as existing:
				message = self.assert_labels(
					lambda: party.validate_party_gle_currency(party_type, "Banking", "Customer", "EUR"),
					frappe.bold(frappe._(party_type)),
					frappe.bold("Banking"),
					frappe.bold("Customer"),
					"conto in valuta",
					exception=party.InvalidAccountCurrency,
				)
				existing.assert_called_once_with(party_type, "Banking", "Customer")
				self.assertNotIn("Servizi bancari", message)
				self.assertNotIn("receivable or payable", message)

	def test_existing_gl_currency_matching_missing_and_account_currency_lookup_controls(self):
		for existing_currency in (None, "EUR"):
			with patch.object(
				party, "get_party_gle_currency", return_value=existing_currency
			) as existing, patch.object(party, "get_party_account_currency", return_value="EUR") as account:
				party.validate_party_gle_currency("Customer", "Banking", "COMPANY-RAW")
				existing.assert_called_once_with("Customer", "Banking", "COMPANY-RAW")
				account.assert_called_once_with("Customer", "Banking", "COMPANY-RAW")

	def test_actual_gl_equity_party_flow_reaches_same_currency_guard(self):
		entry = self.make_doc(
			gl_entry.GLEntry,
			doctype="GL Entry",
			company="COMPANY-RAW",
			account="EQUITY-RAW",
			account_currency="EUR",
			party_type="Shareholder",
			party="Banking",
			is_cancelled=0,
		)
		before = dict(entry.__dict__)
		with patch("frappe.get_cached_value", return_value="Equity") as cache, patch.object(
			gl_entry.erpnext, "get_company_currency", return_value="EUR"
		), patch.object(gl_entry, "get_account_currency", return_value="EUR"), patch.object(
			party, "get_party_gle_currency", return_value="USD"
		) as existing:
			entry.validate_party()
			cache.assert_called_once_with("Account", "EQUITY-RAW", "account_type")
			self.assert_labels(
				entry.validate_currency,
				"Azionista",
				"Banking",
				"conto in valuta",
				exception=party.InvalidAccountCurrency,
			)
			existing.assert_called_once_with("Shareholder", "Banking", "COMPANY-RAW")
		self.assertEqual(entry.__dict__, before)

	def link(self, primary_role="Customer", primary_party="Banking", secondary_party="SECONDARY-RAW"):
		return self.make_doc(
			PartyLink,
			doctype="Party Link",
			primary_role=primary_role,
			primary_party=primary_party,
			secondary_role="Supplier" if primary_role == "Customer" else "Customer",
			secondary_party=secondary_party,
		)

	def link_provider(self, records):
		def get_all(doctype, filters, pluck):
			self.assertEqual((doctype, pluck), ("Party Link", "primary_role"))
			return [
				record[pluck]
				for record in records
				if all(record.get(key) == value for key, value in filters.items())
			]

		return get_all

	def test_exact_party_link_conflict_translates_roles_and_preserves_business_names(self):
		for primary_role in ("Customer", "Supplier"):
			with self.subTest(primary_role=primary_role):
				link = self.link(primary_role)
				before = dict(link.__dict__)
				with patch("frappe.get_all", side_effect=self.link_provider([before])) as reads:
					message = self.assert_labels(
						link.validate,
						frappe._(link.primary_role),
						frappe._(link.secondary_role),
						frappe.bold("Banking"),
						frappe.bold("SECONDARY-RAW"),
					)
					reads.assert_called_once_with(
						"Party Link",
						{"primary_party": "Banking", "secondary_party": "SECONDARY-RAW"},
						pluck="primary_role",
					)
				self.assertNotIn("Servizi bancari", message)
				self.assertEqual(link.__dict__, before)

	def test_secondary_party_already_primary_has_complete_neutral_conflict_message(self):
		for primary_role in ("Customer", "Supplier"):
			with self.subTest(primary_role=primary_role):
				link = self.link(primary_role, "NEW-PRIMARY", "Banking")
				record = dict(
					primary_role=link.secondary_role,
					primary_party="Banking",
					secondary_role=primary_role,
					secondary_party="EXISTING-OTHER",
				)
				before = dict(link.__dict__)
				with patch("frappe.get_all", side_effect=self.link_provider([record])) as reads:
					message = self.assert_labels(
						link.validate, frappe._(link.secondary_role), "Banking", "un’altra controparte"
					)
					self.assertEqual(
						reads.call_args_list,
						[
							call(
								"Party Link",
								{"primary_party": "NEW-PRIMARY", "secondary_party": "Banking"},
								pluck="primary_role",
							),
							call("Party Link", {"primary_party": "Banking"}, pluck="primary_role"),
						],
					)
				self.assertNotIn("Servizi bancari", message)
				self.assertEqual(link.__dict__, before)

	def test_primary_party_already_secondary_has_same_neutral_conflict_message(self):
		for primary_role in ("Customer", "Supplier"):
			with self.subTest(primary_role=primary_role):
				link = self.link(primary_role)
				record = dict(
					primary_role=link.secondary_role,
					primary_party="EXISTING-OTHER",
					secondary_role=primary_role,
					secondary_party="Banking",
				)
				before = dict(link.__dict__)
				with patch("frappe.get_all", side_effect=self.link_provider([record])) as reads:
					self.assert_labels(
						link.validate, frappe._(primary_role), "Banking", "un’altra controparte"
					)
					self.assertEqual(
						reads.call_args_list,
						[
							call(
								"Party Link",
								{"primary_party": "Banking", "secondary_party": "SECONDARY-RAW"},
								pluck="primary_role",
							),
							call("Party Link", {"primary_party": "SECONDARY-RAW"}, pluck="primary_role"),
							call("Party Link", {"secondary_party": "Banking"}, pluck="primary_role"),
						],
					)
				self.assertEqual(link.__dict__, before)

	def test_valid_party_links_and_existing_repeat_primary_behavior_stay_unchanged(self):
		for primary_role in ("Customer", "Supplier"):
			link = self.link(primary_role)
			record = dict(
				primary_role=primary_role,
				primary_party="Banking",
				secondary_role=link.secondary_role,
				secondary_party="DIFFERENT-SECONDARY",
			)
			for records in ([], [record]):
				with patch("frappe.get_all", side_effect=self.link_provider(records)) as reads:
					link.validate()
					self.assertEqual(reads.call_count, 3)
		with patch("frappe.get_all") as reads:
			self.assert_labels(lambda: self.link("Employee").validate(), "Allowed primary roles")
			reads.assert_not_called()

	def test_disabled_party_diagnostic_keeps_exception_precedence_and_raw_identity(self):
		for party_type in ("Customer", "Supplier"):
			with self.subTest(party_type=party_type), patch(
				"frappe.get_cached_value", return_value=frappe._dict(disabled=1, is_frozen=1)
			) as cache, patch("frappe.get_roles") as roles:
				message = self.assert_labels(
					lambda: party.validate_party_frozen_disabled("COMPANY-RAW", party_type, "Banking"),
					frappe._(party_type),
					"Banking",
					exception=party.PartyDisabled,
				)
				self.assertNotIn("Servizi bancari", message)
				cache.assert_called_once_with(party_type, "Banking", ["is_frozen", "disabled"], as_dict=True)
				roles.assert_not_called()

	def test_frozen_party_diagnostic_keeps_canonical_role_exemption(self):
		for party_type in ("Customer", "Supplier"):
			with self.subTest(party_type=party_type), patch(
				"frappe.get_cached_value",
				side_effect=[frappe._dict(disabled=0, is_frozen=1), "Accounts Manager"],
			) as cache, patch("frappe.get_roles", return_value=["Other Role"]) as roles:
				self.assert_labels(
					lambda: party.validate_party_frozen_disabled("COMPANY-RAW", party_type, "Banking"),
					frappe._(party_type),
					"Banking",
					exception=party.PartyFrozen,
				)
				self.assertEqual(
					cache.call_args_list,
					[
						call(party_type, "Banking", ["is_frozen", "disabled"], as_dict=True),
						call("Company", "COMPANY-RAW", "role_allowed_for_frozen_entries"),
					],
				)
				roles.assert_called_once_with()

	def test_allowed_frozen_entry_role_remains_exempt(self):
		for party_type in ("Customer", "Supplier"):
			with self.subTest(party_type=party_type), patch(
				"frappe.get_cached_value",
				side_effect=[frappe._dict(disabled=0, is_frozen=1), "Accounts Manager"],
			), patch("frappe.get_roles", return_value=["Accounts Manager"]):
				party.validate_party_frozen_disabled("COMPANY-RAW", party_type, "Banking")

	def test_party_link_name_only_filter_retains_existing_role_collision_behavior(self):
		link = self.link("Customer")
		record = dict(
			primary_role="Supplier",
			primary_party="Banking",
			secondary_role="Customer",
			secondary_party="SECONDARY-RAW",
		)
		with patch("frappe.get_all", side_effect=self.link_provider([record])) as reads:
			with self.assertRaises(frappe.ValidationError):
				link.validate()
			reads.assert_called_once_with(
				"Party Link",
				{"primary_party": "Banking", "secondary_party": "SECONDARY-RAW"},
				pluck="primary_role",
			)

	def test_ignore_flag_unfrozen_and_missing_party_controls(self):
		with patch.dict(frappe.flags, ignore_party_validation=True), patch(
			"frappe.get_cached_value"
		) as cache:
			party.validate_party_frozen_disabled("COMPANY-RAW", "Customer", "Banking")
			cache.assert_not_called()
		with patch(
			"frappe.get_cached_value", return_value=frappe._dict(disabled=0, is_frozen=0)
		) as cache, patch("frappe.get_roles") as roles:
			party.validate_party_frozen_disabled("COMPANY-RAW", "Supplier", "Banking")
			cache.assert_called_once()
			roles.assert_not_called()
		with patch("frappe.get_cached_value") as cache:
			for party_type, party_name in ((None, None), ("Customer", None), (None, "Banking")):
				party.validate_party_frozen_disabled("COMPANY-RAW", party_type, party_name)
			cache.assert_not_called()

	def test_inactive_employee_translates_label_but_remains_an_alert(self):
		self.db.get_value.return_value = "Inactive"
		with patch("frappe.msgprint") as alert:
			party.validate_party_frozen_disabled("COMPANY-RAW", "Employee", "Banking")
			self.db.get_value.assert_called_once_with("Employee", "Banking", "status")
			alert.assert_called_once_with("Dipendente Banking is not active", alert=True)

	def test_active_employee_remains_silent(self):
		self.db.get_value.return_value = "Active"
		with patch("frappe.msgprint") as alert:
			party.validate_party_frozen_disabled("COMPANY-RAW", "Employee", "Banking")
			alert.assert_not_called()

	def extracted_messages(self, module):
		with Path(module.__file__).open("rb") as source:
			return {
				message
				for _, message, _, _ in extract(
					"frappe.gettext.extractors.python.extract", source, keywords=PYTHON_KEYWORDS
				)
			}

	def test_neutral_currency_message_is_really_extracted(self):
		messages = self.extracted_messages(party)
		self.assertIn(CURRENCY_MESSAGE, messages)
		self.assertNotIn(
			"{0} {1} has accounting entries in currency {2} for company {3}. Please select a receivable or payable account with currency {2}.",
			messages,
		)

	def test_neutral_party_link_message_is_really_extracted(self):
		from erpnext.accounts.doctype.party_link import party_link

		messages = self.extracted_messages(party_link)
		self.assertIn(LINK_MESSAGE, messages)
		self.assertNotIn("{0} {1} is already linked with another {2}", messages)
