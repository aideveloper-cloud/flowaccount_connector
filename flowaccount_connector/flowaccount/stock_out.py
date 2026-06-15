"""Move ERPNext stock to mirror B2B documents that originated in FlowAccount.

B2B sales/returns happen in FlowAccount (no ERPNext Sales Order behind them),
so the stock movement is mirrored as a Stock Entry — a pure stock move that
does NOT post sales revenue/COGS in ERPNext (the books live in FlowAccount):

  - sale document (default: tax-invoice)  -> Material Issue   (stock OUT)
  - credit note  (default: credit-note)   -> Material Receipt (stock IN, return)
  - voided document (isDelete) that was already moved -> cancel its Stock Entry

Safe by design:
  - OFF unless `flowaccount_deduct_stock = 1` in Site Config.
  - Only configured document types move stock.
  - Only line items that map confidently to a STOCK item move; anything
    unmatched or non-stock is skipped and logged — never guess the wrong SKU.
  - Idempotent: each FlowAccount Document gets at most one Stock Entry
    (guarded by `stock_deducted` + `stock_entry` on the mirror).

Warehouse comes from KGF Stock Settings (single source of truth), with a
`flowaccount_stock_warehouse` Site Config fallback.
"""

import frappe
from frappe.utils import flt

DEFAULT_ISSUE_TYPES = ("tax-invoice",)
DEFAULT_RETURN_TYPES = ("credit-note",)


def _enabled():
    return bool(frappe.conf.get("flowaccount_deduct_stock"))


def _types(key, default):
    raw = frappe.conf.get(key)
    if not raw:
        return default
    return tuple(t.strip() for t in str(raw).split(",") if t.strip())


def _direction(doc_type):
    """'issue' (stock out), 'receipt' (stock in/return), or None."""
    if doc_type in _types("flowaccount_deduct_doctypes", DEFAULT_ISSUE_TYPES):
        return "issue"
    if doc_type in _types("flowaccount_return_doctypes", DEFAULT_RETURN_TYPES):
        return "receipt"
    return None


def _warehouse():
    try:
        wh = frappe.db.get_single_value("KGF Stock Settings", "default_warehouse")
        if wh:
            return wh
    except Exception:
        pass
    return frappe.conf.get("flowaccount_stock_warehouse")


def _match_item(line):
    """Return an ERPNext stock item_code for a FlowAccount document line, or
    None if there is no confident, stock-controlled match."""
    candidates = []
    fa_pid = str(line.get("id") or "")
    if fa_pid:
        candidates.append(("flowaccount_product_id", fa_pid))
    code = str(line.get("name") or "").strip()  # FA line "name" holds the code
    if code:
        candidates.append(("item_code", code))

    for field, value in candidates:
        item_code = frappe.db.get_value("Item", {field: value}, "name")
        if item_code:
            if frappe.get_cached_value("Item", item_code, "is_stock_item"):
                return item_code
            return None  # matched but not stock-controlled -> skip silently
    return None


def maybe_move(fa_doc_name, doc_type):
    """Entry point called after a NEW document mirror is inserted (see sync.py)."""
    if not _enabled():
        return
    direction = _direction(doc_type)
    if not direction:
        return
    frappe.enqueue(
        "flowaccount_connector.flowaccount.stock_out.move_for_document",
        queue="long",
        job_id=f"flowaccount-move-{fa_doc_name}",
        deduplicate=True,
        fa_doc_name=fa_doc_name,
        direction=direction,
    )


def maybe_reverse(fa_doc_name):
    """Called when a previously-moved document is voided in FlowAccount."""
    if not _enabled():
        return
    frappe.enqueue(
        "flowaccount_connector.flowaccount.stock_out.reverse_for_document",
        queue="long",
        job_id=f"flowaccount-reverse-{fa_doc_name}",
        deduplicate=True,
        fa_doc_name=fa_doc_name,
    )


@frappe.whitelist()
def run_deduct(fa_doc_name):
    """Manually move stock for one mirrored document (backfill / retry).

    Direction is auto-detected from the document type. Bypasses the
    flowaccount_deduct_stock gate on purpose — an explicit, audited admin
    action. System Manager only.
    """
    frappe.only_for("System Manager")
    doc_type = frappe.db.get_value("FlowAccount Document", fa_doc_name, "document_type")
    direction = _direction(doc_type) or "issue"
    move_for_document(fa_doc_name, direction)
    return frappe.db.get_value(
        "FlowAccount Document", fa_doc_name,
        ["stock_deducted", "stock_entry"], as_dict=True,
    )


def move_for_document(fa_doc_name, direction="issue"):
    mirror = frappe.get_doc("FlowAccount Document", fa_doc_name)
    if mirror.get("stock_deducted"):
        return

    warehouse = _warehouse()
    if not warehouse:
        frappe.log_error(
            title="FlowAccount stock move: no warehouse",
            message="Set KGF Stock Settings.default_warehouse or flowaccount_stock_warehouse",
        )
        return

    payload = frappe.parse_json(mirror.payload) if mirror.payload else {}
    lines = payload.get("items") or []
    wh_field = "s_warehouse" if direction == "issue" else "t_warehouse"

    se_items, skipped = [], []
    for line in lines:
        qty = flt(line.get("quantity"))
        if qty <= 0:
            continue
        item_code = _match_item(line)
        if not item_code:
            skipped.append(line.get("name") or line.get("description"))
            continue
        se_items.append({"item_code": item_code, "qty": qty, wh_field: warehouse})

    if not se_items:
        # Nothing stock-controlled here — mark done so we never retry it.
        frappe.db.set_value("FlowAccount Document", fa_doc_name, "stock_deducted", 1,
                            update_modified=False)
        frappe.db.commit()
        return

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Issue" if direction == "issue" else "Material Receipt"
    se.company = frappe.defaults.get_global_default("company")
    se.remarks = f"FlowAccount {mirror.document_type} {mirror.document_serial} ({mirror.record_id})"
    for row in se_items:
        se.append("items", row)
    se.flags.ignore_permissions = True
    se.insert()
    se.submit()

    frappe.db.set_value(
        "FlowAccount Document", fa_doc_name,
        {"stock_deducted": 1, "stock_entry": se.name},
        update_modified=False,
    )
    frappe.db.commit()

    if skipped:
        frappe.log_error(
            title=f"FlowAccount stock move: skipped {len(skipped)} unmatched lines",
            message=f"{fa_doc_name}\nไม่พบ stock item สำหรับ:\n" + "\n".join(map(str, skipped)),
        )


def reverse_for_document(fa_doc_name):
    """Cancel the Stock Entry created for a now-voided document, so its stock
    movement is undone. Idempotent: a missing or already-cancelled entry is a
    no-op."""
    se_name = frappe.db.get_value("FlowAccount Document", fa_doc_name, "stock_entry")
    if not se_name or not frappe.db.exists("Stock Entry", se_name):
        return
    se = frappe.get_doc("Stock Entry", se_name)
    if se.docstatus == 1:
        se.flags.ignore_permissions = True
        se.cancel()
    frappe.db.set_value("FlowAccount Document", fa_doc_name, "stock_deducted", 0,
                        update_modified=False)
    frappe.db.commit()


# Backwards-compatible alias (older enqueued jobs referenced this name).
def deduct_for_document(fa_doc_name):
    move_for_document(fa_doc_name, "issue")
