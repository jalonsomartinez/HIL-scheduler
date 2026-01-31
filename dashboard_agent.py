import logging
import threading
import time
import pandas as pd
import numpy as np
from datetime import timedelta, datetime
from io import StringIO
import dash
from dash import Dash, dcc, html, Input, Output, State, callback_context
from dash.exceptions import PreventUpdate
from pyModbusTCP.client import ModbusClient
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from schedule_manager import ScheduleMode


def dashboard_agent(config, shared_data):
    """
    Creates and runs a Dash dashboard to visualize the scheduler data.
    Displays power setpoints, battery SoC, and POI measurements.
    """
    logging.info("Dashboard agent started.")
    
    # Suppress the default Werkzeug server logs to keep the console clean
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    app = Dash(__name__, suppress_callback_exceptions=True)
    
    # Store API password in memory (session-only)
    api_password_memory = {"password": None}
    
    app.layout = html.Div(children=[
        # Main container
        html.Div(className='app-container', children=[
            
            # Header
            html.Div(className='app-header', children=[
                html.H1("HIL Scheduler Dashboard"),
                html.P("Real-time visualization of power setpoints, battery SoC, and POI measurements"),
            ]),
            
            # Tab container
            html.Div(className='tab-container', children=[
                # Tab headers
                html.Div(className='tab-header', children=[
                    html.Button(
                        "Schedule Configuration",
                        id='tab-config-btn',
                        className='tab-button active',
                        n_clicks=0
                    ),
                    html.Button(
                        "Status & Plots",
                        id='tab-status-btn',
                        className='tab-button',
                        n_clicks=0
                    ),
                ]),
                
                # Tab content container
                html.Div(id='tab-content', className='tab-content', children=[
                    
                    # =========================================
                    # TAB 1: SCHEDULE CONFIGURATION
                    # =========================================
                    html.Div(id='config-tab', children=[
                        
                        # Mode Selection Card
                        html.Div(className='card', children=[
                            html.Div(className='card-header', children=[
                                html.Div(children=[
                                    html.H3(className='card-title', children="Schedule Source"),
                                    html.P(className='card-subtitle', children="Choose how to generate or load your schedule"),
                                ]),
                            ]),
                            html.Div(className='mode-selector', children=[
                                html.Label(
                                    className='mode-option selected',
                                    id='mode-random-option',
                                    n_clicks=0,
                                    children=[
                                        html.Span(className='mode-label', children="Random Schedule"),
                                        html.Span(className='mode-description', children="Generate random power setpoints"),
                                    ]
                                ),
                                html.Label(
                                    className='mode-option',
                                    id='mode-csv-option',
                                    n_clicks=0,
                                    children=[
                                        html.Span(className='mode-label', children="CSV Upload"),
                                        html.Span(className='mode-description', children="Load schedule from CSV file"),
                                    ]
                                ),
                                html.Label(
                                    className='mode-option',
                                    id='mode-api-option',
                                    n_clicks=0,
                                    children=[
                                        html.Span(className='mode-label', children="Istentore API"),
                                        html.Span(className='mode-description', children="Fetch schedules from API"),
                                    ]
                                ),
                            ]),
                            dcc.RadioItems(
                                id='mode-selector',
                                options=[
                                    {'label': ' Random Schedule', 'value': 'random'},
                                    {'label': ' CSV File Upload', 'value': 'csv'},
                                    {'label': ' Istentore API', 'value': 'api'}
                                ],
                                value='random',
                                style={'display': 'none'}
                            ),
                        ]),
                        
                        # Random Mode Controls
                        html.Div(id='random-mode-controls', className='card', children=[
                            html.Div(className='card-header', children=[
                                html.H3(className='card-title', children="Random Schedule Settings"),
                            ]),
                            html.Div(className='form-row', children=[
                                html.Div(className='form-group', style={'width': '80px'}, children=[
                                    html.Label("Start Hour"),
                                    dcc.Dropdown(
                                        id='random-start-hour',
                                        options=[{'label': f'{h:02d}', 'value': h} for h in range(24)],
                                        value=datetime.now().hour,
                                        clearable=False,
                                        className='form-control'
                                    ),
                                ]),
                                html.Div(className='form-group', style={'width': '70px'}, children=[
                                    html.Label("Min"),
                                    dcc.Dropdown(
                                        id='random-start-minute',
                                        options=[{'label': f'{m:02d}', 'value': m} for m in range(0, 60, 5)],
                                        value=0,
                                        clearable=False,
                                        className='form-control'
                                    ),
                                ]),
                                html.Div(className='form-group', style={'width': '80px'}, children=[
                                    html.Label("End Hour"),
                                    dcc.Dropdown(
                                        id='random-end-hour',
                                        options=[{'label': f'{h:02d}', 'value': h} for h in range(24)],
                                        value=(datetime.now().hour + 1) % 24,
                                        clearable=False,
                                        className='form-control'
                                    ),
                                ]),
                                html.Div(className='form-group', style={'width': '70px'}, children=[
                                    html.Label("Min"),
                                    dcc.Dropdown(
                                        id='random-end-minute',
                                        options=[{'label': f'{m:02d}', 'value': m} for m in range(0, 60, 5)],
                                        value=0,
                                        clearable=False,
                                        className='form-control'
                                    ),
                                ]),
                                html.Div(className='form-group', style={'width': '90px'}, children=[
                                    html.Label("Step (min)"),
                                    dcc.Dropdown(
                                        id='random-step',
                                        options=[{'label': f'{m}', 'value': m} for m in [5, 10, 15, 30, 60]],
                                        value=5,
                                        clearable=False,
                                        className='form-control'
                                    ),
                                ]),
                            ]),
                            html.Div(className='form-row', children=[
                                html.Div(className='form-group', children=[
                                    html.Label("Min Power (kW)"),
                                    dcc.Input(
                                        id='random-min-power',
                                        type='number',
                                        value=-1000,
                                        step=10,
                                        className='form-control'
                                    ),
                                ]),
                                html.Div(className='form-group', children=[
                                    html.Label("Max Power (kW)"),
                                    dcc.Input(
                                        id='random-max-power',
                                        type='number',
                                        value=1000,
                                        step=10,
                                        className='form-control'
                                    ),
                                ]),
                                html.Div(className='form-group', children=[
                                    html.Label(""),
                                    html.Button(
                                        'Preview',
                                        id='random-generate-btn',
                                        n_clicks=0,
                                        className='btn btn-primary btn-block'
                                    ),
                                ]),
                            ]),
                        ]),
                        
                        # CSV Mode Controls
                        html.Div(id='csv-mode-controls', className='card hidden', children=[
                            html.Div(className='card-header', children=[
                                html.H3(className='card-title', children="CSV Upload"),
                            ]),
                            html.Div(className='form-row', style={'flexWrap': 'wrap'}, children=[
                                html.Div(className='form-group', style={'flex': '2', 'minWidth': '200px'}, children=[
                                    html.Label("Schedule File"),
                                    dcc.Upload(
                                        id='csv-upload',
                                        children=html.Div(
                                            className='file-upload',
                                            children=[
                                                html.Span(className='file-upload-text', children=[
                                                    "Drag and drop or ", html.A("select file"), " (CSV)"
                                                ])
                                            ]
                                        ),
                                        multiple=False
                                    ),
                                ]),
                                html.Div(className='form-group', style={'flex': '1', 'minWidth': '150px'}, children=[
                                    html.Label("Start Date"),
                                    dcc.DatePickerSingle(
                                        id='csv-start-date',
                                        date=datetime.now().date(),
                                        min_date_allowed=datetime(2020, 1, 1),
                                        max_date_allowed=datetime(2030, 12, 31),
                                        className='form-control'
                                    ),
                                ]),
                                html.Div(className='form-group', style={'width': '80px'}, children=[
                                    html.Label("Hour"),
                                    dcc.Dropdown(
                                        id='csv-start-hour',
                                        options=[{'label': f'{h:02d}', 'value': h} for h in range(24)],
                                        value=datetime.now().hour,
                                        clearable=False,
                                        className='form-control'
                                    ),
                                ]),
                                html.Div(className='form-group', style={'width': '70px'}, children=[
                                    html.Label("Min"),
                                    dcc.Dropdown(
                                        id='csv-start-minute',
                                        options=[{'label': f'{m:02d}', 'value': m} for m in range(0, 60, 5)],
                                        value=0,
                                        clearable=False,
                                        className='form-control'
                                    ),
                                ]),
                            ]),
                            html.Div(id='csv-filename-display', style={'fontSize': '13px', 'color': '#64748b'}),
                        ]),
                        
                        # API Mode Controls
                        html.Div(id='api-mode-controls', className='card hidden', children=[
                            html.Div(className='card-header', children=[
                                html.H3(className='card-title', children="Istentore API"),
                            ]),
                            html.Div(className='form-row', children=[
                                html.Div(className='form-group', style={'flex': '2'}, children=[
                                    html.Label("API Password (session-only)"),
                                    dcc.Input(
                                        id='api-password',
                                        type='password',
                                        placeholder='Enter API password',
                                        className='form-control'
                                    ),
                                ]),
                                html.Div(className='form-group', children=[
                                    html.Label(""),
                                    html.Button(
                                        'Connect & Fetch',
                                        id='api-connect-btn',
                                        n_clicks=0,
                                        className='btn btn-primary btn-block'
                                    ),
                                ]),
                            ]),
                            html.Div(id='api-status', style={
                                'fontSize': '13px',
                                'color': '#64748b',
                                'marginTop': '8px'
                            }),
                        ]),
                        
                        # Schedule Preview
                        html.Div(className='card', children=[
                            html.Div(className='card-header', children=[
                                html.H3(className='card-title', children="Schedule Preview"),
                                html.Div(id='mode-status', style={
                                    'fontSize': '13px',
                                    'color': '#64748b'
                                }),
                            ]),
                            html.Div(className='form-row', children=[
                                html.Button(
                                    'Clear Preview',
                                    id='clear-schedule-btn',
                                    n_clicks=0,
                                    className='btn btn-secondary'
                                ),
                                html.Button(
                                    'Accept Changes',
                                    id='accept-schedule-btn',
                                    n_clicks=0,
                                    className='btn btn-success'
                                ),
                            ]),
                            dcc.Graph(id='schedule-preview', style={'height': '250px'}),
                        ]),
                        
                    ]),  # End config tab
                    
                    # =========================================
                    # TAB 2: STATUS & PLOTS
                    # =========================================
                    html.Div(id='status-tab', className='hidden', children=[
                        
                        # Status Bar
                        html.Div(className='status-bar', children=[
                            html.Div(id='status-indicator', className='status-indicator status-unknown', children=[
                                html.Span(className='status-dot'),
                                "Unknown"
                            ]),
                            html.Div(id='mode-status-bar', className='status-info', children="Mode: None"),
                            html.Div(id='last-update', className='status-info', children=""),
                        ]),
                        
                        # Control Buttons
                        html.Div(className='card', children=[
                            html.Div(className='form-row', children=[
                                html.Button(
                                    children=[html.Span("▶"), " Start"],
                                    id='start-button',
                                    n_clicks=0,
                                    className='btn btn-success',
                                    disabled=True
                                ),
                                html.Button(
                                    children=[html.Span("■"), " Stop"],
                                    id='stop-button',
                                    n_clicks=0,
                                    className='btn btn-danger'
                                ),
                            ]),
                        ]),
                        
                        # Live Graph
                        html.Div(className='graph-container', children=[
                            dcc.Graph(id='live-graph', style={'height': '550px'}),
                        ]),
                        
                    ]),  # End status tab
                    
                ]),  # End tab content
            ]),  # End tab container
            
            # Hidden stores
            dcc.Store(id='uploaded-file-content'),
            dcc.Store(id='preview-schedule'),
            dcc.Store(id='active-tab', data='config'),
            dcc.Store(id='system-status', data='stopped'),
            dcc.Store(id='api-fetch-status', data={'today': 'idle', 'tomorrow': 'idle', 'last_attempt': None}),
            
            # Refresh interval
            dcc.Interval(
                id='interval-component',
                interval=5 * 1000,
                n_intervals=0
            ),
            
        ]),  # End app container
    ])
    
    # ============================================================
    # TAB SWITCHING CALLBACKS
    # ============================================================
    @app.callback(
        [Output('tab-config-btn', 'className'),
         Output('tab-status-btn', 'className'),
         Output('config-tab', 'className'),
         Output('status-tab', 'className'),
         Output('active-tab', 'data')],
        [Input('tab-config-btn', 'n_clicks'),
         Input('tab-status-btn', 'n_clicks')]
    )
    def switch_tabs(config_clicks, status_clicks):
        ctx = callback_context
        if not ctx.triggered:
            return ('tab-button active', 'tab-button', '', 'hidden', 'config')
        
        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        if button_id == 'tab-config-btn':
            return ('tab-button active', 'tab-button', '', 'hidden', 'config')
        elif button_id == 'tab-status-btn':
            return ('tab-button', 'tab-button active', 'hidden', '', 'status')
        
        return ('tab-button active', 'tab-button', '', 'hidden', 'config')
    
    # ============================================================
    # MODE SELECTION CALLBACKS
    # ============================================================
    @app.callback(
        [Output('random-mode-controls', 'className'),
         Output('csv-mode-controls', 'className'),
         Output('api-mode-controls', 'className'),
         Output('mode-random-option', 'className'),
         Output('mode-csv-option', 'className'),
         Output('mode-api-option', 'className')],
        Input('mode-selector', 'value')
    )
    def update_mode_controls(selected_mode):
        card_base = 'card'
        card_hidden = 'card hidden'
        option_base = 'mode-option'
        option_selected = 'mode-option selected'
        
        random_card = card_base if selected_mode == 'random' else card_hidden
        csv_card = card_base if selected_mode == 'csv' else card_hidden
        api_card = card_base if selected_mode == 'api' else card_hidden
        
        random_opt = option_selected if selected_mode == 'random' else option_base
        csv_opt = option_selected if selected_mode == 'csv' else option_base
        api_opt = option_selected if selected_mode == 'api' else option_base
        
        return random_card, csv_card, api_card, random_opt, csv_opt, api_opt
    
    # ============================================================
    # MODE BUTTON CLICK HANDLERS
    # ============================================================
    @app.callback(
        Output('mode-selector', 'value'),
        [Input('mode-random-option', 'n_clicks'),
         Input('mode-csv-option', 'n_clicks'),
         Input('mode-api-option', 'n_clicks')]
    )
    def handle_mode_clicks(random_clicks, csv_clicks, api_clicks):
        ctx = callback_context
        if not ctx.triggered:
            return 'random'
        
        # Get which option was clicked
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        if 'random' in trigger_id:
            return 'random'
        elif 'csv' in trigger_id:
            return 'csv'
        elif 'api' in trigger_id:
            return 'api'
        
        return 'random'
    
    # ============================================================
    # CSV UPLOAD CALLBACK - Simplified, just stores the file content
    # ============================================================
    @app.callback(
        Output('uploaded-file-content', 'data'),
        Input('csv-upload', 'contents'),
        State('csv-upload', 'filename'),
        prevent_initial_call=True
    )
    def handle_csv_upload(contents, filename):
        if contents is None:
            return None
        
        return {'contents': contents, 'filename': filename}
    
    # ============================================================
    # SCHEDULE PREVIEW UPDATE (with diff visualization)
    # ============================================================
    @app.callback(
        [Output('schedule-preview', 'figure'),
         Output('schedule-preview', 'className'),
         Output('clear-schedule-btn', 'className'),
         Output('accept-schedule-btn', 'className')],
        [Input('interval-component', 'n_intervals'),
         Input('preview-schedule', 'data'),
         Input('random-generate-btn', 'n_clicks'),
         Input('clear-schedule-btn', 'n_clicks'),
         Input('accept-schedule-btn', 'n_clicks')],
        prevent_initial_call=False
    )
    def update_schedule_preview(n, preview_data, random_clicks, clear_clicks, accept_clicks):
        # Check if we're in API mode - hide preview if so
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            if sm.mode == ScheduleMode.API:
                return create_empty_fig("API mode: Data loaded directly from API"), "hidden", "hidden", "hidden"
        
        # Get existing schedule
        existing_df = pd.DataFrame()
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            if not sm.is_empty:
                existing_df = sm.schedule_df.copy()
        
        # Get preview schedule
        preview_df = pd.DataFrame()
        if preview_data:
            try:
                preview_df = pd.read_json(StringIO(preview_data), orient='split')
            except Exception as e:
                logging.error(f"Error reading preview data: {e}")
        
        return create_schedule_preview_diff_fig(existing_df, preview_df), "", "", ""
    
    # ============================================================
    # RANDOM MODE PREVIEW
    # ============================================================
    @app.callback(
        [Output('preview-schedule', 'data', allow_duplicate=True),
         Output('csv-filename-display', 'children', allow_duplicate=True)],
        Input('random-generate-btn', 'n_clicks'),
        [State('random-start-hour', 'value'),
         State('random-start-minute', 'value'),
         State('random-end-hour', 'value'),
         State('random-end-minute', 'value'),
         State('random-step', 'value'),
         State('random-min-power', 'value'),
         State('random-max-power', 'value')],
        prevent_initial_call=True
    )
    def preview_random_schedule(n_clicks, start_hour, start_minute, end_hour, end_minute, step, min_power, max_power):
        if n_clicks == 0:
            raise PreventUpdate
        
        logging.info(f"Dashboard: Previewing random schedule (start={start_hour:02d}:{start_minute:02d}, end={end_hour:02d}:{end_minute:02d}, step={step}min, min={min_power}kW, max={max_power}kW)")
        
        # Calculate duration from start/end times
        today = datetime.now().date()
        start_dt = datetime.combine(today, datetime.min.time().replace(hour=start_hour, minute=start_minute))
        end_dt = datetime.combine(today, datetime.min.time().replace(hour=end_hour, minute=end_minute))
        
        # Handle overnight schedules
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        
        duration_h = (end_dt - start_dt).total_seconds() / 3600
        
        # Generate random schedule data without committing
        num_points = int(duration_h * 60 / step) + 1
        times = [start_dt + timedelta(minutes=i * step) for i in range(num_points)]
        power_values = np.random.uniform(min_power, max_power, num_points)
        
        preview_df = pd.DataFrame({
            'datetime': times,
            'power_setpoint_kw': power_values
        })
        
        preview_json = preview_df.to_json(orient='split', date_format='iso')
        
        return preview_json, f"Preview: {num_points} points generated"
    
    # ============================================================
    # ACCEPT SCHEDULE
    # ============================================================
    @app.callback(
        [Output('preview-schedule', 'data', allow_duplicate=True),
         Output('csv-filename-display', 'children', allow_duplicate=True)],
        Input('accept-schedule-btn', 'n_clicks'),
        State('preview-schedule', 'data'),
        prevent_initial_call=True
    )
    def accept_schedule(n_clicks, preview_data):
        if n_clicks == 0:
            raise PreventUpdate
        
        if not preview_data:
            return None, "No preview to accept"
        
        try:
            preview_df = pd.read_json(StringIO(preview_data), orient='split')
        except Exception as e:
            return None, f"Error: Invalid preview data: {str(e)}"
        
        logging.info(f"Dashboard: Accepting preview schedule ({len(preview_df)} points)")
        
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            sm.append_schedule_from_dict(
                dict(zip(preview_df['datetime'].dt.strftime('%Y-%m-%dT%H:%M:%S'), preview_df['power_setpoint_kw'])),
                default_q_kvar=0.0
            )
        
        return None, f"Schedule accepted ({len(preview_df)} points)"
    
    # ============================================================
    # CLEAR PREVIEW
    # ============================================================
    @app.callback(
        [Output('preview-schedule', 'data', allow_duplicate=True),
         Output('csv-filename-display', 'children', allow_duplicate=True)],
        Input('clear-schedule-btn', 'n_clicks'),
        State('preview-schedule', 'data'),
        prevent_initial_call=True
    )
    def clear_preview(n_clicks, preview_data):
        if n_clicks == 0:
            raise PreventUpdate
        
        logging.info("Dashboard: Clearing preview")
        
        # Clear preview only, not the existing schedule
        return None, "Preview cleared"
    
    # ============================================================
    # CSV PREVIEW (instead of immediate load)
    # ============================================================
    @app.callback(
        [Output('preview-schedule', 'data', allow_duplicate=True),
         Output('csv-filename-display', 'children', allow_duplicate=True)],
        [Input('csv-upload', 'contents'),
         Input('csv-start-date', 'date'),
         Input('csv-start-hour', 'value'),
         Input('csv-start-minute', 'value')],
        [State('csv-upload', 'filename')],
        prevent_initial_call=True
    )
    def preview_csv_schedule(contents, start_date, start_hour, start_minute, filename):
        if contents is None:
            raise PreventUpdate
        
        if not filename:
            raise PreventUpdate
        
        import base64
        
        try:
            content_type, content_string = contents.split(',')
            decoded = base64.b64decode(content_string)
        except Exception as e:
            logging.error(f"Error decoding CSV: {e}")
            raise PreventUpdate
        
        temp_csv_path = f"temp_{filename}"
        try:
            with open(temp_csv_path, 'wb') as f:
                f.write(decoded)
        except Exception as e:
            logging.error(f"Error writing temp file: {e}")
            raise PreventUpdate
        
        try:
            start_datetime = datetime.strptime(f"{start_date}", "%Y-%m-%d")
            start_datetime = start_datetime.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        except Exception as e:
            logging.error(f"Error parsing date: {e}")
            raise PreventUpdate
        
        logging.info(f"Dashboard: Previewing CSV schedule from {filename} starting at {start_datetime}")
        
        try:
            csv_df = pd.read_csv(temp_csv_path, parse_dates=['datetime'])
        except Exception as e:
            logging.error(f"Error reading CSV: {e}")
            raise PreventUpdate
        
        first_ts = csv_df['datetime'].iloc[0]
        offset = start_datetime - first_ts
        
        csv_df['datetime'] = csv_df['datetime'] + offset
        
        preview_df = csv_df[['datetime', 'power_setpoint_kw']].copy()
        preview_json = preview_df.to_json(orient='split', date_format='iso')
        
        return preview_json, f"Preview: {len(preview_df)} points from {filename}"
    
    # ============================================================
    # API CONNECT
    # ============================================================
    @app.callback(
        [Output('api-status', 'children'),
         Output('csv-filename-display', 'children', allow_duplicate=True),
         Output('api-fetch-status', 'data', allow_duplicate=True)],
        Input('api-connect-btn', 'n_clicks'),
        State('api-password', 'value'),
        State('api-fetch-status', 'data'),
        prevent_initial_call=True
    )
    def connect_api(n_clicks, password, current_api_status):
        if n_clicks == 0:
            raise PreventUpdate
        
        if not password:
            return "Error: Password required", "Error: Password required", current_api_status
        
        api_password_memory['password'] = password
        logging.info("Dashboard: Connecting to Istentore API")
        
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            try:
                # Initialize API if not already initialized
                if sm._api is None:
                    from istentore_api import IstentoreAPI
                    sm._api = IstentoreAPI()
                sm._api.set_password(password)
                
                # Fetch current day schedule
                now = datetime.now()
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                today_end = today_start + timedelta(days=1) - timedelta(minutes=15)
                
                schedule = sm._api.get_day_ahead_schedule(today_start, today_end)
                
                # Build status message
                status_parts = []
                today_points = len(schedule) if schedule else 0
                
                if schedule:
                    df = sm._api.schedule_to_dataframe(schedule)
                    with shared_data['lock']:
                        shared_data['schedule_final_df'] = df
                    status_parts.append(f"Today: {today_points} points")
                    today_status = 'success'
                else:
                    status_parts.append("Today: No data available")
                    today_status = 'no_data'
                
                # Try to fetch tomorrow's schedule too
                tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                tomorrow_end = tomorrow_start + timedelta(days=1) - timedelta(minutes=15)
                
                tomorrow_schedule = sm._api.get_day_ahead_schedule(tomorrow_start, tomorrow_end)
                tomorrow_points = len(tomorrow_schedule) if tomorrow_schedule else 0
                
                if tomorrow_schedule:
                    df = sm._api.schedule_to_dataframe(tomorrow_schedule)
                    with shared_data['lock']:
                        if not shared_data['schedule_final_df'].empty:
                            # Append tomorrow's data
                            shared_data['schedule_final_df'] = pd.concat([shared_data['schedule_final_df'], df]).sort_index()
                        else:
                            shared_data['schedule_final_df'] = df
                    status_parts.append(f"Tomorrow: {tomorrow_points} points")
                    tomorrow_status = 'success'
                else:
                    status_parts.append("Tomorrow: Not yet available")
                    tomorrow_status = 'pending'
                
                # Update mode
                sm._mode = ScheduleMode.API
                
                new_status = {
                    'today': today_status,
                    'tomorrow': tomorrow_status,
                    'last_attempt': now.isoformat(),
                    'today_points': today_points,
                    'tomorrow_points': tomorrow_points
                }
                
                # Store in shared_data for status bar display
                shared_data['api_fetch_status'] = new_status
                
                return "; ".join(status_parts), f"API mode: {', '.join(status_parts)}", new_status
            except Exception as e:
                logging.error(f"API connection failed: {e}")
                new_status = {
                    'today': 'error',
                    'tomorrow': 'idle',
                    'last_attempt': datetime.now().isoformat(),
                    'error': str(e)
                }
                shared_data['api_fetch_status'] = new_status
                return f"Error: {str(e)}", f"API error: {str(e)}", new_status
        
        return "Not connected", "Error: Schedule manager not initialized", current_api_status
    
    # ============================================================
    # START/STOP BUTTONS - Set transition status
    # ============================================================
    @app.callback(
        [Output('system-status', 'data', allow_duplicate=True),
         Output('csv-filename-display', 'children', allow_duplicate=True)],
        Input('start-button', 'n_clicks'),
        Input('stop-button', 'n_clicks'),
        prevent_initial_call=True
    )
    def handle_control_buttons(start_clicks, stop_clicks):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
        
        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        # Set transition status
        if button_id == 'start-button':
            new_status = 'starting'
            value_to_write = 1
            logging.info("Dashboard: Start button clicked.")
        elif button_id == 'stop-button':
            new_status = 'stopping'
            value_to_write = 0
            logging.info("Dashboard: Stop button clicked.")
        else:
            raise PreventUpdate
        
        client = ModbusClient(
            host=config["PLANT_MODBUS_HOST"],
            port=config["PLANT_MODBUS_PORT"]
        )
        if not client.open():
            logging.error("Dashboard: Could not connect to Plant.")
            return None, "Error: Connection to Plant failed."
        
        is_ok = client.write_single_register(
            config["PLANT_ENABLE_REGISTER"],
            value_to_write
        )
        client.close()
        
        if is_ok:
            msg = f"Command sent: {button_id.replace('-button', '')}"
            logging.info(f"Dashboard: {msg}")
            return new_status, msg
        else:
            msg = f"Failed to send command"
            logging.error(f"Dashboard: {msg}")
            return None, f"Error: {msg}"
    
    # ============================================================
    # UPDATE GRAPHS AND STATUS
    # ============================================================
    @app.callback(
        [Output('live-graph', 'figure'),
         Output('status-indicator', 'className'),
         Output('status-indicator', 'children'),
         Output('mode-status', 'children'),
         Output('mode-status-bar', 'children'),
         Output('last-update', 'children'),
         Output('system-status', 'data', allow_duplicate=True),
         Output('start-button', 'disabled'),
         Output('stop-button', 'disabled')],
        [Input('interval-component', 'n_intervals'),
         Input('random-generate-btn', 'n_clicks'),
         Input('api-connect-btn', 'n_clicks'),
         Input('accept-schedule-btn', 'n_clicks'),
         Input('system-status', 'data')],
        prevent_initial_call=True
    )
    def update_graphs_and_status(n, random_clicks, api_clicks, accept_clicks, current_status):
        # Read actual status from Modbus (always read, ignore transition state)
        actual_status = None
        client = ModbusClient(
            host=config["PLANT_MODBUS_HOST"],
            port=config["PLANT_MODBUS_PORT"]
        )
        if client.open():
            regs = client.read_holding_registers(
                config["PLANT_ENABLE_REGISTER"], 1
            )
            client.close()
            if regs:
                actual_status = regs[0]  # 1 = running, 0 = stopped
        
        # Determine status text and class from actual Modbus read
        if actual_status == 1:
            status_text = "Running"
            status_class = "status-indicator status-running"
            new_status = 'running'
        elif actual_status == 0:
            status_text = "Stopped"
            status_class = "status-indicator status-stopped"
            new_status = 'stopped'
        else:
            status_text = "Read Error"
            status_class = "status-indicator status-unknown"
            new_status = 'unknown'
        
        # Determine button disabled states based on actual status
        if actual_status == 1:
            # Running - disable start, enable stop
            start_disabled = True
            stop_disabled = False
        elif actual_status == 0:
            # Stopped - enable start, disable stop
            start_disabled = False
            stop_disabled = True
        else:
            # Error state - enable both buttons
            start_disabled = False
            stop_disabled = False
        
        # Mode status
        mode_status = "Mode: None"
        mode_status_bar = "Mode: None"
        api_fetch_info = ""
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            if sm.mode:
                mode_status = f"Mode: {sm.mode.value}"
                mode_status_bar = f"Mode: {sm.mode.value}"
                if sm.mode == ScheduleMode.API:
                    # Show API fetch status
                    api_status = shared_data.get('api_fetch_status', {})
                    today_status = api_status.get('today', 'idle')
                    tomorrow_status = api_status.get('tomorrow', 'idle')
                    today_pts = api_status.get('today_points', 0)
                    tomorrow_pts = api_status.get('tomorrow_points', 0)
                    
                    if today_status == 'success':
                        mode_status += f" | Today: {today_pts} pts"
                    elif today_status == 'no_data':
                        mode_status += f" | Today: No data"
                    
                    if tomorrow_status == 'success':
                        mode_status += f" | Tomorrow: {tomorrow_pts} pts"
                    elif tomorrow_status == 'pending':
                        mode_status += " | Tomorrow: Pending"
                    
                    mode_status_bar = mode_status
                elif not sm.is_empty:
                    mode_status += f" ({len(sm.schedule_df)} points)"
                    mode_status_bar += f" | {len(sm.schedule_df)} points"
        
        # Last update
        last_update = f"Last update: {datetime.now().strftime('%H:%M:%S')}"
        
        # Measurements graph
        measurements_df, schedule_df = load_and_process_data()
        
        if measurements_df.empty or schedule_df.empty:
            return create_empty_fig("No data available"), status_class, status_text, mode_status, mode_status_bar, last_update, new_status, start_disabled, stop_disabled
        
        # Create figure with 3 subplots
        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            subplot_titles=('Active Power (kW)', 'State of Charge (pu)', 'Reactive Power (kvar)')
        )
        
        # Active Power traces
        fig.add_trace(
            go.Scatter(
                x=schedule_df['datetime'],
                y=schedule_df['power_setpoint_kw'],
                mode='lines',
                line_shape='hv',
                name='P Setpoint',
                line=dict(color='#2563eb', width=2)
            ),
            row=1, col=1
        )
        fig.add_trace(
            go.Scatter(
                x=measurements_df['datetime'],
                y=measurements_df['battery_active_power_kw'],
                mode='lines',
                line_shape='hv',
                name='P Battery',
                line=dict(color='#16a34a', width=2)
            ),
            row=1, col=1
        )
        fig.add_trace(
            go.Scatter(
                x=measurements_df['datetime'],
                y=measurements_df['p_poi_kw'],
                mode='lines',
                line_shape='hv',
                name='P POI',
                line=dict(color='#dc2626', width=1.5, dash='dash')
            ),
            row=1, col=1
        )
        
        # SoC trace
        fig.add_trace(
            go.Scatter(
                x=measurements_df['datetime'],
                y=measurements_df['soc_pu'],
                mode='lines',
                name='SoC',
                line=dict(color='#9333ea', width=2)
            ),
            row=2, col=1
        )
        
        # Reactive Power traces
        if 'reactive_power_setpoint_kvar' in schedule_df.columns:
            fig.add_trace(
                go.Scatter(
                    x=schedule_df['datetime'],
                    y=schedule_df['reactive_power_setpoint_kvar'],
                    mode='lines',
                    line_shape='hv',
                    name='Q Setpoint',
                    line=dict(color='#ea580c', width=2)
                ),
                row=3, col=1
            )
        if 'battery_reactive_power_kvar' in measurements_df.columns:
            fig.add_trace(
                go.Scatter(
                    x=measurements_df['datetime'],
                    y=measurements_df['battery_reactive_power_kvar'],
                    mode='lines',
                    line_shape='hv',
                    name='Q Battery',
                    line=dict(color='#16a34a', width=2)
                ),
                row=3, col=1
            )
        if 'q_poi_kvar' in measurements_df.columns:
            fig.add_trace(
                go.Scatter(
                    x=measurements_df['datetime'],
                    y=measurements_df['q_poi_kvar'],
                    mode='lines',
                    line_shape='hv',
                    name='Q POI',
                    line=dict(color='#dc2626', width=1.5, dash='dash')
                ),
                row=3, col=1
            )
        
        # Layout
        fig.update_layout(
            uirevision='constant',
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.12,
                xanchor="center",
                x=0.5
            ),
            margin=dict(l=60, r=20, t=100, b=40),
            plot_bgcolor='#f5f5f0',
            paper_bgcolor='#ffffff',
        )
        
        fig.update_yaxes(title_text="Power (kW)", row=1, col=1, gridcolor='#e2e8f0')
        fig.update_yaxes(title_text="SoC (pu)", row=2, col=1, gridcolor='#e2e8f0')
        fig.update_yaxes(title_text="Power (kvar)", row=3, col=1, gridcolor='#e2e8f0')
        fig.update_xaxes(title_text="Time", row=3, col=1, gridcolor='#e2e8f0')
        
        return fig, status_class, status_text, mode_status, mode_status_bar, last_update, new_status, start_disabled, stop_disabled
    
    # ============================================================
    # HELPER FUNCTIONS
    # ============================================================
    def load_and_process_data():
        """Loads and preprocesses data for the graphs."""
        try:
            measurements_df = pd.read_csv(
                config["MEASUREMENTS_CSV"],
                parse_dates=['timestamp']
            )
            
            schedule_df = None
            if 'schedule_manager' in shared_data:
                sm = shared_data['schedule_manager']
                if not sm.is_empty:
                    schedule_df = sm.schedule_df.copy()
                    schedule_df = schedule_df.reset_index()
                    if 'index' in schedule_df.columns:
                        schedule_df = schedule_df.rename(columns={'index': 'datetime'})
            
            if schedule_df is None or schedule_df.empty:
                try:
                    schedule_df = pd.read_csv(
                        config["SCHEDULE_SOURCE_CSV"],
                        parse_dates=['datetime']
                    )
                except FileNotFoundError:
                    schedule_df = pd.DataFrame()
            
            if 'datetime' not in measurements_df.columns:
                measurements_df['datetime'] = measurements_df['timestamp']
            
            if not schedule_df.empty and 'reactive_power_setpoint_kvar' not in schedule_df.columns:
                schedule_df['reactive_power_setpoint_kvar'] = 0.0
            
            if not schedule_df.empty:
                end_time = schedule_df['datetime'].max() + timedelta(minutes=15)
                measurements_df = measurements_df[measurements_df['datetime'] <= end_time]
            
            return measurements_df, schedule_df
        except Exception as e:
            logging.error(f"Error loading data for dashboard: {e}")
            return pd.DataFrame(), pd.DataFrame()
    
    def create_schedule_preview_diff_fig(existing_df, preview_df):
        """Create a preview figure showing existing vs new schedule."""
        fig = go.Figure()
        
        # Show existing schedule in lighter color (dashed)
        if not existing_df.empty:
            if 'datetime' in existing_df.columns:
                x_existing = existing_df['datetime']
            elif hasattr(existing_df.index, 'name') and existing_df.index.name == 'datetime':
                x_existing = existing_df.index
            else:
                x_existing = existing_df.index
            
            fig.add_trace(
                go.Scatter(
                    x=x_existing,
                    y=existing_df['power_setpoint_kw'],
                    mode='lines',
                    line_shape='hv',
                    name='Existing',
                    line=dict(color='#94a3b8', width=2, dash='dash'),
                    opacity=0.7
                )
            )
        
        # Show preview schedule in stronger color (solid)
        if not preview_df.empty:
            if 'datetime' in preview_df.columns:
                x_preview = preview_df['datetime']
            else:
                x_preview = preview_df.index
            
            fig.add_trace(
                go.Scatter(
                    x=x_preview,
                    y=preview_df['power_setpoint_kw'],
                    mode='lines',
                    line_shape='hv',
                    name='Preview',
                    fill='tozeroy',
                    fillcolor='rgba(37, 99, 235, 0.15)',
                    line=dict(color='#2563eb', width=2.5)
                )
            )
        
        # Show message if both empty
        if existing_df.empty and preview_df.empty:
            fig.add_annotation(
                text="No schedule loaded. Generate a preview or load a schedule.",
                xref="paper", yref="paper",
                x=0.5, y=0.5,
                showarrow=False,
                font=dict(size=14, color='#64748b')
            )
        
        fig.update_layout(
            margin=dict(l=50, r=20, t=70, b=30),
            uirevision='constant',
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.12,
                xanchor="center",
                x=0.5
            ),
            plot_bgcolor='#ffffff',
            paper_bgcolor='#ffffff',
        )
        fig.update_xaxes(showgrid=True, gridcolor='#e2e8f0')
        fig.update_yaxes(title_text="Power (kW)", gridcolor='#e2e8f0')
        
        return fig
    
    def create_empty_fig(message):
        """Create an empty figure with a message."""
        fig = go.Figure()
        fig.add_annotation(
            text=message,
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=14, color='#64748b')
        )
        fig.update_layout(
            margin=dict(l=50, r=20, t=30, b=30),
            plot_bgcolor='#ffffff',
            paper_bgcolor='#ffffff',
        )
        return fig
    
    # Run the Dash app in a separate thread
    def run_app():
        app.run(debug=False)
    
    dashboard_thread = threading.Thread(target=run_app)
    dashboard_thread.daemon = True
    dashboard_thread.start()
    
    while not shared_data['shutdown_event'].is_set():
        time.sleep(1)
    
    logging.info("Dashboard agent stopped.")


if __name__ == "__main__":
    import dash
    from dash import dcc, html
    
    config = {
        'PLANT_MODBUS_HOST': 'localhost',
        'PLANT_MODBUS_PORT': 5020,
        'PLANT_ENABLE_REGISTER': 10,
        'MEASUREMENTS_CSV': 'measurements.csv',
        'SCHEDULE_SOURCE_CSV': 'schedule_source.csv',
        'SCHEDULE_DEFAULT_Q_POWER_KVAR': 0,
        'SCHEDULE_DEFAULT_RESOLUTION_MIN': 5,
    }
    
    shared_data = {
        'lock': threading.Lock(),
        'shutdown_event': threading.Event(),
    }
    
    dashboard_agent(config, shared_data)
