import json

import frappe
from frappe.utils import now_datetime

# @frappe.whitelist()
# def create_sales_order(data=None):
#     """Create a Customer if needed, then create a Sales Order.
#     """
#     if not data:
#         data = frappe.local.form_dict

#     data = frappe.parse_json(data)

#     customer = data.get("customer")
#     if not customer:
#         frappe.throw("Missing required field: customer")

#     if not frappe.db.exists("Customer", customer):
#         cust_doc = frappe.get_doc(
#             {
#                 "doctype": "Customer",
#                 "customer_name": customer,
#                 "customer_type": "Individual"
#             }
#         )
#         cust_doc.insert(ignore_permissions=True)
#         customer = cust_doc.name

#     items = data.get("items") or []
#     # Ensure items is a list of dicts
#     if isinstance(items, str):
#         try:
#             items = json.loads(items)
#         except Exception:
#             items = []

#     so_doc = frappe.get_doc(
#         {
#             "doctype": "Sales Order",
#             "customer": customer,
#             "transaction_date": data.get("transaction_date"),
#             "delivery_date": data.get("delivery_date"),
#             "sales_channel": data.get("sales_channel"),
#             "items": items,
#         }
#     )
#     so_doc.insert(ignore_permissions=True)

#     # frappe.db.commit()

#     return {"sales_order": so_doc.name, "customer": customer}


@frappe.whitelist()
def create_sales_order(data=None):
    """Create a Customer if needed, then create a Draft Sales Order."""

    if not data:
        data = frappe.local.form_dict

    # Build the payload string safely, whatever shape the request has
    try:
        data = frappe.parse_json(data)
        request_payload = json.dumps(data, indent=2, default=str)
    except Exception:
        request_payload = str(data)

    sync_log = frappe.get_doc({
        "doctype": "Middleware Sync Log",
        "event": "create",
        "status": "Pending",
        "document_type": "Sales Order",
        "sync_type": "Single Document",
        "process_type": "Incoming",
        "request_payload": request_payload,
        "retry_count": 0
    })

    sync_log.insert(ignore_permissions=True)
    frappe.db.commit()  # persist the log so a later rollback can't remove it

    try:

        if not isinstance(data, dict):
            frappe.throw("Invalid payload: expected a JSON object.")

        order_details = data.get("order") or {}
        customer_details = data.get("customer") or {}
        items_data = data.get("items") or []

        # Validate customer
        customer_id = customer_details.get("customer_id")
        customer_name = customer_details.get("customer_name")

        customer = customer_name or customer_id

        if not customer_id:
            frappe.throw("Missing required field: customer.customer_id")

        # if not customer_name:
        #     frappe.throw("Missing required field: customer.customer_name")

        existing_customer = frappe.db.get_value(
            "Customer",
            {"source_customer_id": customer_id},
            "name"
        )

        if existing_customer:
            customer = existing_customer
        else:
            cust_doc = frappe.get_doc(
                {
                    "doctype": "Customer",
                    'source': "Shopify",
                    "source_customer_id": customer_id,
                    "customer_name": customer_name or customer_id,
                    "customer_type": "Individual"
                }
            )
            cust_doc.insert(ignore_permissions=True)
            customer = cust_doc.name

        # Validate items
        if not isinstance(items_data, list) or not items_data:
            frappe.throw("Order must contain at least one item.")

        sales_order_items = []

        for item in items_data:
            item_code = item.get("item_code")
            qty = item.get("quantity")
            rate = item.get("unit_price")

            if not item_code:
                frappe.throw("Missing item_code in order items.")

            if qty is None or qty <= 0:
                frappe.throw(f"Invalid quantity for item {item_code}.")

            if rate is None or rate < 0:
                frappe.throw(f"Invalid rate for item {item_code}.")

            warehouse_details = item.get("warehouse") or {}

            sales_order_items.append({
                "item_code": item_code,
                "qty": qty,
                "rate": rate,
                "delivery_date": (
                    order_details.get("delivery_date")
                    or data.get("delivery_date")
                ),
                "warehouse": warehouse_details.get("warehouse_name")
            })

        # Create Sales Order
        so_data = {
            "doctype": "Sales Order",
            "customer": customer,
            "transaction_date": (
                order_details.get("order_date")
                or data.get("transaction_date")
            ),
            "delivery_date": (
                order_details.get("delivery_date")
                or data.get("delivery_date")
            ),
            "sales_channel": (
                order_details.get("sales_channel")
                or data.get("sales_channel")
            ),
            "set_warehouse": data.get("set_warehouse"),
            "po_no": (
                data.get("po_no")
                or order_details.get("po_no")
                or order_details.get("order_number")
            ),
            "items": sales_order_items
        }

        so_doc = frappe.get_doc(so_data)
        so_doc.insert(ignore_permissions=True)
        # frappe.db.commit()

        # Middleware: Update Sync Log after successful creation
        sync_log.document = so_doc.name
        sync_log.status = "Success"
        sync_log.sync_time = now_datetime()
        sync_log.save(ignore_permissions=True)

        return {
            "success": True,
            "sales_order": so_doc.name,
            "customer": customer,
            "message": "Sales Order created successfully"
        }

    except Exception:
        # Discard partial work (e.g. a newly created Customer) from this request
        frappe.db.rollback()

        # Middleware: Mark Sync Log as Failed (the log itself was committed earlier)
        sync_log.status = "Failed"
        sync_log.sync_time = now_datetime()
        sync_log.save(ignore_permissions=True)
        frappe.db.commit()
        raise