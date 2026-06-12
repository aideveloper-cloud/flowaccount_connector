"""Low-level FlowAccount OpenAPI client with multi-account support.

K Garden runs two selling entities, each its own FlowAccount account:
  - "company" -> the VAT-registered company (B2B, 7% documents)
  - "shop"    -> the storefront (B2C, no-VAT documents)

Credentials are read from Site Config (Frappe Cloud dashboard -> Site Config):
  flowaccount_enabled              -> 1 to turn the integration on
  flowaccount_client_id / _secret  -> the "company" account credentials
Optional extra accounts:
  flowaccount_extra_accounts       -> comma list of labels, e.g. "shop"
  flowaccount_shop_client_id       -> credentials per extra label
  flowaccount_shop_client_secret
  flowaccount_api_gateway          -> defaults to https://openapi.flowaccount.com/v1
  flowaccount_scope                -> defaults to flowaccount-api
"""

import frappe
import requests

DEFAULT_GATEWAY = "https://openapi.flowaccount.com/v1"
DEFAULT_ACCOUNT = "company"


def _conf(key, default=None):
    return frappe.conf.get(key, default)


def _cred(account, key):
    if account == DEFAULT_ACCOUNT:
        return _conf(f"flowaccount_{key}")
    return _conf(f"flowaccount_{account}_{key}")


def accounts():
    """Labels of all accounts that have credentials configured."""
    out = []
    if _conf("flowaccount_client_id"):
        out.append(DEFAULT_ACCOUNT)
    for label in str(_conf("flowaccount_extra_accounts") or "").split(","):
        label = label.strip()
        if label and label != DEFAULT_ACCOUNT and _cred(label, "client_id"):
            out.append(label)
    return out


def gateway():
    return _conf("flowaccount_api_gateway", DEFAULT_GATEWAY)


def get_token(account=DEFAULT_ACCOUNT, force=False):
    """Return a valid access token, cached per account until near expiry."""
    cache_key = f"flowaccount_access_token_{account}"
    if not force:
        cached = frappe.cache().get_value(cache_key)
        if cached:
            return cached

    resp = requests.post(
        f"{gateway()}/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "scope": _conf("flowaccount_scope", "flowaccount-api"),
            "client_id": _cred(account, "client_id"),
            "client_secret": _cred(account, "client_secret"),
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("error") or not data.get("access_token"):
        frappe.throw(f"FlowAccount auth failed ({account}): {data.get('error') or data}")

    token = data["access_token"]
    ttl = max(int(data.get("expires_in", 3600)) - 60, 60)
    frappe.cache().set_value(cache_key, token, expires_in_sec=ttl)
    return token


def _request(method, path, params=None, json_body=None, account=DEFAULT_ACCOUNT):
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

    resp = _send(get_token(account))
    if resp.status_code == 401:
        resp = _send(get_token(account, force=True))

    if not resp.ok:
        frappe.log_error(
            title=f"FlowAccount API failed: {method} {path} ({account})",
            message=f"HTTP {resp.status_code}\n{resp.text}\n\nPayload:\n{frappe.as_json(json_body)}",
        )
        resp.raise_for_status()

    return resp.json()


def create_quotation(payload, account=DEFAULT_ACCOUNT):
    """POST a SimpleDocument quotation."""
    return _request("POST", "/quotations", json_body=payload, account=account)


def iter_list(path, page_size=50, max_pages=5, account=DEFAULT_ACCOUNT):
    """Yield records from a paginated list endpoint (?currentPage=&pageSize=).

    Response shape (verified against the live API):
      {"data": {"total": N, "currentPage": 1, "list": [...]}, "status": ..., ...}
    """
    page = 1
    while page <= max_pages:
        body = _request(
            "GET", path,
            params={"currentPage": page, "pageSize": page_size},
            account=account,
        )
        data = (body or {}).get("data") or {}
        batch = data if isinstance(data, list) else (data.get("list") or [])
        for record in batch:
            yield record
        if len(batch) < page_size:
            break
        page += 1
