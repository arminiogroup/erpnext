// Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Service Level Agreement", {
	setup: function (frm) {
		if (cint(frm.doc.apply_sla_for_resolution) === 1) {
			frm.get_field("priorities").grid.editable_fields = [
				{ fieldname: "default_priority", columns: 1 },
				{ fieldname: "priority", columns: 2 },
				{ fieldname: "response_time", columns: 2 },
				{ fieldname: "resolution_time", columns: 2 },
			];
		} else {
			frm.get_field("priorities").grid.editable_fields = [
				{ fieldname: "default_priority", columns: 1 },
				{ fieldname: "priority", columns: 2 },
				{ fieldname: "response_time", columns: 3 },
			];
		}
	},

	refresh: function (frm) {
		frm.trigger("fetch_status_fields");
		frm.trigger("toggle_resolution_fields");
		frm.trigger("default_service_level_agreement");
		frm.trigger("entity");
	},

	default_service_level_agreement: function (frm) {
		const field = frm.get_field("default_service_level_agreement");
		if (frm.doc.default_service_level_agreement) {
			field.set_description(
				__("This SLA is the default for documents of type {0}.", [__(frm.doc.document_type)])
			);
		} else {
			field.set_description(
				__("Enable to make this SLA the default for documents of type {0}.", [
					__(frm.doc.document_type),
				])
			);
		}
	},

	document_type: function (frm) {
		frm.trigger("fetch_status_fields");
		frm.trigger("default_service_level_agreement");
	},

	entity_type: function (frm) {
		frm.set_value("entity", undefined);
	},

	entity: function (frm) {
		const field = frm.get_field("entity");
		if (frm.doc.entity) {
			const description =
				frm.doc.entity_type != "Customer"
					? __(
							"SLA applies to documents of type {0} associated with {1}: {2}, including descendants.",
							[__(frm.doc.document_type), __(frm.doc.entity_type), frm.doc.entity]
					  )
					: __("SLA applies to documents of type {0} associated with {1}: {2}.", [
							__(frm.doc.document_type),
							__(frm.doc.entity_type),
							frm.doc.entity,
					  ]);
			field.set_description(description);
		} else {
			field.set_description("");
		}
	},

	fetch_status_fields: function (frm) {
		let allow_statuses = [];
		let exclude_statuses = [];

		if (frm.doc.document_type) {
			frappe.model.with_doctype(frm.doc.document_type, () => {
				let statuses = frappe.meta.get_docfield(
					frm.doc.document_type,
					"status",
					frm.doc.name
				).options;
				statuses = statuses.split("\n");

				exclude_statuses = ["Open", "Closed"];
				allow_statuses = statuses.filter((status) => !exclude_statuses.includes(status));

				frm.fields_dict.pause_sla_on.grid.update_docfield_property(
					"status",
					"options",
					[""].concat(allow_statuses)
				);

				exclude_statuses = ["Open"];
				allow_statuses = statuses.filter((status) => !exclude_statuses.includes(status));
				frm.fields_dict.sla_fulfilled_on.grid.update_docfield_property(
					"status",
					"options",
					[""].concat(allow_statuses)
				);
			});
		}

		frm.refresh_field("pause_sla_on");
	},

	apply_sla_for_resolution: function (frm) {
		frm.trigger("toggle_resolution_fields");
	},

	toggle_resolution_fields: function (frm) {
		if (cint(frm.doc.apply_sla_for_resolution) === 1) {
			frm.fields_dict.priorities.grid.update_docfield_property("resolution_time", "hidden", 0);
			frm.fields_dict.priorities.grid.update_docfield_property("resolution_time", "reqd", 1);
		} else {
			frm.fields_dict.priorities.grid.update_docfield_property("resolution_time", "hidden", 1);
			frm.fields_dict.priorities.grid.update_docfield_property("resolution_time", "reqd", 0);
		}

		frm.refresh_field("priorities");
	},

	onload: function (frm) {
		frm.set_query("document_type", function () {
			let invalid_doctypes = frappe.model.core_doctypes_list;
			invalid_doctypes.push(frm.doc.doctype, "Cost Center", "Company");

			return {
				filters: [
					["DocType", "issingle", "=", 0],
					["DocType", "istable", "=", 0],
					["DocType", "is_submittable", "=", 0],
					["DocType", "name", "not in", invalid_doctypes],
					[
						"DocType",
						"module",
						"not in",
						[
							"Email",
							"Core",
							"Custom",
							"Event Streaming",
							"Social",
							"Data Migration",
							"Geo",
							"Desk",
						],
					],
				],
			};
		});
	},
});
