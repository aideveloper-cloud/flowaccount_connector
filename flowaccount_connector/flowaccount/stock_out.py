"""Deduct ERPNext stock from B2B sale documents that originated in FlowAccount.

B2B sales happen in FlowAccount (no ERPNext Sales Order behind them), so the
stock movement is mirrored as a Stock Entry (Material Issue) — a pure stock-out
that does NOT post sales revenue/COGS in ERPNext (the books live in FlowAccount).

Safe by design:
  - OFF unless `flowaccount_deduct_stock = 1` in Site Config.
  - Only document types in `flowaccount_deduct_doctypes` (default: tax-invoice).
  - Only line items that map confidently to a STOCK item are issued; anything
    unmatched or non-stock is skipped and logged — never guess and cut the
    wrong SKU.
  - Idempotent: each FlowAccount Document gets at most one Stock Entry
    (guarded by the `stock_deducted` flag on the mirror).

Warehouse comes from KGF Stock Settings (single source of truth), with a
`flowaccount_stock_warehouse` Site Config fallback.
"""

import frappe
from frappe.utils import flt

DEFAULT_DEDUCT_TYPES = ("tax-invoice",)


def _enabled():
    return bool(frappe.conf.get("flowaccount_deduct_stock"))


def _deduct_types():
    raw = frappe.conf.get("flowaccount_deduct_doctypes")
    if not raw:
        return DEFAULT_DEDUCT_TYPES
    return tuple(t.strip() for t in str(raw).split(",") if t.strip())


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


def maybe_deduct(fa_doc_name, doc_type):
    """Entry point called after a document mirror is upserted (see sync.py)."""
    if not _enabled():
        return
    if doc_type not in _deduct_types():
        return
    frappe.enqueue(
        "flowaccount_connector.flowaccount.stock_out.deduct_for_document",
        queue="long",
        job_id=f"flowaccount-deduct-{fa_doc_name}",
        deduplicate=True,
        fa_doc_name=fa_doc_name,
    )


def deduct_for_document(fa_doc_name):
    mirror = frappe.get_doc("FlowAccount Document", fa_doc_name)
    if mirror.get("stock_deducted"):
        return

    warehouse = _warehouse()
    if not warehouse:
        frappe.log_error(
            title="FlowAccount deduct: no warehouse",
            message="Set KGF Stock Settings.default_warehouse or flowaccount_stock_warehouse",
        )
        return

    payload = frappe.parse_json(mirror.payload) if mirror.payload else {}
    lines = payload.get("items") or []

    se_items, skipped = [], []
    for line in lines:
        qty = flt(line.get("quantity"))
        if qty <= 0:
            continue
        item_code = _match_item(line)
        if not item_code:
            skipped.append(line.get("name") or line.get("description"))
            continue
        se_items.append({"item_code": item_code, "qty": qty, "s_warehouse": warehouse})

    if not se_items:
        # Nothing stock-controlled on this doc — mark done so we don't retry it.
        frappe.db.set_value("FlowAccount Document", fa_doc_name, "stock_deducted", 1,
                            update_modified=False)
        frappe.db.commit()
        return

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Issue"
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
            title=f"FlowAccount deduct: skipped {len(skipped)} unmatched lines",
            message=f"{fa_doc_name}\nไม่พบ stock item สำหรับ:\n" + "\n".join(map(str, skipped)),
        )
