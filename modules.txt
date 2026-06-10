"""Map an ERPNext Quotation document to a FlowAccount SimpleDocument payload.

Field names are the exact camelCase JSON keys the FlowAccount API expects
(taken from the SDK attribute_map), NOT the snake_case python attribute names.

isVat = True  -> 7% VAT document   (B2B / company entity)
isVat = False -> no-VAT document   (B2C / ร้านค้า entity)
Derived from whether the Quotation actually carries tax charges.
"""

import frappe


# FlowAccount contactGroup: 1 = บุคคลธรรมดา, 3 = นิติบุคคล
def _contact_group(customer):
    if customer and (customer.customer_type or "").lower() == "company":
        return 3
    return 1


# FlowAccount item type: 1 = service, 3 = non-inventory, 5 = inventory
def _item_type(item_code):
    if not item_code:
        return 3
    is_stock = frappe.db.get_value("Item", item_code, "is_stock_item")
    return 5 if is_stock else 3


def quotation_to_payload(doc):
    customer = None
    if doc.quotation_to == "Customer" and doc.party_name:
        customer = frappe.get_doc("Customer", doc.party_name)

    items = []
    for row in doc.items:
        items.append({
            "type": _item_type(row.item_code),
            "name": row.item_name or row.item_code,
            "description": row.description or "",
            "quantity": float(row.qty),
            "unitName": row.uom or "",
            "pricePerUnit": float(row.rate),
            "total": float(row.amount),
        })

    has_vat = bool(doc.total_taxes_and_charges)

    payload = {
        "documentStructureType": "SimpleDocument",   # required discriminator
        "contactName": doc.customer_name or doc.party_name,
        "contactGroup": _contact_group(customer),
        "publishedOn": str(doc.transaction_date),     # yyyy-MM-dd
        "creditType": 3,                               # 3 = เงินสด (change to 1 + creditDays if credit)
        "dueDate": str(doc.valid_till or doc.transaction_date),
        "reference": doc.name,                         # ERPNext quotation no.
        "salesName": doc.owner,
        "isVatInclusive": False,                       # ERPNext prices are tax-exclusive
        "subTotal": float(doc.total),
        "discountAmount": float(doc.discount_amount or 0),
        "totalAfterDiscount": float(doc.net_total),
        "isVat": has_vat,
        "vatAmount": float(doc.total_taxes_and_charges or 0),
        "grandTotal": float(doc.grand_total),
        "items": items,
    }

    if customer:
        if customer.tax_id:
            payload["contactTaxId"] = customer.tax_id
        if customer.customer_primary_address:
            payload["contactAddress"] = frappe.db.get_value(
                "Address", customer.customer_primary_address, "address_line1"
            ) or ""

    return payload
