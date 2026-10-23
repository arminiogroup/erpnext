from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests import UnitTestCase

from erpnext.setup.doctype.authorization_rule.authorization_rule import AuthorizationRule


class TestAuthorizationRuleMessages(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		for name in ("lang", "message_log", "response"):
			self.addCleanup(setattr, frappe.local, name, getattr(frappe.local, name))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.message_log = []
		frappe.local.response = frappe._dict()
		self.enterContext(patch.dict(frappe.flags, {"mute_messages": False, "print_messages": False}))
		self.unsupported = "Cannot set authorization on basis of Discount for {0}"
		self.percentage_limit = "Discount must be between 0 and 100"
		self.translations = {
			self.unsupported: "Non è possibile impostare un’autorizzazione basata sullo sconto per il tipo di documento «{0}».",
			self.percentage_limit: "Lo sconto deve essere compreso tra 0 e 100.",
			"Purchase Order": "Ordine di acquisto",
			"Purchase Receipt": "Ricevimento di acquisto",
			"Purchase Invoice": "Fattura di acquisto",
			"Stock Entry": "Movimento di magazzino",
			"Rate": "Costo unitario",
		}
		self.enterContext(
			patch(
				"frappe.translate.get_all_translations",
				side_effect=lambda lang: self.translations if lang == "it" else {},
			)
		)
		self.enterContext(patch("frappe.db", SimpleNamespace()))
		self.enterContext(
			patch(
				"frappe.get_system_settings",
				side_effect=lambda field: {"rounding_method": "Banker's Rounding", "time_zone": "UTC"}.get(
					field
				),
			)
		)

	def make_rule(self, **values):
		self.assertEqual(frappe.session.user, "translator@example.com")
		rule = AuthorizationRule.__new__(AuthorizationRule)
		rule.__dict__.update(
			doctype="Authorization Rule",
			name="AUTH-MEMORY",
			approving_role="Rate",
			approving_user=None,
			system_role=None,
			system_user=None,
			to_emp=None,
			to_designation=None,
			transaction="Sales Order",
			based_on="Average Discount",
			value=100,
			master_name=None,
			company=None,
		)
		rule.__dict__.update(values)
		return rule

	def expected(self, source, *values):
		message = self.translations.get(source, source) if frappe.local.lang == "it" else source
		return message.format(*values)

	def test_discount_restriction_translates_document_types_without_changing_selectors(self):
		for lang in ("it", "en"):
			for transaction in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Stock Entry"):
				for basis in (
					"Average Discount",
					"Customerwise Discount",
					"Itemwise Discount",
					"Item Group wise Discount",
				):
					with self.subTest(lang=lang, transaction=transaction, basis=basis):
						frappe.local.lang = lang
						rule = self.make_rule(transaction=transaction, based_on=basis, value=101)
						with self.assertRaises(frappe.ValidationError) as error:
							rule.validate_rule()
						self.assertIs(type(error.exception), frappe.ValidationError)
						display = self.translations[transaction] if lang == "it" else transaction
						self.assertEqual(str(error.exception), self.expected(self.unsupported, display))
						self.assertEqual(
							(rule.transaction, rule.based_on, rule.value, rule.approving_role),
							(transaction, basis, 101, "Rate"),
						)

	def test_all_discount_bases_accept_inclusive_percent_range_without_changing_values(self):
		for lang in ("it", "en"):
			for basis in (
				"Average Discount",
				"Customerwise Discount",
				"Itemwise Discount",
				"Item Group wise Discount",
			):
				for value in (0, 99.99, 100, 100.01, -1, None, "100"):
					with self.subTest(lang=lang, basis=basis, value=value):
						frappe.local.lang = lang
						rule = self.make_rule(based_on=basis, value=value, master_name="Rate")
						if value in (100.01, -1):
							with self.assertRaises(frappe.ValidationError) as error:
								rule.validate_rule()
							self.assertIs(type(error.exception), frappe.ValidationError)
							self.assertEqual(str(error.exception), self.expected(self.percentage_limit))
						else:
							self.assertIsNone(rule.validate_rule())
						self.assertEqual(
							(rule.based_on, rule.value, rule.master_name), (basis, value, "Rate")
						)
		rule = self.make_rule(based_on="Grand Total", value=101)
		self.assertIsNone(rule.validate_rule())
		self.assertEqual(rule.value, 101)

	def test_actual_validate_preserves_duplicate_query_and_value_normalization(self):
		cases = (
			(None, "Sales Order"),
			(100, "Sales Order"),
			(100.01, "Sales Order"),
			(-1, "Sales Order"),
			(100, "Purchase Order"),
		)
		for value, transaction in cases:
			with self.subTest(value=value, transaction=transaction):
				rule = self.make_rule(value=value, transaction=transaction)
				query = Mock(return_value=[])
				with patch("frappe.get_all", query):
					if value in (100.01, -1) or transaction == "Purchase Order":
						with self.assertRaises(frappe.ValidationError) as error:
							rule.validate()
						expected = (
							self.expected(self.percentage_limit)
							if value in (100.01, -1)
							else self.expected(self.unsupported, self.translations[transaction])
						)
						self.assertEqual(str(error.exception), expected)
					else:
						self.assertIsNone(rule.validate())
				self.assertEqual(rule.value, value or 0.0)
				if value in (100.01, -1) or transaction == "Purchase Order":
					query.assert_not_called()
				else:
					query.assert_called_once_with(
						"Authorization Rule",
						filters={
							"transaction": transaction,
							"based_on": "Average Discount",
							"system_user": "",
							"system_role": "",
							"approving_user": "",
							"approving_role": "Rate",
							"to_emp": "",
							"to_designation": "",
							"master_name": "",
							"company": "",
							"value": value or 0,
							"name": ["!=", "AUTH-MEMORY"],
						},
						pluck="name",
					)
		rule = self.make_rule(value=100)
		with patch("frappe.get_all", return_value=["RULE-EXISTING"]):
			with self.assertRaises(frappe.ValidationError) as error:
				rule.validate()
		self.assertEqual(
			str(error.exception), "Duplicate Entry. Please check Authorization Rule RULE-EXISTING"
		)

	def test_earlier_user_role_and_master_guards_keep_their_existing_priority(self):
		cases = (
			(
				{"approving_role": None, "approving_user": None},
				"Please enter Approving Role or Approving User",
			),
			(
				{"system_user": "person@example.com", "approving_user": "person@example.com"},
				"Approving User cannot be same as user the rule is Applicable To",
			),
			({"system_role": "Rate"}, "Approving Role cannot be same as role the rule is Applicable To"),
		)
		for values, expected in cases:
			with self.subTest(values=values):
				rule = self.make_rule(transaction="Purchase Order", value=101, **values)
				with self.assertRaises(frappe.ValidationError) as error:
					rule.validate_rule()
				self.assertEqual(str(error.exception), expected)
		rule = self.make_rule(based_on="Customerwise Discount", master_name=None)
		with self.assertRaises(frappe.ValidationError) as error:
			rule.validate_rule()
		self.assertEqual(str(error.exception), "Customer required for 'Customerwise Discount'")
		rule.master_name = "Rate"
		self.assertIsNone(rule.validate_rule())
		self.assertEqual((rule.master_name, rule.approving_role), ("Rate", "Rate"))
