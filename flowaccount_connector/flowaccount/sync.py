"""Pull FlowAccount data into ERPNext (hourly scheduled job, see hooks.py).

Ownership rules — each data type syncs in ONE direction only:
  - Contacts  -> upserted as real Customer records (ERPNext is the company master DB)
  - Products  -> upserted as real Item records (non-stock, no inventory side effects)
  - Sale documents issued in FlowAccount -> mirrored read-only into the
    "FlowAccount Document" doctype. They are NOT created as Sales Invoices,
    otherwise ERPNext would post GL entries that duplicate FlowAccount's books.
  - Quotations born in ERPNext keep pushing outward via events.py and are
    excluded from the pull to avoid echo loops.

Site Config keys:
  flowaccount_pull_enabled -> 1 to turn the pull on (off by default)
  flowaccount_pull_pages   -> pages fetched per endpoint per run (default 5,
                              50 records/page; raise temporarily for backfill)
"""

import frappe
from flowaccount_connector.flowaccount import client

DOCUMENT_ENDPOINTS = {
    "billing-note": "/billing-notes",
    "invoice": "/invoices",
    "tax-invoice": "/tax-invoices",
    "receipt": "/receipts",
}


def pull_all():
    if not frappe.conf.get("flowaccount_enabled"):
        return
    if not frappe.conf.get("flowaccount_pull_enabled"):
        return

    max_pages = int(frappe.conf.get("flowaccount_pull_pages") or 5)

    for label, fn in (
        ("contacts", lambda: pull_contacts(max_pages)),
        ("products", lambda: pull_products(max_pages)),
        ("documents", lambda: pull_documents(max_pages)),
    ):
        try:
            fn()
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title=f"FlowAccount pull failed: {label}",
                message=frappe.get_traceback(),
            )


def pull_contacts(max_pages=5):
    for contact in client.iter_list("/contacts", max_pages=max_pages):
        _upsert_customer(contact)


def pull_products(max_pages=5):
    for product in client.iter_list("/products", max_pages=max_pages):
        _upsert_item(product)


def pull_documents(max_pages=5):
    for doc_type, path in DOCUMENT_ENDPOINTS.items():
        for record in client.iter_list(path, max_pages=max_pages):
            _upsert_document(doc_type, record)


def _upsert_customer(contact):
    fa_id = str(contact.get("id") or "")
    name = (contact.get("name") or "").strip()
    if not fa_id or not name:
        return

    existing = frappe.db.get_value("Customer", {"flowaccount_contact_id": fa_id})
    if not existing and contact.get("taxNumber"):
        existing = frappe.db.get_value("Customer", {"tax_id": contact["taxNumber"]})
    if not existing:
        existing = frappe.db.get_value("Customer", {"customer_name": name})

    if existing:
        frappe.db.set_value(
            "Customer", existing,
            {"flowaccount_contact_id": fa_id},
            update_modified=False,
        )
        return

    customer = frappe.new_doc("Customer")
    customer.customer_name = name
    customer.customer_type = "Company" if contact.get("contactGroup") == 3 else "Individual"
    customer.tax_id = contact.get("taxNumber")
    customer.flowaccount_contact_id = fa_id
    customer.flags.ignore_permissions = True
    customer.insert(ignore_mandatory=True)


def _upsert_item(product):
    fa_id = str(product.get("id") or "")
    name = (product.get("name") or "").strip()
    if not fa_id or not name:
        return

    existing = frappe.db.get_value("Item", {"flowaccount_product_id": fa_id})
    if not existing:
        code = (product.get("code") or "").strip()
        existing = frappe.db.get_value("Item", {"item_code": code or name})

    if existing:
        frappe.db.set_value(
            "Item", existing,
            {"flowaccount_product_id": fa_id},
            update_modified=False,
        )
        return

    item = frappe.new_doc("Item")
    item.item_code = (product.get("code") or "").strip() or name
    item.item_name = name
    item.item_group = _default_item_group()
    item.stock_uom = "Nos"
    item.is_stock_item = 0
    item.flowaccount_product_id = fa_id
    item.flags.ignore_permissions = True
    item.insert(ignore_mandatory=True)


def _default_item_group():
    leaf = frappe.db.get_value("Item Group", {"is_group": 0})
    return leaf or "All Item Groups"


def _upsert_document(doc_type, record):
    record_id = str(record.get("recordId") or record.get("id") or "")
    if not record_id:
        return

    values = {
        "document_serial": record.get("documentSerial"),
        "contact_name": record.get("contactName"),
        "issue_date": (record.get("publishedOn") or "")[:10] or None,
        "due_date": (record.get("dueDate") or "")[:10] or None,
        "grand_total": record.get("grandTotal") or 0,
        "status": str(record.get("status") or ""),
        "payload": frappe.as_json(record),
        "last_synced": frappe.utils.now(),
    }

    existing = frappe.db.get_value(
        "FlowAccount Document",
        {"document_type": doc_type, "record_id": record_id},
    )
    if existing:
        frappe.db.set_value("FlowAccount Document", existing, values, update_modified=False)
        return

    mirror = frappe.new_doc("FlowAccount Document")
    mirror.document_type = doc_type
    mirror.record_id = record_id
    mirror.update(values)
    mirror.flags.ignore_permissions = True
    mirror.insert()
