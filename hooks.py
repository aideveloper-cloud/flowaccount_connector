"""Quotation -> FlowAccount push, triggered on submit (see hooks.py).

Runs in a background job so a slow/failed API call never blocks submit.
Idempotent: a quotation already carrying a FlowAccount id is skipped.
"""

import frappe
from flowaccount_connector.flowaccount import client, mapping


def on_quotation_submit(doc, method=None):
    if not frappe.conf.get("flowaccount_enabled"):
        return
    if doc.get("flowaccount_document_id"):
        return
    frappe.enqueue(
        "flowaccount_connector.flowaccount.events.push_quotation",
        queue="short",
        quotation_name=doc.name,
    )


def push_quotation(quotation_name):
    doc = frappe.get_doc("Quotation", quotation_name)
    payload = mapping.quotation_to_payload(doc)
    result = client.create_quotation(payload)

    data = (result or {}).get("data") or {}
    fa_id = data.get("recordId") or data.get("documentSerial") or data.get("id")

    if fa_id:
        frappe.db.set_value(
            "Quotation", quotation_name,
            {
                "flowaccount_document_id": str(fa_id),
                "flowaccount_synced": 1,
            },
            update_modified=False,
        )
        frappe.db.commit()
