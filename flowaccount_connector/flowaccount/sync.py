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
from flowaccount_connector.flowaccount import client, stock_out

# No separate /invoices endpoint exists — invoices live inside /tax-invoices.
# Totals probed live 2026-06-12: quotations 19.4k, expenses 17.3k,
# tax-invoices 10.6k, billing-notes/receipts ~10k, credit-notes 202,
# purchases 82. ERPNext-born quotations pushed out will reappear here as
# read-only mirrors, which is harmless (mirrors never push back).
DOCUMENT_ENDPOINTS = {
    "quotation": "/quotations",
    "billing-note": "/billing-notes",
    "tax-invoice": "/tax-invoices",
    "receipt": "/receipts",
    "credit-note": "/credit-notes",
    "debit-note": "/debit-notes",
    "expense": "/expenses",
    "purchase": "/purchases",
}


def pull_all():
    """Hourly dispatcher. Each (account, phase) runs as its own long-queue job
    because a full backfill (10k+ records) blows past the default 5-minute job
    timeout and gets killed silently mid-transaction."""
    if not frappe.conf.get("flowaccount_enabled"):
        return
    if not frappe.conf.get("flowaccount_pull_enabled"):
        return

    max_pages = int(frappe.conf.get("flowaccount_pull_pages") or 5)

    for account in client.accounts():
        for phase in ("contacts", "products", "documents"):
            frappe.enqueue(
                "flowaccount_connector.flowaccount.sync.run_phase",
                queue="long",
                timeout=7200,
                job_id=f"flowaccount-pull-{account}-{phase}",
                deduplicate=True,
                phase=phase,
                account=account,
                max_pages=max_pages,
            )


def run_phase(phase, account="company", max_pages=5):
    fn = {
        "contacts": pull_contacts,
        "products": pull_products,
        "documents": pull_documents,
    }[phase]
    try:
        fn(max_pages, account)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(
            title=f"FlowAccount pull failed: {phase} ({account})",
            message=frappe.get_traceback(),
        )


COMMIT_EVERY = 200


def pull_contacts(max_pages=5, account="company"):
    for i, contact in enumerate(client.iter_list("/contacts", max_pages=max_pages, account=account), 1):
        _upsert_customer(contact, account)
        if i % COMMIT_EVERY == 0:
            frappe.db.commit()


def pull_products(max_pages=5, account="company"):
    for i, product in enumerate(client.iter_list("/products", max_pages=max_pages, account=account), 1):
        _upsert_item(product, account)
        if i % COMMIT_EVERY == 0:
            frappe.db.commit()


def pull_documents(max_pages=5, account="company"):
    i = 0
    for doc_type, path in DOCUMENT_ENDPOINTS.items():
        for record in client.iter_list(path, max_pages=max_pages, account=account):
            _upsert_document(doc_type, record, account)
            i += 1
            if i % COMMIT_EVERY == 0:
                frappe.db.commit()


def _qualified_id(account, fa_id):
    # IDs are unique only within one FlowAccount account; prefix extras so a
    # shop contact can never link to a company contact with the same number.
    return fa_id if account == "company" else f"{account}:{fa_id}"


def _upsert_customer(contact, account="company"):
    fa_id = _qualified_id(account, str(contact.get("id") or ""))
    name = _clip(contact.get("contactName"))
    tax_id = _clip(contact.get("contactTaxId"))
    if not str(contact.get("id") or "") or not name or name == "-":
        return

    existing = frappe.db.get_value("Customer", {"flowaccount_contact_id": fa_id})
    if not existing and tax_id:
        existing = frappe.db.get_value("Customer", {"tax_id": tax_id})
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
    customer.customer_type = "Company" if str(contact.get("contactGroup")) == "3" else "Individual"
    customer.tax_id = tax_id or None
    customer.flowaccount_contact_id = fa_id
    customer.flags.ignore_permissions = True
    customer.insert(ignore_mandatory=True)


def _upsert_item(product, account="company"):
    fa_id = _qualified_id(account, str(product.get("id") or ""))
    name = _clip(product.get("name"))
    if not str(product.get("id") or "") or not name:
        return

    existing = frappe.db.get_value("Item", {"flowaccount_product_id": fa_id})
    if not existing:
        code = _clip(product.get("code"))
        existing = frappe.db.get_value("Item", {"item_code": code or name})

    if existing:
        frappe.db.set_value(
            "Item", existing,
            {"flowaccount_product_id": fa_id},
            update_modified=False,
        )
        return

    item = frappe.new_doc("Item")
    item.item_code = _clip(product.get("code")) or name
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


def _clip(value, length=140):
    # FlowAccount data carries trailing-space padding that can exceed
    # frappe's 140-char Data field limit and abort the whole batch.
    return str(value or "").strip()[:length]


def _upsert_document(doc_type, record, account="company"):
    record_id = str(record.get("recordId") or record.get("id") or "")
    if not record_id:
        return
    if record.get("isDelete") in (True, "true"):
        # Voided in FlowAccount: if we already moved stock for it, reverse it.
        voided = frappe.db.get_value(
            "FlowAccount Document",
            {"document_type": doc_type, "record_id": record_id},
            ["name", "stock_entry"], as_dict=True,
        )
        if voided and voided.stock_entry:
            stock_out.maybe_reverse(voided.name)
        return

    values = {
        "account": account,
        "document_serial": _clip(record.get("documentSerial")),
        "contact_name": _clip(record.get("contactName")),
        "issue_date": (record.get("publishedOn") or "")[:10] or None,
        "due_date": (record.get("dueDate") or "")[:10] or None,
        "grand_total": float(record.get("grandTotal") or 0),
        "status": _clip(record.get("statusString") or record.get("status")),
        "payload": frappe.as_json(record),
        "last_synced": frappe.utils.now(),
    }

    existing = frappe.db.get_value(
        "FlowAccount Document",
        {"document_type": doc_type, "record_id": record_id, "account": account},
    )
    if not existing and account == "company":
        # Rows mirrored before the account field existed have account = NULL.
        existing = frappe.db.get_value(
            "FlowAccount Document",
            {"document_type": doc_type, "record_id": record_id, "account": ("in", ("", None))},
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

    # Newly-mirrored doc -> move ERPNext stock (no-op unless enabled).
    # Only on insert, never on re-pull, to avoid flooding the queue.
    stock_out.maybe_move(mirror.name, doc_type)
