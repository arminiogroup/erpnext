import unittest
from io import BytesIO

from babel.messages.extract import extract
from babel.util import pathmatch
from frappe.gettext.translate import get_method_map


class TestBankingJavaScriptExtraction(unittest.TestCase):
	def method_for(self, filename):
		return next(
			(
				method
				for pattern, method in get_method_map("erpnext") + get_method_map("frappe")
				if pathmatch(pattern, filename)
			),
			None,
		)

	def messages(self, code, filename="banking/src/components/Balance.tsx"):
		return list(extract(self.method_for(filename), BytesIO(code.encode())))

	def test_mapping_is_bounded_to_banking_source_and_distinguishes_typescript(self):
		self.assertEqual(
			self.method_for("banking/src/components/Balance.tsx"),
			"erpnext.gettext.extractors.javascript.extract",
		)
		self.assertEqual(
			self.method_for("banking/src/lib/date.ts"),
			"erpnext.gettext.extractors.javascript.extract_typescript",
		)
		self.assertIsNone(self.method_for("other_app/translate.tsx"))
		self.assertEqual(
			self.method_for("ui/src/utils/translate.ts"),
			"frappe.gettext.extractors.javascript.extract_typescript",
		)

	def test_real_date_format_replacements_are_not_translation_contexts(self):
		cases = (
			("As of {0}", "[formatDate(data?.message?.date ?? '', 'Do MMM YYYY')]"),
			("Balances as per bank statement before {0}", "[formatDate(date, 'Do MMM YYYY')]"),
			("Closing Balance as of {}", '[formatDate(data.doc.end_date, "Do MMMM YYYY")]'),
			("Closing balance on bank statement as of {0}", "[formatDate(date, 'Do MMM YYYY')]"),
			(
				"Enter the closing balance you see in your bank statement for {0} as of the {1}",
				"[bankAccount?.account_name ?? bankAccount?.name ?? '', formatDate(date, 'Do MMM YYYY')]",
			),
			(
				"{0} to {1}",
				'[formatDate(data.doc.start_date, "Do MMMM YYYY"), formatDate(data.doc.end_date, "Do MMMM YYYY")]',
			),
		)
		for message, replacements in cases:
			with self.subTest(message=message):
				self.assertEqual(self.messages(f'_("{message}", {replacements})'), [(1, message, [], None)])

	def test_explicit_third_context_and_nested_translations_are_preserved(self):
		for function in ("_", "__"):
			with self.subTest(function=function):
				code = f'{function}("Value {{0}}", [formatDate(date, "Do MMM YYYY")], "Bank balance")'
				self.assertEqual(self.messages(code), [(1, "Value {0}", [], "Bank balance")])
		self.assertEqual(
			self.messages(
				'_("Outer {0}", [_("Inner"), format({items: [row]}), "replacement"], "Outer context")'
			),
			[(1, "Inner", [], None), (1, "Outer {0}", [], "Outer context")],
		)

	def test_typescript_generics_regex_and_tsx_comments_do_not_hide_or_invent_messages(self):
		code = """const identity = <T>(value: T): T => value;
const escaped = value.replace(/"/g, "&quot;");
// _("Comment")
const example = "_('Example')";
const matcher = /_\\("Regex"\\)/;
const label = _("Visible");"""
		self.assertEqual(self.messages(code, "banking/src/lib/helper.ts"), [(6, "Visible", [], None)])
		code = """<div title="_('Attribute example')">
{/* _("Commented JSX") */}
<span>{_("Visible JSX")}</span>
</div>"""
		self.assertEqual(self.messages(code), [(3, "Visible JSX", [], None)])

	def test_literal_escapes_and_current_technical_filter_are_preserved(self):
		self.assertEqual(self.messages(r"""_("Caff\u00e8", [], "Caf\u00e8")"""), [(1, "Caffè", [], "Cafè")])
		code = """_("0.00"); _("{} :"); _("12px"); _("eval:field"); _("fa fa-icon"); _("ID");"""
		self.assertEqual(self.messages(code), [(1, "ID", [], None)])
		self.assertEqual(
			self.messages('_(row.label); _(`Dynamic ${value}`); _("After");'), [(1, "After", [], None)]
		)

	def test_python_and_jinja_context_routing_remains_unchanged(self):
		self.assertEqual(
			self.messages('_("State", context="Account")', "banking/helper.py"),
			[(1, "State", [], "Account")],
		)
		self.assertEqual(
			self.messages('{{ _("State", context="Account") }}', "banking/template.html"),
			[(1, "State", [], "Account")],
		)
