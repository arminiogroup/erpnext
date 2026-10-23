# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpnext.accounts.doctype.exchange_rate_revaluation import exchange_rate_revaluation
from erpnext.accounts.doctype.payment_request import payment_request
from erpnext.controllers import selling_controller
from erpnext.manufacturing.doctype.bom import bom


def raise_message(msg=None, **kwargs):
	raise ValueError(msg)


class TestLocalizedValidationMessages(unittest.TestCase):
	def test_selling_price_translates_rate_labels_and_keeps_item_values(self):
		labels = {
			"Last Purchase Rate": "Ultimo prezzo di acquisto",
			"Valuation Rate": "Costo unitario di valorizzazione",
			"Net Rate": "Prezzo netto",
		}

		def translate(message):
			if message.startswith("Row #{0}: Selling rate"):
				return "Riga {0}: {1}; riferimento {2}; {3} minimo {4}; impostazione {5} in {6}."
			return labels.get(message, message)

		for doctype, last_purchase_rate, rate_field, expected_label, expected_rate in (
			("Sales Order", 50, "valuation_rate", "Last Purchase Rate", 100.0),
			("Sales Order", 0, "valuation_rate", "Valuation Rate", 120.0),
			("Delivery Note", 0, "incoming_rate", "Valuation Rate", 120.0),
		):
			with self.subTest(doctype=doctype, reference=expected_label):
				item = frappe._dict(
					idx=1,
					item_code="ITEM-RAW",
					item_name="Customer's item name",
					is_free_item=False,
					base_net_rate=80,
					conversion_factor=2,
					precision=lambda field: 2,
					**{rate_field: 60},
				)
				doc = SimpleNamespace(doctype=doctype, items=[item])
				doc.get = lambda field: getattr(doc, field, None)
				with (
					patch.object(selling_controller, "frappe") as mock_frappe,
					patch.object(selling_controller, "_", side_effect=translate),
					patch.object(selling_controller, "throw", side_effect=raise_message),
					patch.object(selling_controller, "get_link_to_form", return_value="settings-link"),
					patch("frappe.get_system_settings", return_value="Banker's Rounding"),
				):
					mock_frappe.get_single_value.return_value = True
					mock_frappe.get_cached_value.return_value = (last_purchase_rate, True)
					mock_frappe.get_meta.return_value.get_translated_label.return_value = "Controlla prezzo"
					with self.assertRaises(ValueError) as error:
						selling_controller.SellingController.validate_selling_price(doc)

				message = str(error.exception)
				self.assertIn(labels[expected_label], message)
				self.assertIn(labels["Net Rate"], message)
				self.assertIn(str(expected_rate), message)
				self.assertIn(item.item_name, message)
				self.assertNotIn("last purchase rate", message)
				self.assertNotIn("valuation rate", message)
				self.assertNotIn("net rate", message)
				self.assertEqual(item.item_code, "ITEM-RAW")
				self.assertEqual(item.base_net_rate, 80)
				self.assertEqual(item.get(rate_field), 60)

	def test_secondary_item_type_is_translated_only_for_display(self):
		labels = {
			"Scrap": "Scarto",
			"By-Product": "Sottoprodotto",
			"Additional Finished Good": "Prodotto finito aggiuntivo",
		}
		message = "Row #{0}: Process Loss Percentage should be less than 100% for {1} Item {2}"
		for item_type, translated_type in labels.items():
			with self.subTest(item_type=item_type):
				item = frappe._dict(
					idx=1,
					item_code="SECONDARY-RAW",
					secondary_item_type=item_type,
					valuation_type="Manual",
					process_loss_per=100,
				)
				doc = frappe._dict(item="FINISHED-RAW", secondary_items=[item])
				translations = {
					**labels,
					message: "Riga {0}: perdita inferiore al 100% per {1}; articolo {2}.",
				}
				with (
					patch.object(bom, "_", side_effect=lambda text: translations.get(text, text)),
					patch.object(bom, "frappe") as mock_frappe,
					patch.object(bom, "get_link_to_form", return_value="item-link") as item_link,
				):
					mock_frappe.throw.side_effect = raise_message
					with self.assertRaises(ValueError) as error:
						bom.BOM.validate_secondary_items(doc)

				self.assertIn(translated_type, str(error.exception))
				self.assertIn("item-link", str(error.exception))
				item_link.assert_called_once_with("Item", "SECONDARY-RAW")
				self.assertEqual(item.secondary_item_type, item_type)
				self.assertEqual(item.item_code, "SECONDARY-RAW")
				self.assertEqual(item.process_loss_per, 100)

	def test_existing_draft_reversals_use_a_complete_message_and_preserve_links(self):
		for drafts in (["JV-1"], ["JV-1", "JV-2"]):
			with self.subTest(drafts=drafts):
				query = MagicMock()
				query.join.return_value.on.return_value.select.return_value.distinct.return_value.where.return_value.run.return_value = [
					"POSTED-JV"
				]
				translations = {"Existing draft reversals: {0}": "Storni già presenti in bozza: {0}"}
				with (
					patch.object(exchange_rate_revaluation, "frappe") as mock_frappe,
					patch.object(exchange_rate_revaluation, "qb", new=MagicMock()) as mock_qb,
					patch.object(
						exchange_rate_revaluation, "_", side_effect=lambda text: translations.get(text, text)
					),
					patch.object(
						exchange_rate_revaluation,
						"get_link_to_form",
						side_effect=lambda doctype, name: f"<a>{name}</a>",
					),
					patch(
						"erpnext.accounts.doctype.journal_entry.mapper.make_reverse_journal_entry"
					) as make_reverse,
				):
					mock_qb.from_.return_value = query
					mock_frappe.db.get_all.return_value = drafts
					mock_frappe.throw.side_effect = raise_message
					with self.assertRaises(ValueError) as error:
						exchange_rate_revaluation.ExchangeRateRevaluation.make_reverse_journal(
							frappe._dict(name="REVALUATION-RAW")
						)

					self.assertEqual(
						str(error.exception),
						"Storni già presenti in bozza: " + ", ".join(f"<a>{name}</a>" for name in drafts),
					)
					mock_frappe.has_permission.assert_called_once_with("Journal Entry", "write", throw=True)
					mock_frappe.db.get_all.assert_called_once_with(
						"Journal Entry",
						filters={"docstatus": 0, "reversal_of": ["in", ["POSTED-JV"]]},
						pluck="name",
						as_list=1,
					)
					make_reverse.assert_not_called()

	def test_duplicate_payment_schedule_rows_are_translated_with_original_values(self):
		row_message = "Payment Term: {0}, Due Date: {1}, Amount: {2} (already requested in PR {3})"
		translations = {
			row_message: "Condizione: {0}, scadenza: {1}, importo: {2} (già richiesto in {3})",
			"The following payment schedule(s) already exist:\n{0}": "Scadenze già presenti:\n{0}",
		}
		rows = [
			{
				"name": "SCHEDULE-1",
				"payment_term": "Custom term A",
				"due_date": "2026-10-01",
				"payment_amount": 60.5,
			},
			{
				"name": "SCHEDULE-2",
				"payment_term": "Custom term B",
				"due_date": "2026-11-01",
				"payment_amount": 40,
			},
			{
				"name": "SCHEDULE-NEW",
				"payment_term": "New term",
				"due_date": "2026-12-01",
				"payment_amount": 10,
			},
		]
		for duplicate_count in (1, 2):
			with self.subTest(duplicate_count=duplicate_count):
				references = [
					{"payment_schedule": row["name"], "parent": f"PR-RAW-{index}"}
					for index, row in enumerate(rows[:duplicate_count], start=1)
				]
				with (
					patch.object(payment_request, "frappe") as mock_frappe,
					patch.object(payment_request, "_", side_effect=lambda text: translations.get(text, text)),
					patch.object(payment_request, "get_gateway_details", return_value=None),
					patch.object(payment_request, "get_existing_payment_entry", return_value=False),
					patch.object(payment_request, "get_existing_payment_references", return_value=references),
					patch.object(payment_request, "set_payment_references") as set_references,
				):
					mock_frappe._dict.side_effect = frappe._dict
					mock_frappe.parse_json.side_effect = lambda value: value
					mock_frappe.throw.side_effect = raise_message
					with self.assertRaises(ValueError) as error:
						payment_request.make_payment_request(
							dt="Sales Order",
							dn="SO-RAW",
							company="Company RAW",
							ref_doc=frappe._dict(name="SO-RAW", company="Company RAW"),
							schedules=rows,
						)
					expected_rows = [
						translations[row_message].format(
							row["payment_term"], row["due_date"], row["payment_amount"], reference["parent"]
						)
						for row, reference in zip(rows, references, strict=False)
					]
					self.assertEqual(
						str(error.exception), "Scadenze già presenti:\n" + "\n".join(expected_rows)
					)
					set_references.assert_not_called()
					self.assertEqual(rows[-1]["name"], "SCHEDULE-NEW")
