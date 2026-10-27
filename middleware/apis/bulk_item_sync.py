import json

import frappe
import requests
from middleware.apis.item_sync import update_sync_log, build_payload
from middleware.utils import get_middleware_settings


@frappe.whitelist()
def bulk_sync_items(items):
    if isinstance(items, str):
        items = frappe.parse_json(items)

    pending_items = []
    payload = []

    for item in items:
        doc = frappe.get_doc("Item", item)

        if doc.enable_product_sync or doc.has_variants:
            continue
            
        pending_items.append(item)
        payload.append(build_payload(doc, "create")["item"])

    if not pending_items:
        return {"queued": False}

    frappe.enqueue(
        "middleware.apis.bulk_item_sync.process_bulk_sync",
        queue="long",
        items=pending_items,
        payload=payload,
        enqueue_after_commit=True
    )

    return {"queued": True}


def process_bulk_sync(items, payload=None, log_name=None):
    settings = get_middleware_settings(
        endpoint="bulk_sync_endpoint",
        token="bulk_sync_authorization_token"
    )

    endpoint = settings.get("bulk_sync_endpoint")
    token = settings.get_password("bulk_sync_authorization_token")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    payload = []
    pending_items = []

    for item_code in items:
        doc = frappe.get_doc("Item", item_code)

        if doc.enable_product_sync:
            continue

        pending_items.append(item_code)
        payload.append(build_payload(doc, "create")["item"])

    if not pending_items:
        return

    if log_name:
        log = frappe.get_doc("Middleware Sync Log", log_name)
    else:
        log = create_bulk_sync_log(items)

    log.request_payload = json.dumps(payload, indent=2, default=str)
    log.save(ignore_permissions=True)
    frappe.db.commit()

    response = None

    try:
        response = requests.post(
            endpoint,
            json={
                "event": "create",
                "items": payload
            },
            headers=headers,
            timeout=120
        )

        response.raise_for_status()

        update_sync_log(log, "Success", response=response.text)

        for item_code in items:
            frappe.db.set_value(
                "Item",
                item_code,
                "enable_product_sync",
                1,
                update_modified=False
            )

    except Exception:
        update_sync_log(log, "Failed", response=response.text if response else frappe.get_traceback())
        
        frappe.log_error(
            title=f"Bulk Item Sync Failed",
            message=f"""
                Status: {response.status_code}
                Response: {response.text}
                {frappe.get_traceback()}
                """
        )


def create_bulk_sync_log(items):
    documents = []

    for item in items:
        doc = frappe.get_doc("Item", item)

        documents.append({
            "document": item,
            "is_variant": bool(doc.variant_of)
        })

    log = frappe.get_doc({
        "doctype": "Middleware Sync Log",
        "event": "create",
        "sync_type": "Bulk Import",
        "process_type": "Outgoing",
        "status": "Pending",
        "document_type": "Item",
        "documents": documents
    })

    log.insert(ignore_permissions=True)

    return log
