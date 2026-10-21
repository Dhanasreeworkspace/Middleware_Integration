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
    """Create a Customer if needed, then create a Sales Order."""

    # Capture the incoming request payload
    raw_request = frappe.request.get_data(as_text=True)

    if not data:
        data = raw_request

    if isinstance(data, str):
        data = json.loads(data)

    # Create Middleware Sync Log for the incoming Sales Order request
    sync_log = frappe.get_doc({
        "doctype": "Middleware Sync Log",
        "event": "create",
        "status": "Pending",
        "document_type": "Sales Order",
        "sync_type": "Sales Order Sync",
        "request": raw_request or json.dumps(data, indent=2),
        "retry_count": 0,
    })

    sync_log.insert(ignore_permissions=True)

    try:
        orders = data.get("data") if isinstance(data, dict) and "data" in data else data
        if isinstance(orders, dict):
            orders = [orders]

        results = []

        for order_data in orders:
            order_details = order_data.get("order")
            customer_details = order_data.get("customer")
            items_data = order_data.get("items")
            sales_channel = order_details.get("sales_channel")

            customer_name = customer_details.get("customer_name")

            customer = frappe.db.get_value(
                "Customer",
                {"customer_name": customer_name},
                "name"
            )

            # Create Customer if it does not exist
            if not customer:
                customer_doc = frappe.get_doc({
                    "doctype": "Customer",
                    "customer_name": customer_name,
                    "customer_type": "Individual",
                })

                customer_doc.insert(ignore_permissions=True)
                customer = customer_doc.name

            sales_order_items = []

            for item in items_data:
                sales_order_items.append({
                    "item_code": item.get("item_code"),
                    "qty": item.get("quantity"),
                    "rate": item.get("unit_price"),
                    "delivery_date": order_details.get("delivery_date"),
                    "warehouse": item["warehouse"]["warehouse_name"]
                })

            # Create Sales Order
            so_doc = frappe.get_doc({
                "doctype": "Sales Order",
                "customer": customer,
                "transaction_date": order_details.get("order_date"),
                "delivery_date": order_details.get("delivery_date"),
                "sales_channel": sales_channel,
                "items": sales_order_items,
            })

            so_doc.insert(ignore_permissions=True)

            results.append({
                "sales_order": so_doc.name,
                "customer": customer,
            })

        # Update the log after successful creation
        sync_log.document = results[0]["sales_order"] if results else None
        sync_log.status = "Success"
        sync_log.sync_time = now_datetime()
        sync_log.save(ignore_permissions=True)

        return {
            "success": True,
            "message": "Sales Order created successfully",
            "data": results,
        }

    except Exception:
        # Mark the log as failed if an error occurs
        sync_log.reload()
        sync_log.status = "Failed"
        sync_log.sync_time = now_datetime()
        sync_log.save(ignore_permissions=True)

        raise