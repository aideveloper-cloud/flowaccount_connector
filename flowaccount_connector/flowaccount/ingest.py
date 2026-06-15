"""Controlled, idempotent ingest endpoints — the entry point for n8n.

Architecture (per project directive 2026-06): n8n calls the FlowAccount API
(no direct API calls from inside ERPNext), then POSTs the raw records here.
ERPNext owns the matching / clipping / dedup logic (reused from sync.py), so
n8n stays a thin transport layer and data-integrity rules live in one tested
place.

These REPLACE the in-ERPNext pull (sync.pull_all). When n8n is live, disable
the scheduler pull with Site Config `flowaccount_pull_enabled = 0` so the two
don't both pull (idempotent, but wasteful + burns API quota).

Idempotent: every endpoint upserts (matches existing records), and document
ingest also handles voids (isDelete) via the same path as the pull. Accepts a
single record or a list. Guarded: caller must hold a role in
`flowaccount_ingest_roles` (Site Config, default "System Manager").
"""

import json

import frappe

from flowaccount_connector.flowaccount import sync


def _guard():
    roles = frappe.conf.get("flowaccount_ingest_roles") or "System Manager"
    frappe.only_for([r.strip() for r in str(roles).split(",") if r.strip()])


def _records(payload):
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        return [payload]
    return payload or []


@frappe.whitelist()
def ingest_contacts(records, account="company"):
    _guard()
    n = 0
    for rec in _records(records):
        sync._upsert_customer(rec, account)
        n += 1
    frappe.db.commit()
    return {"ingested": n}


@frappe.whitelist()
def ingest_products(records, account="company"):
    _guard()
    n = 0
    for rec in _records(records):
        sync._upsert_item(rec, account)
        n += 1
    frappe.db.commit()
    return {"ingested": n}


@frappe.whitelist()
def ingest_documents(records, doc_type, account="company"):
    _guard()
    n = 0
    for rec in _records(records):
        sync._upsert_document(doc_type, rec, account)
        n += 1
    frappe.db.commit()
    return {"ingested": n}
