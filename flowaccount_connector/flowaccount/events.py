"""Quotation -> FlowAccount push, triggered on submit (see hooks.py).

Runs in a background job so a slow/failed API call never blocks submit.
Idempotent: a quotation already carrying a FlowAccount id is skipped.
"""

import frappe
from flowaccount_connector.flowaccount import client, mapping


def on_quotation_submit(doc, method=None):
    if not frappe.conf.get("flowaccount_enabled"):
        return
    # Kill-switch for UAT/staging sites that share production credentials:
    # lets the read-only pull run while blocking outbound document creation.
    if frappe.conf.get("flowaccount_push_disabled"):
        return
    if doc.get("flowaccount_document_id"):
        return
    frappe.enqueue(
        "flowaccount_connector.flowaccount.events.push_quotation",
        queue="short",
        quotation_name=doc.name,
    )


def resolve_account(doc):
    """Pick which FlowAccount account receives this quotation.

    Explicit choice on the form wins; Auto routes VAT documents to the
    company account and no-VAT (B2C) ones to the shop account when its
    credentials are configured.
    """
    configured = client.accounts()
    choice = doc.get("flowaccount_entity") or "Auto"
    if choice == "Company":
        return "company"
    if choice == "Shop":
        return "shop" if "shop" in configured else "company"
    has_vat = bool(doc.get("total_taxes_and_charges"))
    if not has_vat and "shop" in configured:
        return "shop"
    return "company"


def push_quotation(quotation_name):
    doc = frappe.get_doc("Quotation", quotation_name)
    payload = mapping.quotation_to_payload(doc)
    result = client.create_quotation(payload, account=resolve_account(doc))

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
