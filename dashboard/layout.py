"""Dashboard layout composition."""

from dash import dcc, html


def build_dashboard_layout(
    config,
    plant_ids,
    plant_name_fn,
    brand_logo_src,
    initial_transport,
    initial_posting_enabled,
    now_value,
):
    return html.Div(
        className="app-container",
        children=[
            html.Header(
                className="app-header",
                children=[
                    html.Div(
                        className="app-header-brand",
                        children=[
                            html.Img(src=brand_logo_src, alt="i-STENTORE", className="brand-logo"),
                            html.Div(
                                className="app-header-copy",
                                children=[
                                    html.H1("Spanish Demo Dashboard", className="app-title"),
                                    html.P("Dispatch, recording, and API observability for LIB and VRFB plants.", className="app-subtitle"),
                                ],
                            ),
                        ],
                    )
                ],
            ),
            dcc.Tabs(
                id="main-tabs",
                value="status",
                className="main-tabs",
                parent_className="main-tabs-parent",
                children=[
                    dcc.Tab(
                        label="Status",
                        value="status",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="control-panel",
                                children=[
                                    html.Div(
                                        className="controls-row status-top-controls-row",
                                        children=[
                                            html.Div(
                                                className="control-section status-top-control-section",
                                                children=[
                                                    html.Span("Transport", className="toggle-label"),
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Local", id="transport-local-btn", className="toggle-option active", n_clicks=0, disabled=True),
                                                            html.Button("Remote", id="transport-remote-btn", className="toggle-option", n_clicks=0, disabled=False),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="control-section status-top-control-section",
                                                children=[
                                                    html.Span("Fleet Actions", className="toggle-label"),
                                                    html.Div(
                                                        className="fleet-actions-group",
                                                        children=[
                                                            html.Button("Start All", id="start-all-btn", className="btn btn-primary", n_clicks=0),
                                                            html.Button("Stop All", id="stop-all-btn", className="btn btn-danger", n_clicks=0),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(id="operator-plant-summary-table", className="public-summary-table-wrap"),
                                    html.Div(id="api-status-inline", className="status-text"),
                                    html.Div(id="control-engine-status-inline", className="status-text"),
                                    html.Div(id="control-queue-status-inline", className="status-text"),
                                ],
                            ),
                            html.Div(
                                id="toggle-confirm-modal",
                                className="modal-overlay hidden",
                                children=[
                                    html.Div(
                                        className="modal-card",
                                        children=[
                                            html.H3("Confirm Action", id="toggle-confirm-modal-title", className="modal-title"),
                                            html.P("", id="toggle-confirm-modal-text"),
                                            html.Div(
                                                className="modal-actions",
                                                children=[
                                                    html.Button("Cancel", id="toggle-confirm-cancel", className="btn btn-secondary"),
                                                    html.Button("Confirm", id="toggle-confirm-confirm", className="btn btn-primary"),
                                                ],
                                            ),
                                        ],
                                    )
                                ],
                            ),
                            html.Div(
                                id="bulk-control-modal",
                                className="modal-overlay hidden",
                                children=[
                                    html.Div(
                                        className="modal-card",
                                        children=[
                                            html.H3("Confirm Fleet Action", id="bulk-control-modal-title", className="modal-title"),
                                            html.P("", id="bulk-control-modal-text"),
                                            html.Div(
                                                className="modal-actions",
                                                children=[
                                                    html.Button("Cancel", id="bulk-control-cancel", className="btn btn-secondary"),
                                                    html.Button("Confirm", id="bulk-control-confirm", className="btn btn-primary"),
                                                ],
                                            ),
                                        ],
                                    )
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.H3(f"{plant_name_fn('lib')}"),
                                    html.Div(
                                        className="plant-controls-row",
                                        children=[
                                            html.Div(
                                                className="control-group plant-control-group",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Run", id="start-lib", className="toggle-option toggle-option--positive", n_clicks=0, disabled=False),
                                                            html.Button("Stopped", id="stop-lib", className="toggle-option toggle-option--negative active", n_clicks=0, disabled=True),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-section",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Dispatch", id="dispatch-enable-lib", className="toggle-option toggle-option--positive", n_clicks=0, disabled=False),
                                                            html.Button("Paused", id="dispatch-disable-lib", className="toggle-option toggle-option--negative active", n_clicks=0, disabled=True),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-group record-control-group",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Record", id="record-lib", className="toggle-option toggle-option--positive", n_clicks=0, disabled=False),
                                                            html.Button("Stopped", id="record-stop-lib", className="toggle-option toggle-option--negative active", n_clicks=0, disabled=True),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(id="status-lib", className="status-text"),
                                    dcc.Graph(id="graph-lib", className="plot-graph"),
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.H3(f"{plant_name_fn('vrfb')}"),
                                    html.Div(
                                        className="plant-controls-row",
                                        children=[
                                            html.Div(
                                                className="control-group plant-control-group",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Run", id="start-vrfb", className="toggle-option toggle-option--positive", n_clicks=0, disabled=False),
                                                            html.Button("Stopped", id="stop-vrfb", className="toggle-option toggle-option--negative active", n_clicks=0, disabled=True),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-section",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Dispatch", id="dispatch-enable-vrfb", className="toggle-option toggle-option--positive", n_clicks=0, disabled=False),
                                                            html.Button("Paused", id="dispatch-disable-vrfb", className="toggle-option toggle-option--negative active", n_clicks=0, disabled=True),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-group record-control-group",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Record", id="record-vrfb", className="toggle-option toggle-option--positive", n_clicks=0, disabled=False),
                                                            html.Button("Stopped", id="record-stop-vrfb", className="toggle-option toggle-option--negative active", n_clicks=0, disabled=True),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(id="status-vrfb", className="status-text"),
                                    dcc.Graph(id="graph-vrfb", className="plot-graph"),
                                ],
                            ),
                        ],
                    ),
                    dcc.Tab(
                        label="Plots",
                        value="plots",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="card",
                                children=[
                                    html.H3("Historical Measurements"),
                                    html.Div(id="plots-status-text", className="status-text"),
                                    dcc.Graph(id="plots-timeline-graph", className="plot-graph"),
                                    html.Div(id="plots-range-label", className="status-text"),
                                    dcc.RangeSlider(
                                        id="plots-range-slider",
                                        min=0,
                                        max=1,
                                        value=[0, 1],
                                        marks={},
                                        allowCross=False,
                                        updatemode="mouseup",
                                        disabled=True,
                                    ),
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.Div(
                                        className="form-row",
                                        children=[
                                            html.H3(f"{plant_name_fn('lib')}"),
                                            html.Div(
                                                className="fleet-actions-group",
                                                children=[
                                                    html.Button(
                                                        "Download CSV",
                                                        id="plots-download-csv-lib-btn",
                                                        className="btn btn-primary",
                                                        n_clicks=0,
                                                    ),
                                                    html.Button(
                                                        "Download PNG",
                                                        id="plots-download-png-lib-btn",
                                                        className="btn btn-secondary",
                                                        n_clicks=0,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    dcc.Graph(id="plots-graph-lib", className="plot-graph"),
                                    dcc.Download(id="plots-download-csv-lib"),
                                    html.Div(id="plots-lib-png-noop", style={"display": "none"}),
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.Div(
                                        className="form-row",
                                        children=[
                                            html.H3(f"{plant_name_fn('vrfb')}"),
                                            html.Div(
                                                className="fleet-actions-group",
                                                children=[
                                                    html.Button(
                                                        "Download CSV",
                                                        id="plots-download-csv-vrfb-btn",
                                                        className="btn btn-primary",
                                                        n_clicks=0,
                                                    ),
                                                    html.Button(
                                                        "Download PNG",
                                                        id="plots-download-png-vrfb-btn",
                                                        className="btn btn-secondary",
                                                        n_clicks=0,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    dcc.Graph(id="plots-graph-vrfb", className="plot-graph"),
                                    dcc.Download(id="plots-download-csv-vrfb"),
                                    html.Div(id="plots-vrfb-png-noop", style={"display": "none"}),
                                ],
                            ),
                        ],
                    ),
                    dcc.Tab(
                        label="Manual Schedule",
                        value="manual",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="card",
                                children=[
                                    html.Div(
                                        style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "alignItems": "flex-start"},
                                        children=[
                                            html.Div(
                                                style={"flex": "2 1 680px", "minWidth": "320px"},
                                                children=[
                                                    html.Div(
                                                        className="manual-override-grid",
                                                        style={"rowGap": "16px", "columnGap": "16px"},
                                                        children=[
                                                            html.Div(
                                                                className="manual-override-card",
                                                                style={"paddingBottom": "10px"},
                                                                children=[
                                                                    html.Div(
                                                                        className="form-row",
                                                                        style={"alignItems": "center", "gap": "10px"},
                                                                        children=[
                                                                            html.Div("LIB Active Power", className="toggle-label"),
                                                                            html.Div(
                                                                                className="compact-toggle",
                                                                                children=[
                                                                                    html.Button("Activate", id="manual-toggle-lib-p-enable-btn", className="toggle-option toggle-option--positive", n_clicks=0),
                                                                                    html.Button("Inactive", id="manual-toggle-lib-p-disable-btn", className="toggle-option toggle-option--negative active", n_clicks=0),
                                                                                ],
                                                                            ),
                                                                            html.Button("Update", id="manual-toggle-lib-p-update-btn", className="btn btn-secondary", n_clicks=0),
                                                                        ],
                                                                    ),
                                                                    html.Div(style={"height": "4px"}),
                                                                    dcc.Graph(id="manual-graph-lib-p", className="plot-graph", style={"marginBottom": "6px"}),
                                                                ],
                                                            ),
                                                            html.Div(
                                                                className="manual-override-card",
                                                                style={"paddingBottom": "10px"},
                                                                children=[
                                                                    html.Div(
                                                                        className="form-row",
                                                                        style={"alignItems": "center", "gap": "10px"},
                                                                        children=[
                                                                            html.Div("LIB Reactive Power", className="toggle-label"),
                                                                            html.Div(
                                                                                className="compact-toggle",
                                                                                children=[
                                                                                    html.Button("Activate", id="manual-toggle-lib-q-enable-btn", className="toggle-option toggle-option--positive", n_clicks=0),
                                                                                    html.Button("Inactive", id="manual-toggle-lib-q-disable-btn", className="toggle-option toggle-option--negative active", n_clicks=0),
                                                                                ],
                                                                            ),
                                                                            html.Button("Update", id="manual-toggle-lib-q-update-btn", className="btn btn-secondary", n_clicks=0),
                                                                        ],
                                                                    ),
                                                                    html.Div(style={"height": "4px"}),
                                                                    dcc.Graph(id="manual-graph-lib-q", className="plot-graph", style={"marginBottom": "6px"}),
                                                                ],
                                                            ),
                                                            html.Div(
                                                                className="manual-override-card",
                                                                style={"paddingBottom": "10px"},
                                                                children=[
                                                                    html.Div(
                                                                        className="form-row",
                                                                        style={"alignItems": "center", "gap": "10px"},
                                                                        children=[
                                                                            html.Div("VRFB Active Power", className="toggle-label"),
                                                                            html.Div(
                                                                                className="compact-toggle",
                                                                                children=[
                                                                                    html.Button("Activate", id="manual-toggle-vrfb-p-enable-btn", className="toggle-option toggle-option--positive", n_clicks=0),
                                                                                    html.Button("Inactive", id="manual-toggle-vrfb-p-disable-btn", className="toggle-option toggle-option--negative active", n_clicks=0),
                                                                                ],
                                                                            ),
                                                                            html.Button("Update", id="manual-toggle-vrfb-p-update-btn", className="btn btn-secondary", n_clicks=0),
                                                                        ],
                                                                    ),
                                                                    html.Div(style={"height": "4px"}),
                                                                    dcc.Graph(id="manual-graph-vrfb-p", className="plot-graph", style={"marginBottom": "6px"}),
                                                                ],
                                                            ),
                                                            html.Div(
                                                                className="manual-override-card",
                                                                style={"paddingBottom": "10px"},
                                                                children=[
                                                                    html.Div(
                                                                        className="form-row",
                                                                        style={"alignItems": "center", "gap": "10px"},
                                                                        children=[
                                                                            html.Div("VRFB Reactive Power", className="toggle-label"),
                                                                            html.Div(
                                                                                className="compact-toggle",
                                                                                children=[
                                                                                    html.Button("Activate", id="manual-toggle-vrfb-q-enable-btn", className="toggle-option toggle-option--positive", n_clicks=0),
                                                                                    html.Button("Inactive", id="manual-toggle-vrfb-q-disable-btn", className="toggle-option toggle-option--negative active", n_clicks=0),
                                                                                ],
                                                                            ),
                                                                            html.Button("Update", id="manual-toggle-vrfb-q-update-btn", className="btn btn-secondary", n_clicks=0),
                                                                        ],
                                                                    ),
                                                                    html.Div(style={"height": "4px"}),
                                                                    dcc.Graph(id="manual-graph-vrfb-q", className="plot-graph", style={"marginBottom": "6px"}),
                                                                ],
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                style={"flex": "0 1 350px", "minWidth": "320px", "maxWidth": "390px"},
                                                children=[
                                                    html.H4(
                                                        "Manual Schedule Editor",
                                                        className="card-title",
                                                        style={"marginBottom": "10px"},
                                                    ),
                                                    html.Div(
                                                        style={"display": "flex", "gap": "6px", "flexWrap": "wrap", "alignItems": "center"},
                                                        children=[
                                                            html.Div(
                                                                style={"width": "128px", "minWidth": "128px", "flex": "0 0 128px"},
                                                                children=[
                                                                    dcc.Dropdown(
                                                                        id="manual-editor-series-selector",
                                                                        className="manual-editor-series-select",
                                                                        style={"width": "128px"},
                                                                        clearable=False,
                                                                        value="lib_p",
                                                                        options=[
                                                                            {"label": "LIB - P", "value": "lib_p"},
                                                                            {"label": "LIB - Q", "value": "lib_q"},
                                                                            {"label": "VRFB - P", "value": "vrfb_p"},
                                                                            {"label": "VRFB - Q", "value": "vrfb_q"},
                                                                        ],
                                                                    ),
                                                                ],
                                                            ),
                                                            html.Button(
                                                                "Clear",
                                                                id="manual-editor-clear-btn",
                                                                className="btn btn-danger",
                                                                n_clicks=0,
                                                                style={"padding": "4px 10px", "fontSize": "0.75rem", "minHeight": "30px"},
                                                            ),
                                                            dcc.Upload(
                                                                id="manual-editor-csv-upload",
                                                                children=html.Button(
                                                                    "Load",
                                                                    className="btn btn-secondary",
                                                                    n_clicks=0,
                                                                    style={"padding": "4px 10px", "fontSize": "0.75rem", "minHeight": "30px"},
                                                                ),
                                                                multiple=False,
                                                                style={"display": "inline-flex", "border": "none", "padding": "0", "background": "transparent"},
                                                            ),
                                                            html.Button(
                                                                "Save",
                                                                id="manual-editor-save-csv-btn",
                                                                className="btn btn-primary",
                                                                n_clicks=0,
                                                                style={"padding": "4px 10px", "fontSize": "0.75rem", "minHeight": "30px"},
                                                            ),
                                                        ],
                                                    ),
                                                    html.Div(
                                                        style={"height": "8px"},
                                                    ),
                                                    html.Div(
                                                        style={"display": "flex", "gap": "4px", "alignItems": "center", "flexWrap": "wrap"},
                                                        children=[
                                                            html.Div("Start", style={"fontWeight": "600", "minWidth": "40px"}),
                                                            dcc.DatePickerSingle(
                                                                id="manual-editor-start-date",
                                                                date=now_value.date(),
                                                                className="date-picker manual-editor-start-date-picker",
                                                                style={"width": "108px", "minWidth": "108px"},
                                                            ),
                                                            html.Div(style={"width": "2px"}),
                                                            dcc.Input(id="manual-editor-start-hour", className="form-control", type="number", min=0, max=23, step=1, value=now_value.hour, style={"width": "44px", "minWidth": "44px", "padding": "2px 3px", "height": "28px"}),
                                                            dcc.Input(id="manual-editor-start-minute", className="form-control", type="number", min=0, max=59, step=1, value=now_value.minute, style={"width": "44px", "minWidth": "44px", "padding": "2px 3px", "height": "28px"}),
                                                            dcc.Input(id="manual-editor-start-second", className="form-control", type="number", min=0, max=59, step=1, value=0, style={"width": "44px", "minWidth": "44px", "padding": "2px 3px", "height": "28px"}),
                                                        ],
                                                    ),
                                                    html.Div(style={"height": "8px"}),
                                                    html.Div(
                                                        id="manual-editor-add-row-container",
                                                        children=[
                                                            html.Button(
                                                                "Add Breakpoint",
                                                                id="manual-editor-add-first-row-btn",
                                                                className="btn btn-primary",
                                                                n_clicks=0,
                                                                style={"padding": "5px 12px", "fontSize": "0.78rem", "minHeight": "32px"},
                                                            ),
                                                        ],
                                                    ),
                                                    html.Div(
                                                        id="manual-breakpoint-rows-container",
                                                        children=[html.Div(id="manual-breakpoint-rows")],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ],
                    ),
                    dcc.Tab(
                        label="API Schedule",
                        value="api",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="card",
                                children=[
                                    html.Div(
                                        className="form-row api-credentials-row",
                                        children=[
                                            dcc.Input(id="api-password", type="password", placeholder="API password", className="form-control api-password-input"),
                                            html.Div(
                                                className="compact-toggle",
                                                children=[
                                                    html.Button("Connect", id="set-password-btn", className="toggle-option toggle-option--positive", n_clicks=0),
                                                    html.Button("Disconnected", id="disconnect-api-btn", className="toggle-option toggle-option--negative active", n_clicks=0),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        className="form-row",
                                        children=[
                                            html.Div(
                                                className="control-section api-posting-toggle-section",
                                                children=[
                                                    html.Span("Measurement Posting", className="toggle-label"),
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button(
                                                                "Enabled" if initial_posting_enabled else "Enable",
                                                                id="api-posting-enable-btn",
                                                                className=(
                                                                    "toggle-option toggle-option--positive active"
                                                                    if initial_posting_enabled
                                                                    else "toggle-option toggle-option--positive"
                                                                ),
                                                                n_clicks=0,
                                                            ),
                                                            html.Button(
                                                                "Disable" if initial_posting_enabled else "Disabled",
                                                                id="api-posting-disable-btn",
                                                                className=(
                                                                    "toggle-option toggle-option--negative"
                                                                    if initial_posting_enabled
                                                                    else "toggle-option toggle-option--negative active"
                                                                ),
                                                                n_clicks=0,
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            )
                                        ],
                                    ),
                                    html.Div(id="api-connection-status", className="status-text"),
                                    html.Div(id="api-measurement-posting-status"),
                                    dcc.Graph(id="api-preview-graph", className="plot-graph"),
                                ],
                            )
                        ],
                    ),
                    dcc.Tab(
                        label="Logs",
                        value="logs",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="card",
                                children=[
                                    html.Div(
                                        className="card-header logs-header",
                                        children=[
                                            html.H3(className="card-title", children="Logs"),
                                            html.Div(
                                                className="logs-header-actions",
                                                children=[
                                                    html.Div(id="log-file-path", className="log-file-path"),
                                                    dcc.Dropdown(
                                                        id="log-file-selector",
                                                        className="log-selector-dropdown",
                                                        options=[],
                                                        value="today",
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(id="logs-display", className="logs-display"),
                                ],
                            )
                        ],
                    ),
                ],
            ),
            dcc.Store(id="control-action", data="idle"),
            dcc.Store(id="manual-settings-action", data="idle"),
            dcc.Store(id="api-connection-action", data="idle"),
            dcc.Store(id="posting-settings-action", data="idle"),
            dcc.Store(id="bulk-control-request", data=None),
            dcc.Store(id="toggle-confirm-request", data=None),
            dcc.Store(id="toggle-confirm-action", data=None),
            dcc.Store(id="transport-mode-selector", data=initial_transport),
            dcc.Store(id="api-posting-toggle-store", data=bool(initial_posting_enabled)),
            dcc.Store(id="manual-editor-rows-store", data=[]),
            dcc.Store(id="manual-editor-status-store", data=""),
            dcc.Store(id="manual-editor-delete-index-store", data=None),
            dcc.Store(id="plots-index-store", data={"has_data": False, "files_by_plant": {"lib": [], "vrfb": []}}),
            dcc.Store(id="plots-range-meta-store", data=None),
            dcc.Download(id="manual-editor-download"),
            dcc.ConfirmDialog(id="manual-editor-clear-confirm", message="Clear selected manual schedule?"),
            dcc.ConfirmDialog(id="manual-editor-delete-confirm", message="Delete this breakpoint?"),
            dcc.Interval(id="interval-component", interval=int(float(config["MEASUREMENT_PERIOD_S"]) * 1000), n_intervals=0),
            dcc.Interval(id="plots-refresh-interval", interval=30000, n_intervals=0),
        ],
    )
