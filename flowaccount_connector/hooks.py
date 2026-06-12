app_name = "flowaccount_connector"
app_title = "FlowAccount Connector"
app_publisher = "K Garden"
app_description = "Push ERPNext sales documents to FlowAccount via OpenAPI"
app_email = "ai.developer@kgarden.local"
app_license = "MIT"

# Show FlowAccount as a tile on the /apps screen.
add_to_apps_screen = [
    {
        "name": "flowaccount_connector",
        "logo": "/assets/flowaccount_connector/images/flowaccount-logo.svg",
        "title": "FlowAccount",
        "route": "/app/flowaccount-document",
    }
]

# Push to FlowAccount: quotations on submit, new items on first save.
doc_events = {
    "Quotation": {
        "on_submit": "flowaccount_connector.flowaccount.events.on_quotation_submit",
    },
    "Item": {
        "after_insert": "flowaccount_connector.flowaccount.events.on_item_save",
        "on_update": "flowaccount_connector.flowaccount.events.on_item_save",
    },
}

# Pull FlowAccount masters/documents into ERPNext every hour.
# No-op unless flowaccount_pull_enabled is set in Site Config (see sync.py).
scheduler_events = {
    "hourly": [
        "flowaccount_connector.flowaccount.sync.pull_all",
    ]
}

# The custom fields below are auto-created on install via fixtures/custom_field.json
