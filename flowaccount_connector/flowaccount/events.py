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
    """Pick which FlowAccount account receives this quotation, or None to
    keep it in ERPNext only.

    Business rule (decided 2026-06): B2B/VAT documents live in FlowAccount
    (company account); B2C/no-VAT documents are issued natively in ERPNext
    and are NOT pushed. Explicit choice on the form always wins.
    """
    configured = client.accounts()
    choice = doc.get("flowaccount_entity") or "Auto"
    if choice == "ERPNext Only":
        return None
    if choice == "Company":
        return "company"
    if choice == "Shop":
        return "shop" if "shop" in configured else None
    has_vat = bool(doc.get("total_taxes_and_charges"))
    if has_vat:
        return "company"
    return "shop" if "shop" in configured else None


def push_quotation(quotation_name):
    doc = frappe.get_doc("Quotation", quotation_name)
    account = resolve_account(doc)
    if not account:
        return
    payload = mapping.quotation_to_payload(doc)
    result = client.create_quotation(payload, account=account)

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


def on_item_save(doc, method=None):
    """Item created in ERPNext -> create it in FlowAccount so accountants can
    pick it on documents there. One-way, create-once: items that already
    carry a FlowAccount id (pulled or previously pushed) are skipped."""
    if not frappe.conf.get("flowaccount_enabled"):
        return
    if frappe.conf.get("flowaccount_push_disabled"):
        return
    if doc.get("flowaccount_product_id"):
        return
    if doc.get("disabled"):
        return
    frappe.enqueue(
        "flowaccount_connector.flowaccount.events.push_item",
        queue="short",
        item_code=doc.name,
    )


def push_item(item_code):
    doc = frappe.get_doc("Item", item_code)
    if doc.get("flowaccount_product_id"):
        return

    # Always non-inventory (type 3) on purpose: stock has exactly one owner,
    # ERPNext. FlowAccount must never run its own parallel stock count.
    payload = {
        "type": 3,
        "code": doc.item_code,
        "name": doc.item_name or doc.item_code,
        "sellDescription": doc.description or "",
        "unitName": doc.stock_uom or "",
        "sellPrice": float(doc.get("standard_rate") or 0),
    }
    result = client._request("POST", "/products", json_body=payload)

    data = (result or {}).get("data") or {}
    fa_id = data.get("id")
    if fa_id:
        frappe.db.set_value(
            "Item", item_code,
            {"flowaccount_product_id": str(fa_id)},
            update_modified=False,
        )
        frappe.db.commit()
