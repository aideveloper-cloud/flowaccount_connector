"""Low-level FlowAccount OpenAPI client.

Verified against the official SDK (flowaccount/flowaccount-openapi-sdk):
  - Token:      POST {gateway}/token        (application/x-www-form-urlencoded)
  - Quotation:  POST {gateway}/quotations   (JSON, Bearer token)

Credentials are read from Site Config (set them in the Frappe Cloud dashboard
under Site -> Site Config). Required keys:
  flowaccount_enabled        -> 1 to turn the integration on
  flowaccount_client_id      -> your OpenAPI client id
  flowaccount_client_secret  -> your OpenAPI client secret
Optional:
  flowaccount_api_gateway    -> defaults to https://openapi.flowaccount.com/v1
  flowaccount_scope          -> defaults to flowaccount-api
"""

import frappe
import requests

TOKEN_CACHE_KEY = "flowaccount_access_token"
DEFAULT_GATEWAY = "https://openapi.flowaccount.com/v1"


def _conf(key, default=None):
    return frappe.conf.get(key, default)


def gateway():
    return _conf("flowaccount_api_gateway", DEFAULT_GATEWAY)


def get_token(force=False):
    """Return a valid access token, cached until shortly before it expires."""
    if not force:
        cached = frappe.cache().get_value(TOKEN_CACHE_KEY)
        if cached:
            return cached

    resp = requests.post(
        f"{gateway()}/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "scope": _conf("flowaccount_scope", "flowaccount-api"),
            "client_id": _conf("flowaccount_client_id"),
            "client_secret": _conf("flowaccount_client_secret"),
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("error") or not data.get("access_token"):
        frappe.throw(f"FlowAccount auth failed: {data.get('error') or data}")

    token = data["access_token"]
    ttl = max(int(data.get("expires_in", 3600)) - 60, 60)
    frappe.cache().set_value(TOKEN_CACHE_KEY, token, expires_in_sec=ttl)
    return token


def _request(method, path, params=None, json_body=None):
    """Send an authed request. Retries once on 401 with a fresh token."""
    url = f"{gateway()}{path}"

    def _send(token):
        return requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            params=params,
            json=json_body,
            timeout=60,
        )

    resp = _send(get_token())
    if resp.status_code == 401:
        resp = _send(get_token(force=True))

    if not resp.ok:
        frappe.log_error(
            title=f"FlowAccount API failed: {method} {path}",
            message=f"HTTP {resp.status_code}\n{resp.text}\n\nPayload:\n{frappe.as_json(json_body)}",
        )
        resp.raise_for_status()

    return resp.json()


def create_quotation(payload):
    """POST a SimpleDocument quotation."""
    return _request("POST", "/quotations", json_body=payload)


def iter_list(path, page_size=50, max_pages=5):
    """Yield records from a paginated list endpoint (?currentPage=&pageSize=)."""
    page = 1
    while page <= max_pages:
        data = _request("GET", path, params={"currentPage": page, "pageSize": page_size})
        batch = (data or {}).get("data") or []
        for record in batch:
            yield record
        if len(batch) < page_size:
            break
        page += 1
