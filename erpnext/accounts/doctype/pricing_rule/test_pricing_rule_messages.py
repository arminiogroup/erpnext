import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from babel.messages.extract import extract
from frappe.gettext.translate import PYTHON_KEYWORDS
from frappe.model.meta import Meta
from frappe.tests import UnitTestCase
from frappe.utils.formatters import format_value

from erpnext.accounts.doctype.pricing_rule.pricing_rule import PricingRule


class TestPricingRuleMessages(UnitTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, frappe.session.user)
		for name in ("lang", "message_log", "response"):
			self.addCleanup(setattr, frappe.local, name, getattr(frappe.local, name))
		frappe.set_user("translator@example.com")
		frappe.local.lang = "it"
		frappe.local.message_log = []
		frappe.local.response = frappe._dict()
		self.enterContext(patch.dict(frappe.flags, {"mute_messages": False, "print_messages": False}))
		metadata = json.loads(
			Path(
				frappe.get_app_path("erpnext", "accounts", "doctype", "pricing_rule", "pricing_rule.json")
			).read_text()
		)
		self.meta = Meta.__new__(Meta)
		self.meta.__dict__.update(
			name=metadata["name"],
			_fields={field["fieldname"]: frappe._dict(field) for field in metadata["fields"]},
		)
		with Path(frappe.get_app_path("erpnext", "gettext", "contextual_messages.py")).open("rb") as file:
			self.markers = {
				(message, context)
				for _line, message, _comments, context in extract(
					"frappe.gettext.extractors.python.extract", file, keywords=PYTHON_KEYWORDS
				)
			}
		self.missing_table = "{0} is not added in the table"
		self.other_required = "For the 'Apply Rule On Other' condition the field {0} is mandatory"
		self.recursion_limit = "Recurse Over Qty cannot exceed Min Qty."
		self.negative_rate = "{0} can not be negative"
		self.translations = {
			self.missing_table: "Inserire almeno una riga per «{0}» nella tabella.",
			self.other_required: "Per la condizione «Applica regola su altri articoli» il campo {0} è obbligatorio",
			self.recursion_limit: "La quantità esclusa dal calcolo ripetuto degli articoli promozionali non può superare la quantità minima.",
			self.negative_rate: "Il valore del campo «{0}» non può essere negativo.",
			"Item Code": "Codice articolo",
			"Item Group": "Gruppo di articoli",
			"Brand": "Marca",
			"Rate": "Costo unitario",
			"Message:Default title of the message dialog": "Messaggio",
		}
		if ("Rate", "Pricing Rule") in self.markers:
			self.translations["Rate:Pricing Rule"] = "Prezzo"
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
		rule = PricingRule.__new__(PricingRule)
		rule.__dict__.update(
			doctype="Pricing Rule",
			name="RULE-MEMORY",
			meta=self.meta,
			has_priority=0,
			priority=None,
			apply_on="Transaction",
			items=[],
			item_groups=[],
			brands=[],
			applicable_for="",
			apply_rule_on_other="",
			price_or_product_discount="Price",
			rate_or_discount="Rate",
			apply_discount_on_rate=0,
			rate=0,
			free_item="",
			same_item=0,
			mixed_conditions=0,
			is_recursive=0,
			apply_recursion_over=0,
			min_qty=0,
			recurse_for=0,
		)
		rule.__dict__.update(values)
		return rule

	def expected(self, source, *values):
		message = self.translations.get(source, source) if frappe.local.lang == "it" else source
		return message.format(*values)

	def test_missing_tables_translate_only_canonical_apply_on_labels(self):
		for lang in ("it", "en"):
			for label, table, field in (
				("Item Code", "items", "item_code"),
				("Item Group", "item_groups", "item_group"),
				("Brand", "brands", "brand"),
			):
				with self.subTest(lang=lang, label=label):
					frappe.local.lang = lang
					rule = self.make_rule(apply_on=label)
					with self.assertRaises(frappe.MandatoryError) as error:
						rule.validate()
					self.assertIs(type(error.exception), frappe.MandatoryError)
					display = self.translations[label] if lang == "it" else label
					self.assertEqual(str(error.exception), self.expected(self.missing_table, display))
					self.assertEqual(rule.apply_on, label)
					rule.__dict__[table] = [frappe._dict({field: "Rate"})]
					self.assertIsNone(rule.validate_mandatory())
					self.assertEqual(rule.get(table)[0].get(field), "Rate")

	def test_other_rule_target_uses_translated_label_and_keeps_business_values(self):
		for lang in ("it", "en"):
			for label, field in (
				("Item Code", "other_item_code"),
				("Item Group", "other_item_group"),
				("Brand", "other_brand"),
			):
				with self.subTest(lang=lang, label=label):
					frappe.local.lang = lang
					rule = self.make_rule(apply_rule_on_other=label)
					with self.assertRaises(frappe.ValidationError) as error:
						rule.validate()
					self.assertIs(type(error.exception), frappe.ValidationError)
					display = self.translations[label] if lang == "it" else label
					self.assertEqual(
						str(error.exception), self.expected(self.other_required, frappe.bold(display))
					)
					rule.__dict__[field] = "Rate"
					self.assertIsNone(rule.validate_mandatory())
					self.assertEqual(rule.apply_rule_on_other, label)
					self.assertEqual(rule.get(field), "Rate")

	def test_recursion_inclusive_limit_preserves_normalization_and_other_gates(self):
		for lang in ("it", "en"):
			frappe.local.lang = lang
			for threshold in (4, 5, 5.01):
				with self.subTest(lang=lang, threshold=threshold):
					rule = self.make_rule(
						price_or_product_discount="Product",
						is_recursive=1,
						min_qty=5,
						apply_recursion_over=threshold,
						free_item="ITEM-MEMORY",
					)
					if threshold > 5:
						with self.assertRaises(frappe.ValidationError) as error:
							rule.validate_recursion()
						self.assertEqual(str(error.exception), self.expected(self.recursion_limit))
					else:
						self.assertIsNone(rule.validate_recursion())
					self.assertEqual(rule.recurse_for, 1)
					self.assertEqual((rule.min_qty, rule.apply_recursion_over), (5, threshold))
		for kind, recursive, expected_recurse in (("Price", 1, 0), ("Product", 0, 1)):
			rule = self.make_rule(
				price_or_product_discount=kind,
				is_recursive=recursive,
				min_qty=5,
				apply_recursion_over=6,
				free_item="ITEM-MEMORY",
			)
			self.assertIsNone(rule.validate_recursion())
			self.assertEqual(rule.recurse_for, expected_recurse)
		rule = self.make_rule(
			price_or_product_discount="Product", is_recursive=1, min_qty=5, apply_recursion_over=-1
		)
		with self.assertRaises(frappe.ValidationError) as error:
			rule.validate_recursion()
		self.assertEqual(str(error.exception), "Recurse Over Qty cannot be less than 0")

	def test_negative_rate_uses_actual_metadata_label_and_preserves_other_decisions(self):
		for label in ("Rate", "Prezzo personalizzato"):
			with self.subTest(label=label), patch.dict(self.meta._fields["rate"], {"label": label}):
				rule = self.make_rule(rate=-0.01)
				with self.assertRaises(frappe.ValidationError) as error:
					rule.validate_rate_or_discount()
				self.assertIs(type(error.exception), frappe.ValidationError)
				self.assertEqual(
					str(error.exception),
					self.expected(self.negative_rate, "Prezzo" if label == "Rate" else label),
				)
				self.assertEqual(rule.rate, -0.01)
		for rate in (0, 1, None):
			rule = self.make_rule(rate=rate)
			self.assertIsNone(rule.validate_rate_or_discount())
			self.assertEqual(rule.rate, rate)
		rule = self.make_rule(price_or_product_discount="Product")
		self.assertIsNone(rule.validate_rate_or_discount())
		self.assertEqual(rule.same_item, 1)
		rule = self.make_rule(price_or_product_discount="Product", mixed_conditions=1)
		with self.assertRaises(frappe.ValidationError) as error:
			rule.validate_rate_or_discount()
		self.assertEqual(str(error.exception), "Free item code is not selected")

	def test_extracted_price_context_reaches_actual_metadata_and_select_display(self):
		self.assertIn(("Rate", "Pricing Rule"), self.markers)
		self.assertEqual(self.meta.get_translated_label("rate"), "Prezzo")
		field = self.meta.get_field("rate_or_discount")
		before_options = field.options
		self.assertEqual(format_value("Rate", frappe._dict(field, parent="Pricing Rule")), "Prezzo")
		self.assertEqual(format_value("Rate", frappe._dict(field, parent="Other Parent")), "Costo unitario")
		self.assertEqual(self.meta.get_label("rate"), "Rate")
		self.assertEqual(field.options, before_options)
		self.assertIn("Rate", field.options.split("\n"))
