app_name = "flowaccount_connector"
app_title = "FlowAccount Connector"
app_publisher = "K Garden"
app_description = "Push ERPNext sales documents to FlowAccount via OpenAPI"
app_email = "ai.developer@kgarden.local"
app_license = "MIT"

# Push to FlowAccount when a Quotation is submitted.
doc_events = {
    "Quotation": {
        "on_submit": "flowaccount_connector.flowaccount.events.on_quotation_submit",
    }
}

# The custom fields below are auto-created on install via fixtures/custom_field.json
