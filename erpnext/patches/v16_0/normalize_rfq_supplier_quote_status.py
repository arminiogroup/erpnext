from collections import defaultdict

import frappe


def execute():
	rfq = frappe.qb.DocType("Request for Quotation")
	supplier = frappe.qb.DocType("Request for Quotation Supplier")
	rfq_item = frappe.qb.DocType("Request for Quotation Item")
	quotation = frappe.qb.DocType("Supplier Quotation")
	quotation_item = frappe.qb.DocType("Supplier Quotation Item")

	while rows := (
		frappe.qb.from_(supplier)
		.join(rfq)
		.on(supplier.parent == rfq.name)
		.select(supplier.name, supplier.parent, supplier.supplier)
		.where(supplier.parenttype == "Request for Quotation")
		.where(supplier.parentfield == "suppliers")
		.where(supplier.quote_status.notin(["Pending", "Received", ""]))
		.orderby(supplier.name)
		.limit(1000)
	).run(as_dict=True):
		items = (
			frappe.qb.from_(rfq_item)
			.select(rfq_item.name, rfq_item.parent)
			.where(rfq_item.parent.isin({row.parent for row in rows}))
			.where(rfq_item.parenttype == "Request for Quotation")
			.where(rfq_item.parentfield == "items")
		).run(as_dict=True)
		item_names = {item.name for item in items}
		coverage = set()
		if item_names:
			quoted_items = (
				frappe.qb.from_(quotation_item)
				.join(quotation)
				.on(quotation_item.parent == quotation.name)
				.select(quotation.supplier, quotation_item.request_for_quotation_item)
				.where(quotation_item.docstatus == 1)
				.where(quotation_item.request_for_quotation_item.isin(item_names))
				.where(quotation.supplier.isin({row.supplier for row in rows}))
				.distinct()
			).run(as_dict=True)
			coverage = {(item.supplier, item.request_for_quotation_item) for item in quoted_items}

		items_by_parent = defaultdict(list)
		for item in items:
			items_by_parent[item.parent].append(item.name)
		frappe.db.bulk_update(
			"Request for Quotation Supplier",
			{
				row.name: {
					"quote_status": "Received"
					if all((row.supplier, item) in coverage for item in items_by_parent[row.parent])
					else "Pending"
				}
				for row in rows
			},
			update_modified=False,
		)
