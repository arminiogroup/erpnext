from frappe.gettext.extractors.javascript import extract_javascript
from frappe.gettext.extractors.utils import is_translatable


def extract(fileobj, keywords, comment_tags, options):
	yield from _extract(fileobj, {**options, "jsx": True})


def extract_typescript(fileobj, keywords, comment_tags, options):
	yield from _extract(fileobj, {**options, "jsx": False})


def _extract(fileobj, options):
	code = fileobj.read().decode("utf-8")
	for line, _function, messages in extract_javascript(code, keywords=("_", "__"), options=options):
		if isinstance(messages, tuple):
			message = messages[0]
			context = messages[2] if len(messages) == 3 else None
		else:
			message = messages
			context = None
		if not message or not is_translatable(message):
			continue
		if context:
			yield line, "pgettext", (context, message), []
		else:
			yield line, "gettext", message, []
