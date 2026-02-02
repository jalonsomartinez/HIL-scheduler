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
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import manual_schedule_manager as msm


def dashboard_agent(config, shared_data):
    """
    Creates and runs a Dash dashboard with three tabs:
    1. Manual Schedule: Random generation, CSV upload, preview/accept
    2. API Schedule: Password input, connection status
    3. Status & Plots: Active schedule selector, live graphs, system status
    
    All shared data access uses brief locks for immediate data freshness.
    """
    logging.info("Dashboard agent started.")
    
    # Suppress the default Werkzeug server logs
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    app = Dash(__name__, suppress_callback_exceptions=True)
    
    # Track last Modbus status (only mutable state needed)
    last_modbus_status = None
    
    app.layout = html.Div(children=[
        html.Div(className='app-container', children=[
            
            # Header
            html.Div(className='app-header', children=[
                html.H1("HIL Scheduler Dashboard"),
                html.P("Manage manual schedules, API connection, and monitor system status"),
            ]),
            
            # Tab headers
            html.Div(className='tab-header', children=[
                html.Button("Status & Plots", id='tab-status-btn', className='tab-button active', n_clicks=0),
                html.Button("Manual Schedule", id='tab-manual-btn', className='tab-button', n_clicks=0),
                html.Button("API Schedule", id='tab-api-btn', className='tab-button', n_clicks=0),
                html.Button("Logs", id='tab-logs-btn', className='tab-button', n_clicks=0),
            ]),
            
            # Tab content
            html.Div(id='tab-content', className='tab-content', children=[
                
                # =========================================
                # TAB 1: STATUS & PLOTS
                # =========================================
                html.Div(id='status-tab', children=[
                    
                    # Control Panel - Two Rows
                    html.Div(className='control-panel', children=[
                        
                        # Row 1: Controls (Buttons + Toggles) - 1/3 width each on medium+
                        html.Div(className='controls-row', children=[
                            # Start/Stop Buttons (1/3)
                            html.Div(className='control-section', children=[
                                html.Div(className='control-group', children=[
                                    html.Button(
                                        children=["▶ Start"], 
                                        id='start-button', 
                                        n_clicks=0, 
                                        className='control-btn control-btn-start', 
                                        disabled=True
                                    ),
                                    html.Button(
                                        children=["■ Stop"], 
                                        id='stop-button', 
                                        n_clicks=0, 
                                        className='control-btn control-btn-stop'
                                    ),
                                ]),
                            ]),
                            
                            # Schedule Toggle (1/3)
                            html.Div(className='control-section', children=[
                                html.Div(className='toggle-wrapper', children=[
                                    html.Span(className='toggle-label', children="Schedule:"),
                                    html.Div(className='compact-toggle', children=[
                                        html.Button(
                                            "Manual", 
                                            id='source-manual-btn', 
                                            className='toggle-option active',
                                            n_clicks=0
                                        ),
                                        html.Button(
                                            "API", 
                                            id='source-api-btn', 
                                            className='toggle-option',
                                            n_clicks=0
                                        ),
                                    ]),
                                ]),
                            ]),
                            
                            # Plant Toggle (1/3)
                            html.Div(className='control-section', children=[
                                html.Div(className='toggle-wrapper', children=[
                                    html.Span(className='toggle-label', children="Plant:"),
                                    html.Div(className='compact-toggle', children=[
                                        html.Button(
                                            "Local", 
                                            id='plant-local-btn', 
                                            className='toggle-option active',
                                            n_clicks=0
                                        ),
                                        html.Button(
                                            "Remote", 
                                            id='plant-remote-btn', 
                                            className='toggle-option',
                                            n_clicks=0
                                        ),
                                    ]),
                                ]),
                            ]),
                        ]),
                        
                        # Row 2: Status Info
                        html.Div(className='status-row', children=[
                            html.Div(id='status-indicator', className='status-badge status-badge-unknown', children=[
                                html.Span(className='status-dot'),
                                "Unknown"
                            ]),
                            html.Div(id='active-source-display', className='status-text', children="Source: Manual"),
                            html.Div(id='data-fetcher-status-display', className='status-text', children="API: Not connected"),
                            html.Div(id='last-update', className='status-text', children=""),
                        ]),
                    ]),
                    
                    # Hidden RadioItems for state management
                    dcc.RadioItems(
                        id='active-source-selector',
                        options=[
                            {'label': ' Manual', 'value': 'manual'},
                            {'label': ' API', 'value': 'api'}
                        ],
                        style={'display': 'none'}
                    ),
                    dcc.RadioItems(
                        id='selected-plant-selector',
                        options=[
                            {'label': ' Local', 'value': 'local'},
                            {'label': ' Remote', 'value': 'remote'}
                        ],
                        style={'display': 'none'}
                    ),
                    
                    # Plant Switch Confirmation Modal
                    html.Div(id='plant-switch-modal', className='hidden', style={
                        'position': 'fixed', 'top': '0', 'left': '0', 'width': '100%', 'height': '100%',
                        'backgroundColor': 'rgba(0,0,0,0.5)', 'zIndex': '1000', 'display': 'flex',
                        'justifyContent': 'center', 'alignItems': 'center'
                    }, children=[
                        html.Div(style={
                            'backgroundColor': 'white', 'padding': '24px', 'borderRadius': '8px',
                            'maxWidth': '400px', 'boxShadow': '0 4px 12px rgba(0,0,0,0.15)'
                        }, children=[
                            html.H3("Confirm Plant Switch", style={'marginTop': '0'}),
                            html.P("Switching plants will stop the system and flush current measurements. Continue?"),
                            html.Div(style={'display': 'flex', 'gap': '12px', 'marginTop': '20px', 'justifyContent': 'flex-end'}, children=[
                                html.Button('Cancel', id='plant-switch-cancel', className='btn btn-secondary'),
                                html.Button('Confirm', id='plant-switch-confirm', className='btn btn-primary'),
                            ]),
                        ]),
                    ]),
                    
                    # Schedule Switch Confirmation Modal
                    html.Div(id='schedule-switch-modal', className='hidden', style={
                        'position': 'fixed', 'top': '0', 'left': '0', 'width': '100%', 'height': '100%',
                        'backgroundColor': 'rgba(0,0,0,0.5)', 'zIndex': '1000', 'display': 'flex',
                        'justifyContent': 'center', 'alignItems': 'center'
                    }, children=[
                        html.Div(style={
                            'backgroundColor': 'white', 'padding': '24px', 'borderRadius': '8px',
                            'maxWidth': '400px', 'boxShadow': '0 4px 12px rgba(0,0,0,0.15)'
                        }, children=[
                            html.H3("Confirm Schedule Switch", style={'marginTop': '0'}),
                            html.P("Switching schedule source will stop the system and flush current measurements. Continue?"),
                            html.Div(style={'display': 'flex', 'gap': '12px', 'marginTop': '20px', 'justifyContent': 'flex-end'}, children=[
                                html.Button('Cancel', id='schedule-switch-cancel', className='btn btn-secondary'),
                                html.Button('Confirm', id='schedule-switch-confirm', className='btn btn-primary'),
                            ]),
                        ]),
                    ]),
                    
                    # Live Graph
                    html.Div(className='graph-container', children=[
                        dcc.Graph(id='live-graph', style={'height': '550px'}),
                    ]),
                    
                ]),  # End status tab
                
                # =========================================
                # TAB 2: MANUAL SCHEDULE
                # =========================================
                html.Div(id='manual-tab', className='hidden', children=[
                    
                    # Random Schedule Section
                    html.Div(className='card', children=[
                        html.Div(className='card-header', children=[
                            html.H3(className='card-title', children="Random Schedule Generation"),
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
                                dcc.Input(id='random-min-power', type='number', value=-1000, step=10, className='form-control'),
                            ]),
                            html.Div(className='form-group', children=[
                                html.Label("Max Power (kW)"),
                                dcc.Input(id='random-max-power', type='number', value=1000, step=10, className='form-control'),
                            ]),
                            html.Div(className='form-group', children=[
                                html.Label(""),
                                html.Button('Preview', id='random-generate-btn', n_clicks=0, className='btn btn-primary btn-block'),
                            ]),
                        ]),
                    ]),
                    
                    # CSV Upload Section
                    html.Div(className='card', children=[
                        html.Div(className='card-header', children=[
                            html.H3(className='card-title', children="CSV Upload"),
                        ]),
                        html.Div(className='form-row', children=[
                            html.Div(className='form-group', style={'flex': '2', 'minWidth': '200px'}, children=[
                                html.Label("Schedule File"),
                                dcc.Upload(
                                    id='csv-upload',
                                    children=html.Div(className='file-upload', children=[
                                        html.Span(className='file-upload-text', children=[
                                            "Drag and drop or ", html.A("select file"), " (CSV)"
                                        ])
                                    ]),
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
                        html.Div(id='csv-filename-display', style={'fontSize': '13px', 'color': '#64748b', 'marginTop': '8px'}),
                    ]),
                    
                    # Preview Section
                    html.Div(className='card', children=[
                        html.Div(className='card-header', children=[
                            html.H3(className='card-title', children="Schedule Preview"),
                            html.Div(id='manual-status', style={'fontSize': '13px', 'color': '#64748b'}),
                        ]),
                        html.Div(className='form-row', children=[
                            html.Button('Clear Preview', id='clear-preview-btn', n_clicks=0, className='btn btn-secondary'),
                            html.Button('Clear Schedule', id='clear-schedule-btn', n_clicks=0, className='btn btn-danger'),
                            html.Button('Accept Schedule', id='accept-schedule-btn', n_clicks=0, className='btn btn-success'),
                        ]),
                        dcc.Graph(id='schedule-preview', style={'height': '300px'}),
                    ]),
                    
                ]),  # End manual tab
                
                # =========================================
                # TAB 3: API SCHEDULE
                # =========================================
                html.Div(id='api-tab', className='hidden', children=[
                    
                    html.Div(className='card', children=[
                        html.Div(className='card-header', children=[
                            html.H3(className='card-title', children="Istentore API Connection"),
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
                                html.Button('Set Password', id='set-password-btn', n_clicks=0, className='btn btn-primary btn-block'),
                            ]),
                        ]),
                        html.Div(className='form-row', style={'marginTop': '12px'}, children=[
                            html.Div(className='form-group', children=[
                                html.Button('Disconnect from API', id='disconnect-api-btn', n_clicks=0, className='btn btn-danger btn-block'),
                            ]),
                        ]),
                    ]),
                    
                    html.Div(className='card', children=[
                        html.Div(className='card-header', children=[
                            html.H3(className='card-title', children="Connection Status"),
                        ]),
                        html.Div(id='api-connection-status', style={'padding': '16px'}, children=[
                            html.Div(id='api-status-text', children="No password set"),
                            html.Div(id='api-today-status', style={'marginTop': '8px', 'color': '#64748b'}),
                            html.Div(id='api-tomorrow-status', style={'marginTop': '4px', 'color': '#64748b'}),
                            html.Div(id='api-last-attempt', style={'marginTop': '4px', 'fontSize': '12px', 'color': '#94a3b8'}),
                        ]),
                    ]),
                    
                    html.Div(className='card', children=[
                        html.Div(className='card-header', children=[
                            html.H3(className='card-title', children="API Schedule Preview"),
                        ]),
                        dcc.Graph(id='api-schedule-preview', style={'height': '300px'}),
                    ]),
                    
                ]),  # End API tab
                
                # =========================================
                # TAB 4: LOGS
                # =========================================
                html.Div(id='logs-tab', className='hidden', children=[

                    html.Div(className='card', children=[
                        html.Div(className='card-header', style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}, children=[
                            html.H3(className='card-title', children="Session Logs"),
                            html.Div(style={'display': 'flex', 'gap': '12px', 'alignItems': 'center'}, children=[
                                html.Div(id='log-file-path', style={'fontSize': '13px', 'color': '#64748b'}),
                                dcc.Dropdown(
                                    id='log-file-selector',
                                    options=[],
                                    value='current_session',
                                    clearable=False,
                                    style={'width': '200px', 'fontSize': '12px'},
                                    className='compact-dropdown'
                                ),
                            ]),
                        ]),
                        html.Div(
                            id='logs-display',
                            style={
                                'height': '500px',
                                'overflowY': 'auto',
                                'backgroundColor': '#1e293b',
                                'color': '#e2e8f0',
                                'padding': '16px',
                                'fontFamily': 'monospace',
                                'fontSize': '13px',
                                'lineHeight': '1.5',
                                'borderRadius': '8px',
                                'whiteSpace': 'pre-wrap',
                                'wordWrap': 'break-word'
                            }
                        ),
                    ]),

                ]),  # End logs tab
                
            ]),  # End tab content
            
            # Hidden stores
            dcc.Store(id='preview-schedule'),
            dcc.Store(id='active-tab', data='status'),
            dcc.Store(id='system-status', data='stopped'),
            
            # Refresh interval - uses measurement_period_s from config
            dcc.Interval(id='interval-component', interval=config.get('MEASUREMENT_PERIOD_S', 1)*1000, n_intervals=0),
            
        ]),  # End app container
    ])
    
    # ============================================================
    # HELPER FUNCTIONS FOR SYSTEM CONTROL AND MEASUREMENTS
    # ============================================================
    def stop_system():
        """Stop the system by writing 0 to the enable register of the selected plant."""
        try:
            from pyModbusTCP.client import ModbusClient
            
            with shared_data['lock']:
                selected_plant = shared_data.get('selected_plant', 'local')
            
            if selected_plant == 'remote':
                host = config.get('PLANT_REMOTE_MODBUS_HOST', '10.117.133.21')
                port = config.get('PLANT_REMOTE_MODBUS_PORT', 502)
                enable_reg = config.get('PLANT_REMOTE_ENABLE_REGISTER', 10)
            else:
                host = config.get('PLANT_LOCAL_MODBUS_HOST', 'localhost')
                port = config.get('PLANT_LOCAL_MODBUS_PORT', 5020)
                enable_reg = config.get('PLANT_ENABLE_REGISTER', 10)
            
            client = ModbusClient(host=host, port=port)
            if client.open():
                client.write_single_register(enable_reg, 0)
                client.close()
                logging.info(f"Dashboard: System stopped (plant: {selected_plant})")
                return True
            else:
                logging.warning(f"Dashboard: Could not connect to {selected_plant} plant to stop system")
                return False
        except Exception as e:
            logging.error(f"Dashboard: Error stopping system: {e}")
            return False
    
    def flush_and_clear_measurements():
        """Flush measurements to CSV and clear the measurements DataFrame."""
        try:
            with shared_data['lock']:
                measurements_filename = shared_data.get('measurements_filename')
                measurements_df = shared_data.get('measurements_df', pd.DataFrame()).copy()
            
            # Write to CSV if filename exists and dataframe is not empty
            if measurements_filename and not measurements_df.empty:
                try:
                    measurements_df.to_csv(measurements_filename, index=False)
                    logging.info(f"Dashboard: Flushed {len(measurements_df)} measurements to {measurements_filename}")
                except Exception as e:
                    logging.error(f"Dashboard: Error flushing measurements to CSV: {e}")
            
            # Clear the measurements DataFrame and filename
            with shared_data['lock']:
                shared_data['measurements_df'] = pd.DataFrame()
                shared_data['measurements_filename'] = None
            
            logging.info("Dashboard: Measurements DataFrame cleared")
            return True
        except Exception as e:
            logging.error(f"Dashboard: Error clearing measurements: {e}")
            return False
    
    # ============================================================
    # TAB SWITCHING CALLBACK
    # ============================================================
    @app.callback(
        [Output('tab-status-btn', 'className'),
         Output('tab-manual-btn', 'className'),
         Output('tab-api-btn', 'className'),
         Output('tab-logs-btn', 'className'),
         Output('status-tab', 'className'),
         Output('manual-tab', 'className'),
         Output('api-tab', 'className'),
         Output('logs-tab', 'className'),
         Output('active-tab', 'data')],
        [Input('tab-status-btn', 'n_clicks'),
         Input('tab-manual-btn', 'n_clicks'),
         Input('tab-api-btn', 'n_clicks'),
         Input('tab-logs-btn', 'n_clicks')]
    )
    def switch_tab(status_clicks, manual_clicks, api_clicks, logs_clicks):
        ctx = callback_context
        if not ctx.triggered:
            return ['tab-button active', 'tab-button', 'tab-button', 'tab-button', '', 'hidden', 'hidden', 'hidden', 'status']
        
        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        if button_id == 'tab-manual-btn':
            return ['tab-button', 'tab-button active', 'tab-button', 'tab-button', 'hidden', '', 'hidden', 'hidden', 'manual']
        elif button_id == 'tab-api-btn':
            return ['tab-button', 'tab-button', 'tab-button active', 'tab-button', 'hidden', 'hidden', '', 'hidden', 'api']
        elif button_id == 'tab-logs-btn':
            return ['tab-button', 'tab-button', 'tab-button', 'tab-button active', 'hidden', 'hidden', 'hidden', '', 'logs']
        return ['tab-button active', 'tab-button', 'tab-button', 'tab-button', '', 'hidden', 'hidden', 'hidden', 'status']
    
    # ============================================================
    # RANDOM SCHEDULE PREVIEW
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
        if n_clicks == 0 or n_clicks is None:
            raise PreventUpdate
        
        try:
            today = datetime.now().date()
            start_dt = datetime.combine(today, datetime.min.time().replace(hour=start_hour, minute=start_minute))
            end_dt = datetime.combine(today, datetime.min.time().replace(hour=end_hour, minute=end_minute))
            
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            
            df = msm.generate_random_schedule(
                start_time=start_dt,
                end_time=end_dt,
                step_minutes=step,
                min_power_kw=min_power,
                max_power_kw=max_power
            )
            
            preview_json = df.reset_index().to_json(orient='split', date_format='iso')
            return preview_json, f"Preview: {len(df)} points generated"
        except Exception as e:
            logging.error(f"Error generating preview: {e}")
            raise PreventUpdate
    
    # ============================================================
    # CSV PREVIEW
    # ============================================================
    @app.callback(
        [Output('preview-schedule', 'data', allow_duplicate=True),
         Output('csv-filename-display', 'children', allow_duplicate=True)],
        [Input('csv-upload', 'contents')],
        [State('csv-start-date', 'date'),
         State('csv-start-hour', 'value'),
         State('csv-start-minute', 'value'),
         State('csv-upload', 'filename')],
        prevent_initial_call=True
    )
    def preview_csv_schedule(contents, start_date, start_hour, start_minute, filename):
        if contents is None:
            raise PreventUpdate
        
        import base64
        
        try:
            content_type, content_string = contents.split(',')
            decoded = base64.b64decode(content_string)
            
            # Use memory buffer instead of file
            from io import BytesIO
            csv_buffer = BytesIO(decoded)
            
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            start_datetime = start_datetime.replace(hour=start_hour, minute=start_minute)
            
            df = pd.read_csv(csv_buffer, parse_dates=['datetime'])
            
            if 'power_setpoint_kw' not in df.columns:
                raise ValueError("CSV must contain 'power_setpoint_kw' column")
            
            if 'reactive_power_setpoint_kvar' not in df.columns:
                df['reactive_power_setpoint_kvar'] = 0.0
            
            # Apply start time offset
            first_ts = df['datetime'].iloc[0]
            offset = start_datetime - first_ts
            df['datetime'] = df['datetime'] + offset
            df = df.set_index('datetime')
            
            preview_json = df.reset_index().to_json(orient='split', date_format='iso')
            return preview_json, f"Preview: {len(df)} points from {filename}"
        except Exception as e:
            logging.error(f"Error loading CSV: {e}")
            raise PreventUpdate
    
    # ============================================================
    # SCHEDULE PREVIEW GRAPH (DIRECT SHARED DATA ACCESS)
    # ============================================================
    @app.callback(
        Output('schedule-preview', 'figure'),
        Input('preview-schedule', 'data')
    )
    def update_schedule_preview(preview_data):
        # Read directly from shared data with brief lock
        with shared_data['lock']:
            existing_df = shared_data.get('manual_schedule_df', pd.DataFrame()).copy()
        
        preview_df = pd.DataFrame()
        if preview_data:
            try:
                preview_df = pd.read_json(StringIO(preview_data), orient='split')
                if 'datetime' in preview_df.columns:
                    preview_df['datetime'] = pd.to_datetime(preview_df['datetime'])
            except Exception as e:
                logging.error(f"Error reading preview: {e}")
        
        return create_schedule_preview_fig(existing_df, preview_df)
    
    # ============================================================
    # ACCEPT/CLEAR SCHEDULE
    # ============================================================
    @app.callback(
        [Output('csv-filename-display', 'children', allow_duplicate=True),
         Output('preview-schedule', 'data', allow_duplicate=True)],
        [Input('accept-schedule-btn', 'n_clicks'),
         Input('clear-preview-btn', 'n_clicks'),
         Input('clear-schedule-btn', 'n_clicks')],
        State('preview-schedule', 'data'),
        prevent_initial_call=True
    )
    def handle_schedule_buttons(accept_clicks, clear_preview_clicks, clear_schedule_clicks, preview_data):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
        
        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        if button_id == 'accept-schedule-btn' and accept_clicks and accept_clicks > 0:
            if not preview_data:
                return "No preview to accept", preview_data

            try:
                # Fast JSON parsing
                preview_df = pd.read_json(StringIO(preview_data), orient='split')
                if 'datetime' in preview_df.columns:
                    preview_df['datetime'] = pd.to_datetime(preview_df['datetime'])
                    preview_df = preview_df.set_index('datetime')

                # Replace schedule in shared data directly
                with shared_data['lock']:
                    shared_data['manual_schedule_df'] = preview_df
                    
                logging.info(f"Dashboard: Accepted schedule ({len(preview_df)} points)")
                # Clear the preview so the graph shows only the accepted schedule
                return f"Schedule accepted ({len(preview_df)} points)", None
            except Exception as e:
                logging.error(f"Error accepting schedule: {e}")
                return f"Error: {str(e)}", preview_data
        
        elif button_id == 'clear-preview-btn' and clear_preview_clicks and clear_preview_clicks > 0:
            return "Preview cleared", None
        
        elif button_id == 'clear-schedule-btn' and clear_schedule_clicks and clear_schedule_clicks > 0:
            # Clear the actual schedule from shared data
            try:
                with shared_data['lock']:
                    shared_data['manual_schedule_df'] = pd.DataFrame()
                logging.info("Dashboard: Manual schedule cleared")
                return "Schedule cleared", preview_data
            except Exception as e:
                logging.error(f"Error clearing schedule: {e}")
                return f"Error: {str(e)}", preview_data
        
        raise PreventUpdate
    
    # ============================================================
    # API PASSWORD SETTING
    # ============================================================
    @app.callback(
        Output('api-status-text', 'children', allow_duplicate=True),
        Input('set-password-btn', 'n_clicks'),
        State('api-password', 'value'),
        prevent_initial_call=True
    )
    def set_api_password(n_clicks, password):
        if not n_clicks or n_clicks == 0:
            raise PreventUpdate

        if not password:
            return "Error: Password cannot be empty"

        try:
            with shared_data['lock']:
                shared_data['api_password'] = password
            logging.info("Dashboard: API password set")
            return "Password set. Data fetcher will connect automatically."
        except Exception as e:
            logging.error(f"Error setting password: {e}")
            return f"Error: {str(e)}"

    # ============================================================
    # API DISCONNECT
    # ============================================================
    @app.callback(
        Output('api-status-text', 'children', allow_duplicate=True),
        Input('disconnect-api-btn', 'n_clicks'),
        prevent_initial_call=True
    )
    def disconnect_api(n_clicks):
        if not n_clicks or n_clicks == 0:
            raise PreventUpdate

        try:
            with shared_data['lock']:
                shared_data['api_password'] = None
            logging.info("Dashboard: API disconnected")
            return "Disconnected. Data fetcher will stop polling."
        except Exception as e:
            logging.error(f"Error disconnecting API: {e}")
            return f"Error: {str(e)}"
    
    # ============================================================
    # API STATUS DISPLAY (DIRECT SHARED DATA ACCESS)
    # ============================================================
    @app.callback(
        [Output('api-today-status', 'children'),
         Output('api-tomorrow-status', 'children'),
         Output('api-last-attempt', 'children')],
        Input('interval-component', 'n_intervals')
    )
    def update_api_status(n):
        # Read directly from shared data with brief lock
        with shared_data['lock']:
            status = shared_data.get('data_fetcher_status', {}).copy()
        
        today_fetched = status.get('today_fetched', False)
        tomorrow_fetched = status.get('tomorrow_fetched', False)
        today_points = status.get('today_points', 0)
        tomorrow_points = status.get('tomorrow_points', 0)
        last_attempt = status.get('last_attempt')
        error = status.get('error')
        
        today_text = f"Today: {'✓ Fetched' if today_fetched else '⏳ Pending'} ({today_points} points)"
        tomorrow_text = f"Tomorrow: {'✓ Fetched' if tomorrow_fetched else '⏳ Pending'} ({tomorrow_points} points)"
        
        if error:
            today_text += f" - Error: {error}"
        
        last_attempt_text = ""
        if last_attempt:
            try:
                dt = datetime.fromisoformat(last_attempt)
                last_attempt_text = f"Last attempt: {dt.strftime('%H:%M:%S')}"
            except:
                last_attempt_text = f"Last attempt: {last_attempt}"
        
        return today_text, tomorrow_text, last_attempt_text
    
    # ============================================================
    # API SCHEDULE PREVIEW (DIRECT SHARED DATA ACCESS)
    # ============================================================
    @app.callback(
        Output('api-schedule-preview', 'figure'),
        Input('interval-component', 'n_intervals')
    )
    def update_api_schedule_preview(n):
        # Read directly from shared data with brief lock
        with shared_data['lock']:
            api_df = shared_data.get('api_schedule_df', pd.DataFrame()).copy()
        
        if api_df.empty:
            fig = go.Figure()
            fig.add_annotation(
                text="No API schedule available. Set password to fetch.",
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
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=api_df.index,
            y=api_df['power_setpoint_kw'],
            mode='lines',
            line_shape='hv',
            name='API Schedule',
            fill='tozeroy',
            fillcolor='rgba(37, 99, 235, 0.15)',
            line=dict(color='#2563eb', width=2)
        ))
        fig.update_layout(
            margin=dict(l=50, r=20, t=70, b=30),
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.12, xanchor="center", x=0.5),
            plot_bgcolor='#ffffff',
            paper_bgcolor='#ffffff',
        )
        fig.update_xaxes(showgrid=True, gridcolor='#e2e8f0')
        fig.update_yaxes(title_text="Power (kW)", gridcolor='#e2e8f0')
        return fig
    
    # ============================================================
    # SCHEDULE SOURCE SELECTION (with confirmation modal)
    # ============================================================
    @app.callback(
        [Output('active-source-selector', 'value'),
         Output('source-manual-btn', 'className'),
         Output('source-api-btn', 'className'),
         Output('schedule-switch-modal', 'className'),
         Output('system-status', 'data', allow_duplicate=True)],
        [Input('source-manual-btn', 'n_clicks'),
         Input('source-api-btn', 'n_clicks'),
         Input('schedule-switch-cancel', 'n_clicks'),
         Input('schedule-switch-confirm', 'n_clicks')],
        [State('active-source-selector', 'value'),
         State('system-status', 'data')],
        prevent_initial_call='initial_duplicate'
    )
    def select_active_source(manual_clicks, api_clicks, cancel_clicks, confirm_clicks, current_source, current_system_status):
        ctx = callback_context
        if not ctx.triggered:
            # Read current source from shared_data on initial load
            with shared_data['lock']:
                stored_source = shared_data.get('active_schedule_source', 'manual')
            if stored_source == 'api':
                return 'api', 'toggle-option', 'toggle-option active', 'hidden', current_system_status
            else:
                return 'manual', 'toggle-option active', 'toggle-option', 'hidden', current_system_status
        
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        # Handle initial load - no system_status change
        if trigger_id == '':
            with shared_data['lock']:
                stored_source = shared_data.get('active_schedule_source', 'manual')
            if stored_source == 'api':
                return 'api', 'toggle-option', 'toggle-option active', 'hidden', current_system_status
            else:
                return 'manual', 'toggle-option active', 'toggle-option', 'hidden', current_system_status
        
        # Handle cancel button - return to current selection without changing
        if trigger_id == 'schedule-switch-cancel':
            with shared_data['lock']:
                stored_source = shared_data.get('active_schedule_source', 'manual')
            if stored_source == 'api':
                return 'api', 'toggle-option', 'toggle-option active', 'hidden', current_system_status
            else:
                return 'manual', 'toggle-option active', 'toggle-option', 'hidden', current_system_status
        
        # Handle confirm button - perform the actual switch
        if trigger_id == 'schedule-switch-confirm':
            requested_source = 'api' if current_source == 'manual' else 'manual'
            
            def perform_schedule_switch():
                try:
                    # 1. Stop the system
                    logging.info(f"Dashboard: Stopping system before switching to {requested_source} schedule...")
                    stop_system()
                    
                    # 2. Flush measurements and clear DataFrame
                    logging.info("Dashboard: Flushing and clearing measurements...")
                    flush_and_clear_measurements()
                    
                    # 3. Update active_schedule_source in shared_data
                    with shared_data['lock']:
                        shared_data['active_schedule_source'] = requested_source
                        shared_data['schedule_switching'] = False
                    
                    logging.info(f"Dashboard: Switched to {requested_source} schedule")
                    
                except Exception as e:
                    logging.error(f"Dashboard: Error during schedule switch: {e}")
                    with shared_data['lock']:
                        shared_data['schedule_switching'] = False
            
            # Run switch in background thread
            with shared_data['lock']:
                shared_data['schedule_switching'] = True
            
            switch_thread = threading.Thread(target=perform_schedule_switch)
            switch_thread.daemon = True
            switch_thread.start()
            
            # Return the new selection (will update after switch completes)
            # Also set system-status to 'stopping' since the system is being stopped
            if requested_source == 'api':
                return 'api', 'toggle-option', 'toggle-option active', 'hidden', 'stopping'
            else:
                return 'manual', 'toggle-option active', 'toggle-option', 'hidden', 'stopping'
        
        # Handle schedule option clicks - show confirmation modal when attempting to switch
        if trigger_id == 'source-api-btn' and current_source != 'api':
            return current_source, 'toggle-option active', 'toggle-option', '', current_system_status
        elif trigger_id == 'source-manual-btn' and current_source != 'manual':
            return current_source, 'toggle-option', 'toggle-option active', '', current_system_status
        
        # Default: no change (read from shared_data to ensure sync)
        with shared_data['lock']:
            stored_source = shared_data.get('active_schedule_source', 'manual')
        if stored_source == 'api':
            return 'api', 'toggle-option', 'toggle-option active', 'hidden', current_system_status
        else:
            return 'manual', 'toggle-option active', 'toggle-option', 'hidden', current_system_status
    
    # ============================================================
    # PLANT SELECTION
    # ============================================================
    @app.callback(
        [Output('selected-plant-selector', 'value'),
         Output('plant-local-btn', 'className'),
         Output('plant-remote-btn', 'className'),
         Output('plant-switch-modal', 'className'),
         Output('system-status', 'data', allow_duplicate=True)],
        [Input('plant-local-btn', 'n_clicks'),
         Input('plant-remote-btn', 'n_clicks'),
         Input('plant-switch-cancel', 'n_clicks'),
         Input('plant-switch-confirm', 'n_clicks')],
        [State('selected-plant-selector', 'value'),
         State('system-status', 'data')],
        prevent_initial_call='initial_duplicate'
    )
    def select_plant(local_clicks, remote_clicks, cancel_clicks, confirm_clicks, current_plant, current_system_status):
        ctx = callback_context
        if not ctx.triggered:
            # Read current plant from shared_data on initial load
            with shared_data['lock']:
                stored_plant = shared_data.get('selected_plant', 'local')
            if stored_plant == 'remote':
                return 'remote', 'toggle-option', 'toggle-option active', 'hidden', current_system_status
            else:
                return 'local', 'toggle-option active', 'toggle-option', 'hidden', current_system_status
        
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        # Handle initial load - no system_status change
        if trigger_id == '':
            with shared_data['lock']:
                stored_plant = shared_data.get('selected_plant', 'local')
            if stored_plant == 'remote':
                return 'remote', 'toggle-option', 'toggle-option active', 'hidden', current_system_status
            else:
                return 'local', 'toggle-option active', 'toggle-option', 'hidden', current_system_status
        
        # Handle cancel button
        if trigger_id == 'plant-switch-cancel':
            # Return to current selection without changing
            with shared_data['lock']:
                stored_plant = shared_data.get('selected_plant', 'local')
            if stored_plant == 'remote':
                return 'remote', 'toggle-option', 'toggle-option active', 'hidden', current_system_status
            else:
                return 'local', 'toggle-option active', 'toggle-option', 'hidden', current_system_status
        
        # Handle confirm button - perform the actual switch
        if trigger_id == 'plant-switch-confirm':
            requested_plant = 'remote' if current_plant == 'local' else 'local'
            
            def perform_plant_switch():
                try:
                    # 1. Stop the system and flush measurements
                    logging.info(f"Dashboard: Stopping system and flushing measurements before switching to {requested_plant} plant...")
                    stop_system()
                    flush_and_clear_measurements()
                    
                    # 2. Update shared_data with new plant selection
                    with shared_data['lock']:
                        shared_data['selected_plant'] = requested_plant
                        shared_data['plant_switching'] = False
                    
                    logging.info(f"Dashboard: Switched to {requested_plant} plant")
                    
                except Exception as e:
                    logging.error(f"Dashboard: Error during plant switch: {e}")
                    with shared_data['lock']:
                        shared_data['plant_switching'] = False
            
            # Run switch in background thread
            switch_thread = threading.Thread(target=perform_plant_switch)
            switch_thread.daemon = True
            switch_thread.start()
            
            # Return the new selection (will update after switch completes)
            # Also set system-status to 'stopping' since the system is being stopped
            if requested_plant == 'remote':
                return 'remote', 'toggle-option', 'toggle-option active', 'hidden', 'stopping'
            else:
                return 'local', 'toggle-option active', 'toggle-option', 'hidden', 'stopping'
        
        # Handle plant option clicks - show confirmation modal
        if trigger_id == 'plant-remote-btn' and current_plant != 'remote':
            return current_plant, 'toggle-option active', 'toggle-option', '', current_system_status
        elif trigger_id == 'plant-local-btn' and current_plant != 'local':
            return current_plant, 'toggle-option', 'toggle-option active', '', current_system_status
        
        # Default: no change
        with shared_data['lock']:
            stored_plant = shared_data.get('selected_plant', 'local')
        if stored_plant == 'remote':
            return 'remote', 'toggle-option', 'toggle-option active', 'hidden', current_system_status
        else:
            return 'local', 'toggle-option active', 'toggle-option', 'hidden', current_system_status
    
    # ============================================================
    # START/STOP BUTTONS - RUN IN BACKGROUND THREAD
    # ============================================================
    @app.callback(
        Output('system-status', 'data', allow_duplicate=True),
        [Input('start-button', 'n_clicks'),
         Input('stop-button', 'n_clicks')],
        prevent_initial_call=True
    )
    def handle_control_buttons(start_clicks, stop_clicks):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
        
        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        if button_id == 'start-button':
            value_to_write = 1
            logging.info("Dashboard: Start button clicked.")
            
            # Generate timestamped filename for measurements
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"data/{timestamp}_data.csv"
            
            with shared_data['lock']:
                shared_data['measurements_filename'] = filename
            
            logging.info(f"Dashboard: Measurements filename set to {filename}")
        elif button_id == 'stop-button':
            value_to_write = 0
            logging.info("Dashboard: Stop button clicked.")
            
            # Clear the measurements filename to stop writing to disk
            with shared_data['lock']:
                shared_data['measurements_filename'] = None
            
            logging.info("Dashboard: Measurements filename cleared.")
        else:
            raise PreventUpdate
        
        # Run Modbus operation in background to not block UI
        def send_command():
            from pyModbusTCP.client import ModbusClient
            
            # Read selected plant to determine which one to control
            with shared_data['lock']:
                selected_plant = shared_data.get('selected_plant', 'local')
            
            if selected_plant == 'remote':
                host = config.get('PLANT_REMOTE_MODBUS_HOST', '10.117.133.21')
                port = config.get('PLANT_REMOTE_MODBUS_PORT', 502)
                enable_reg = config.get('PLANT_REMOTE_ENABLE_REGISTER', 10)
            else:
                host = config.get('PLANT_LOCAL_MODBUS_HOST', 'localhost')
                port = config.get('PLANT_LOCAL_MODBUS_PORT', 5020)
                enable_reg = config.get('PLANT_ENABLE_REGISTER', 10)
            
            client = ModbusClient(host=host, port=port)
            if not client.open():
                logging.error(f"Dashboard: Could not connect to {selected_plant} Plant.")
                return
            
            is_ok = client.write_single_register(enable_reg, value_to_write)
            client.close()
            
            if is_ok:
                logging.info(f"Dashboard: Command sent successfully")
            else:
                logging.error("Dashboard: Failed to send command")
        
        cmd_thread = threading.Thread(target=send_command)
        cmd_thread.daemon = True
        cmd_thread.start()
        
        return 'starting' if button_id == 'start-button' else 'stopping'
    
    # ============================================================
    # MAIN STATUS AND GRAPHS UPDATE (DIRECT SHARED DATA ACCESS)
    # ============================================================
    @app.callback(
        [Output('live-graph', 'figure'),
         Output('status-indicator', 'className'),
         Output('status-indicator', 'children'),
         Output('active-source-display', 'children'),
         Output('data-fetcher-status-display', 'children'),
         Output('last-update', 'children'),
         Output('start-button', 'disabled'),
         Output('stop-button', 'disabled')],
        [Input('interval-component', 'n_intervals'),
         Input('system-status', 'data')]
    )
    def update_status_and_graphs(n, system_status):
        nonlocal last_modbus_status
        
        # Modbus status check (with timeout to prevent blocking)
        actual_status = last_modbus_status
        
        # Try to update modbus status every interval (using measurement_period_s)
        def check_modbus():
            nonlocal last_modbus_status
            from pyModbusTCP.client import ModbusClient
            
            # Read selected plant to determine which one to check
            with shared_data['lock']:
                selected_plant = shared_data.get('selected_plant', 'local')
            
            if selected_plant == 'remote':
                host = config.get('PLANT_REMOTE_MODBUS_HOST', '10.117.133.21')
                port = config.get('PLANT_REMOTE_MODBUS_PORT', 502)
                enable_reg = config.get('PLANT_REMOTE_ENABLE_REGISTER', 10)
            else:
                host = config.get('PLANT_LOCAL_MODBUS_HOST', 'localhost')
                port = config.get('PLANT_LOCAL_MODBUS_PORT', 5020)
                enable_reg = config.get('PLANT_ENABLE_REGISTER', 10)
            
            client = ModbusClient(host=host, port=port)
            try:
                if client.open():
                    regs = client.read_holding_registers(enable_reg, 1)
                    if regs:
                        last_modbus_status = regs[0]
            except:
                pass
            finally:
                try:
                    client.close()
                except:
                    pass
        
        check_thread = threading.Thread(target=check_modbus)
        check_thread.daemon = True
        check_thread.start()
        
        # Determine status display with intermediate states
        # system_status comes from button clicks ('starting', 'stopping', or None)
        # Check if we've reached the target state, otherwise show transition state
        if system_status == 'starting' and actual_status != 1:
            # Still transitioning to running
            status_text = "Starting..."
            status_class = "status-badge status-badge-starting"
            start_disabled = True
            stop_disabled = True  # Disable both during transition
        elif system_status == 'stopping' and actual_status != 0:
            # Still transitioning to stopped
            status_text = "Stopping..."
            status_class = "status-badge status-badge-stopping"
            start_disabled = True  # Disable both during transition
            stop_disabled = True
        elif actual_status == 1:
            status_text = "Running"
            status_class = "status-badge status-badge-running"
            start_disabled = True
            stop_disabled = False
        elif actual_status == 0:
            status_text = "Stopped"
            status_class = "status-badge status-badge-stopped"
            start_disabled = False
            stop_disabled = True
        else:
            status_text = "Unknown"
            status_class = "status-badge status-badge-unknown"
            start_disabled = False
            stop_disabled = False
        
        # Read all shared data with brief locks
        with shared_data['lock']:
            measurements_df = shared_data.get('measurements_df', pd.DataFrame()).copy()
            active_source = shared_data.get('active_schedule_source', 'manual')
            df_status = shared_data.get('data_fetcher_status', {}).copy()
            if active_source == 'api':
                schedule_df = shared_data.get('api_schedule_df', pd.DataFrame()).copy()
            else:
                schedule_df = shared_data.get('manual_schedule_df', pd.DataFrame()).copy()
        
        # Status text messages
        source_text = f"Source: {'API' if active_source == 'api' else 'Manual'}"
        
        if df_status.get('connected'):
            df_text = f"API: Connected"
            if df_status.get('today_fetched'):
                df_text += f" | Today: {df_status.get('today_points', 0)} pts"
            if df_status.get('tomorrow_fetched'):
                df_text += f" | Tomorrow: {df_status.get('tomorrow_points', 0)} pts"
        else:
            df_text = "API: Not connected"
        
        last_update = f"Last update: {datetime.now().strftime('%H:%M:%S')}"
        
        # Convert measurements timestamp to datetime
        if not measurements_df.empty and 'timestamp' in measurements_df.columns:
            measurements_df = measurements_df.copy()
            measurements_df['datetime'] = measurements_df['timestamp']
        
        # Create figure with subplots
        fig = make_subplots(
            rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.08,
            subplot_titles=('Active Power (kW)', 'State of Charge (pu)', 'Reactive Power (kvar)')
        )
        
        # Active Power - plot in order: setpoint, POI, battery
        if not schedule_df.empty:
            fig.add_trace(go.Scatter(
                x=schedule_df.index, y=schedule_df['power_setpoint_kw'],
                mode='lines', line_shape='hv', name='P Setpoint',
                line=dict(color='#2563eb', width=2)
            ), row=1, col=1)
        
        if not measurements_df.empty:
            # P POI (second in legend order)
            fig.add_trace(go.Scatter(
                x=measurements_df['datetime'], y=measurements_df['p_poi_kw'],
                mode='lines', line_shape='hv', name='P POI',
                line=dict(color='#0891b2', width=2, dash='dot')
            ), row=1, col=1)
            
            # P Battery (third in legend order)
            fig.add_trace(go.Scatter(
                x=measurements_df['datetime'], y=measurements_df['battery_active_power_kw'],
                mode='lines', line_shape='hv', name='P Battery',
                line=dict(color='#16a34a', width=2)
            ), row=1, col=1)
            
            # SoC
            fig.add_trace(go.Scatter(
                x=measurements_df['datetime'], y=measurements_df['soc_pu'],
                mode='lines', name='SoC',
                line=dict(color='#9333ea', width=2)
            ), row=2, col=1)
        
        # Reactive Power - plot in order: setpoint, POI, battery
        if not schedule_df.empty and 'reactive_power_setpoint_kvar' in schedule_df.columns:
            fig.add_trace(go.Scatter(
                x=schedule_df.index, y=schedule_df['reactive_power_setpoint_kvar'],
                mode='lines', line_shape='hv', name='Q Setpoint',
                line=dict(color='#ea580c', width=2)
            ), row=3, col=1)
        
        if not measurements_df.empty:
            # Q POI (second in legend order)
            fig.add_trace(go.Scatter(
                x=measurements_df['datetime'], y=measurements_df['q_poi_kvar'],
                mode='lines', line_shape='hv', name='Q POI',
                line=dict(color='#0891b2', width=2, dash='dot')
            ), row=3, col=1)
            
            # Q Battery (third in legend order)
            fig.add_trace(go.Scatter(
                x=measurements_df['datetime'], y=measurements_df['battery_reactive_power_kvar'],
                mode='lines', line_shape='hv', name='Q Battery',
                line=dict(color='#16a34a', width=2)
            ), row=3, col=1)
        
        fig.update_layout(
            uirevision='constant', showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.12, xanchor="center", x=0.5),
            margin=dict(l=60, r=20, t=100, b=40),
            plot_bgcolor='#f8f8ff', paper_bgcolor='#ffffff',
        )
        fig.update_yaxes(title_text="Power (kW)", row=1, col=1, gridcolor='#e2e8f0')
        fig.update_yaxes(title_text="SoC (pu)", row=2, col=1, gridcolor='#e2e8f0')
        fig.update_yaxes(title_text="Power (kvar)", row=3, col=1, gridcolor='#e2e8f0')
        fig.update_xaxes(title_text="Time", row=3, col=1, gridcolor='#e2e8f0')
        
        return fig, status_class, [html.Span(className='status-dot'), status_text], source_text, df_text, last_update, start_disabled, stop_disabled
    
    # ============================================================
    # LOG FILE SELECTOR OPTIONS
    # ============================================================
    @app.callback(
        Output('log-file-selector', 'options'),
        Input('interval-component', 'n_intervals')
    )
    def update_log_file_options(n):
        """Scan logs/ folder and return available log files."""
        import os

        options = [{'label': 'Current Session', 'value': 'current_session'}]

        try:
            if os.path.exists('logs'):
                log_files = []
                for filename in os.listdir('logs'):
                    if filename.endswith('.log'):
                        # Extract date from filename (format: YYYY-MM-DD_hil_scheduler.log)
                        try:
                            date_str = filename.split('_')[0]  # Get YYYY-MM-DD part
                            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                            display_name = date_obj.strftime('%Y-%m-%d')
                            log_files.append((display_name, filename))
                        except (ValueError, IndexError):
                            # If parsing fails, use filename as-is
                            log_files.append((filename, filename))

                # Sort by date (newest first)
                log_files.sort(key=lambda x: x[0], reverse=True)

                for display_name, filename in log_files:
                    options.append({'label': display_name, 'value': f'logs/{filename}'})

        except Exception as e:
            logging.error(f"Error scanning log files: {e}")

        return options

    # ============================================================
    # LOGS DISPLAY CALLBACKS
    # ============================================================
    @app.callback(
        [Output('logs-display', 'children'),
         Output('log-file-path', 'children')],
        [Input('interval-component', 'n_intervals'),
         Input('log-file-selector', 'value')],
        prevent_initial_call=True
    )
    def update_logs_display(n_intervals, selected_file):
        ctx = callback_context
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0] if ctx.triggered else None

        # Handle file selection (dropdown changes)
        if trigger_id == 'log-file-selector':
            if selected_file == 'current_session':
                # Show current session logs
                with shared_data['log_lock']:
                    session_logs = shared_data.get('session_logs', []).copy()
                    log_file_path = shared_data.get('log_file_path', '')

                log_entries = format_log_entries(session_logs)
                path_display = f"Log file: {log_file_path}" if log_file_path else "Current Session"
                return log_entries, path_display
            else:
                # Show historical log file
                try:
                    with open(selected_file, 'r', encoding='utf-8', errors='replace') as f:
                        file_content = f.read()

                    # Parse and format historical log entries
                    log_entries = parse_and_format_historical_logs(file_content)
                    path_display = f"File: {selected_file}"
                    return log_entries, path_display

                except Exception as e:
                    error_msg = f"Error reading log file: {str(e)}"
                    logging.error(error_msg)
                    return [html.Div(error_msg, style={'color': '#ef4444'})], f"Error: {selected_file}"

        # Handle interval updates (only when current session is selected)
        elif trigger_id == 'interval-component' and selected_file == 'current_session':
            with shared_data['log_lock']:
                session_logs = shared_data.get('session_logs', []).copy()
                log_file_path = shared_data.get('log_file_path', '')

            log_entries = format_log_entries(session_logs)
            path_display = f"Log file: {log_file_path}" if log_file_path else "Current Session"
            return log_entries, path_display

        # For any other case (including interval when historical file is selected), do nothing
        raise PreventUpdate
    
    # ============================================================
    # LOG FORMATTING HELPER FUNCTIONS
    # ============================================================
    def format_log_entries(log_entries):
        """Format session log entries with color coding for display."""
        formatted_entries = []
        for log in log_entries:
            level = log['level']
            timestamp = log['timestamp']
            message = log['message']

            # Color coding based on log level
            if level == 'ERROR':
                color = '#ef4444'  # Red
            elif level == 'WARNING':
                color = '#f97316'  # Orange
            elif level == 'INFO':
                color = '#22c55e'  # Green
            else:
                color = '#94a3b8'  # Gray for DEBUG

            formatted_entries.append(
                html.Div([
                    html.Span(f"[{timestamp}] ", style={'color': '#64748b'}),
                    html.Span(f"{level}: ", style={'color': color, 'fontWeight': 'bold'}),
                    html.Span(message, style={'color': '#e2e8f0'})
                ])
            )
        return formatted_entries

    def parse_and_format_historical_logs(file_content):
        """Parse historical log file content and format with color coding."""
        import re

        formatted_entries = []
        lines = file_content.split('\n')

        # Regex pattern to match log format: YYYY-MM-DD HH:MM:SS - LEVEL - message
        log_pattern = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (\w+) - (.+)'

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = re.match(log_pattern, line)
            if match:
                timestamp_str, level, message = match.groups()

                # Extract time part for display (HH:MM:SS)
                try:
                    display_time = timestamp_str.split(' ')[1]  # Get HH:MM:SS part
                except:
                    display_time = timestamp_str  # Fallback to full timestamp

                # Color coding based on log level
                if level == 'ERROR':
                    color = '#ef4444'  # Red
                elif level == 'WARNING':
                    color = '#f97316'  # Orange
                elif level == 'INFO':
                    color = '#22c55e'  # Green
                else:
                    color = '#94a3b8'  # Gray for DEBUG

                formatted_entries.append(
                    html.Div([
                        html.Span(f"[{display_time}] ", style={'color': '#64748b'}),
                        html.Span(f"{level}: ", style={'color': color, 'fontWeight': 'bold'}),
                        html.Span(message, style={'color': '#e2e8f0'})
                    ])
                )
            else:
                # If line doesn't match expected format, display as plain text
                formatted_entries.append(
                    html.Div(line, style={'color': '#e2e8f0'})
                )

        return formatted_entries

    # ============================================================
    # HELPER FUNCTIONS
    # ============================================================
    def create_schedule_preview_fig(existing_df, preview_df):
        """Create a preview figure showing existing vs preview schedule."""
        fig = go.Figure()
        
        if not existing_df.empty:
            fig.add_trace(go.Scatter(
                x=existing_df.index,
                y=existing_df['power_setpoint_kw'],
                mode='lines', line_shape='hv', name='Existing',
                line=dict(color='#94a3b8', width=2, dash='dash'), opacity=0.7
            ))
        
        if not preview_df.empty:
            # Get x data - use datetime column if available, otherwise use index
            if 'datetime' in preview_df.columns:
                x_data = preview_df['datetime']
            else:
                x_data = preview_df.index
            fig.add_trace(go.Scatter(
                x=x_data,
                y=preview_df['power_setpoint_kw'],
                mode='lines', line_shape='hv', name='Preview',
                fill='tozeroy', fillcolor='rgba(37, 99, 235, 0.15)',
                line=dict(color='#2563eb', width=2.5)
            ))
        
        if existing_df.empty and preview_df.empty:
            fig.add_annotation(
                text="No schedule loaded. Generate a preview or load a schedule.",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(size=14, color='#64748b')
            )
        
        fig.update_layout(
            margin=dict(l=50, r=20, t=70, b=30), uirevision='constant', showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.12, xanchor="center", x=0.5),
            plot_bgcolor='#ffffff', paper_bgcolor='#ffffff',
        )
        fig.update_xaxes(showgrid=True, gridcolor='#e2e8f0')
        fig.update_yaxes(title_text="Power (kW)", gridcolor='#e2e8f0')
        return fig
    
    def create_empty_fig(message):
        """Create an empty figure with a message."""
        fig = go.Figure()
        fig.add_annotation(
            text=message, xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False, font=dict(size=14, color='#64748b')
        )
        fig.update_layout(
            margin=dict(l=50, r=20, t=30, b=30),
            plot_bgcolor='#ffffff', paper_bgcolor='#ffffff',
        )
        return fig
    
    # Run the app
    def run_app():
        app.run(debug=False, threaded=True)
    
    dashboard_thread = threading.Thread(target=run_app)
    dashboard_thread.daemon = True
    dashboard_thread.start()
    
    while not shared_data['shutdown_event'].is_set():
        time.sleep(1)
    
    logging.info("Dashboard agent stopped.")


if __name__ == "__main__":
    config = {
        'PLANT_MODBUS_HOST': 'localhost',
        'PLANT_MODBUS_PORT': 5020,
        'PLANT_ENABLE_REGISTER': 10,
    }
    shared_data = {
        'lock': threading.Lock(),
        'shutdown_event': threading.Event(),
        'manual_schedule_df': pd.DataFrame(),
        'api_schedule_df': pd.DataFrame(),
        'active_schedule_source': 'manual',
        'api_password': None,
        'data_fetcher_status': {},
    }
    dashboard_agent(config, shared_data)
