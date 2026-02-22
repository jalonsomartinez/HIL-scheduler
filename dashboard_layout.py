"""Dashboard layout composition."""

from dash import dcc, html


def build_dashboard_layout(
    config,
    plant_ids,
    plant_name_fn,
    brand_logo_src,
    initial_transport,
    initial_source,
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
                                        className="controls-row",
                                        children=[
                                            html.Div(
                                                className="control-section",
                                                children=[
                                                    html.Span("Schedule Source", className="toggle-label"),
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Manual", id="source-manual-btn", className="toggle-option active", n_clicks=0),
                                                            html.Button("API", id="source-api-btn", className="toggle-option", n_clicks=0),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="control-section",
                                                children=[
                                                    html.Span("Transport", className="toggle-label"),
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Local", id="transport-local-btn", className="toggle-option active", n_clicks=0),
                                                            html.Button("Remote", id="transport-remote-btn", className="toggle-option", n_clicks=0),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="control-section",
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
                                    html.Div(id="api-status-inline", className="status-text"),
                                ],
                            ),
                            html.Div(
                                id="schedule-switch-modal",
                                className="modal-overlay hidden",
                                children=[
                                    html.Div(
                                        className="modal-card",
                                        children=[
                                            html.H3("Confirm Schedule Source Switch", className="modal-title"),
                                            html.P("Switching schedule source will safe-stop both plants. Recording will remain active. Continue?"),
                                            html.Div(
                                                className="modal-actions",
                                                children=[
                                                    html.Button("Cancel", id="schedule-switch-cancel", className="btn btn-secondary"),
                                                    html.Button("Confirm", id="schedule-switch-confirm", className="btn btn-primary"),
                                                ],
                                            ),
                                        ],
                                    )
                                ],
                            ),
                            html.Div(
                                id="transport-switch-modal",
                                className="modal-overlay hidden",
                                children=[
                                    html.Div(
                                        className="modal-card",
                                        children=[
                                            html.H3("Confirm Transport Switch", className="modal-title"),
                                            html.P("Switching transport mode will safe-stop both plants, stop recording, and clear plot caches. Continue?"),
                                            html.Div(
                                                className="modal-actions",
                                                children=[
                                                    html.Button("Cancel", id="transport-switch-cancel", className="btn btn-secondary"),
                                                    html.Button("Confirm", id="transport-switch-confirm", className="btn btn-primary"),
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
                                                    html.Button("Start", id="start-lib", className="control-btn control-btn-start", n_clicks=0, disabled=False),
                                                    html.Button("Stopped", id="stop-lib", className="control-btn control-btn-stop", n_clicks=0, disabled=True),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-group record-control-group",
                                                children=[
                                                    html.Button("Record", id="record-lib", className="control-btn control-btn-record", n_clicks=0, disabled=False),
                                                    html.Button("Record Stopped", id="record-stop-lib", className="control-btn control-btn-record-stop", n_clicks=0, disabled=True),
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
                                                    html.Button("Start", id="start-vrfb", className="control-btn control-btn-start", n_clicks=0, disabled=False),
                                                    html.Button("Stopped", id="stop-vrfb", className="control-btn control-btn-stop", n_clicks=0, disabled=True),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-group record-control-group",
                                                children=[
                                                    html.Button("Record", id="record-vrfb", className="control-btn control-btn-record", n_clicks=0, disabled=False),
                                                    html.Button("Record Stopped", id="record-stop-vrfb", className="control-btn control-btn-record-stop", n_clicks=0, disabled=True),
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
                                        className="form-row",
                                        children=[
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("Plant"),
                                                    dcc.Dropdown(
                                                        id="manual-plant-selector",
                                                        options=[{"label": plant_name_fn(pid), "value": pid} for pid in plant_ids],
                                                        value="lib",
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("Start Hour"),
                                                    dcc.Dropdown(
                                                        id="manual-start-hour",
                                                        options=[{"label": f"{h:02d}", "value": h} for h in range(24)],
                                                        value=now_value.hour,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("End Hour"),
                                                    dcc.Dropdown(
                                                        id="manual-end-hour",
                                                        options=[{"label": f"{h:02d}", "value": h} for h in range(24)],
                                                        value=(now_value.hour + 1) % 24,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("Step (min)"),
                                                    dcc.Dropdown(
                                                        id="manual-step",
                                                        options=[{"label": str(v), "value": v} for v in [5, 10, 15, 30, 60]],
                                                        value=5,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="form-group", children=[html.Label("Min kW"), dcc.Input(id="manual-min-power", className="form-control", type="number", value=-1000)]),
                                            html.Div(className="form-group", children=[html.Label("Max kW"), dcc.Input(id="manual-max-power", className="form-control", type="number", value=1000)]),
                                        ],
                                    ),
                                    html.Div(
                                        className="form-row",
                                        children=[
                                            html.Button("Generate Random", id="manual-generate", className="btn btn-primary", n_clicks=0),
                                            html.Button("Clear Plant Schedule", id="manual-clear", className="btn btn-danger", n_clicks=0),
                                        ],
                                    ),
                                    html.Div(
                                        className="form-row",
                                        children=[
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("CSV Upload"),
                                                    dcc.Upload(
                                                        id="manual-csv-upload",
                                                        className="file-upload",
                                                        children=html.Div(["Drag/drop or ", html.A("select CSV")]),
                                                        multiple=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[html.Label("CSV Start Date"), dcc.DatePickerSingle(id="manual-csv-date", date=now_value.date(), className="date-picker")],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("CSV Start Hour"),
                                                    dcc.Dropdown(
                                                        id="manual-csv-hour",
                                                        options=[{"label": f"{h:02d}", "value": h} for h in range(24)],
                                                        value=now_value.hour,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("CSV Start Min"),
                                                    dcc.Dropdown(
                                                        id="manual-csv-minute",
                                                        options=[{"label": f"{m:02d}", "value": m} for m in range(0, 60, 5)],
                                                        value=0,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(id="manual-status-text", className="status-text"),
                                    dcc.Graph(id="manual-preview-graph", className="plot-graph"),
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
                                            html.Button("Set Password", id="set-password-btn", className="btn btn-primary", n_clicks=0),
                                            html.Button("Disconnect", id="disconnect-api-btn", className="btn btn-danger", n_clicks=0),
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
                                                                "Enabled",
                                                                id="api-posting-enable-btn",
                                                                className="toggle-option active" if initial_posting_enabled else "toggle-option",
                                                                n_clicks=0,
                                                            ),
                                                            html.Button(
                                                                "Disabled",
                                                                id="api-posting-disable-btn",
                                                                className="toggle-option" if initial_posting_enabled else "toggle-option active",
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
            dcc.Store(id="bulk-control-request", data=None),
            dcc.Store(id="transport-mode-selector", data=initial_transport),
            dcc.Store(id="active-source-selector", data=initial_source),
            dcc.Store(id="api-posting-toggle-store", data=bool(initial_posting_enabled)),
            dcc.Store(id="plots-index-store", data={"has_data": False, "files_by_plant": {"lib": [], "vrfb": []}}),
            dcc.Store(id="plots-range-meta-store", data=None),
            dcc.Interval(id="interval-component", interval=int(float(config.get("MEASUREMENT_PERIOD_S", 1)) * 1000), n_intervals=0),
            dcc.Interval(id="plots-refresh-interval", interval=30000, n_intervals=0),
        ],
    )
