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
                html.Button("Manual Schedule", id='tab-manual-btn', className='tab-button active', n_clicks=0),
                html.Button("API Schedule", id='tab-api-btn', className='tab-button', n_clicks=0),
                html.Button("Status & Plots", id='tab-status-btn', className='tab-button', n_clicks=0),
            ]),
            
            # Tab content
            html.Div(id='tab-content', className='tab-content', children=[
                
                # =========================================
                # TAB 1: MANUAL SCHEDULE
                # =========================================
                html.Div(id='manual-tab', children=[
                    
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
                # TAB 2: API SCHEDULE
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
                # TAB 3: STATUS & PLOTS
                # =========================================
                html.Div(id='status-tab', className='hidden', children=[
                    
                    # Active Schedule Selector
                    html.Div(className='card', children=[
                        html.Div(className='card-header', children=[
                            html.H3(className='card-title', children="Active Schedule"),
                            html.P("Select which schedule is used for setpoints", className='card-subtitle'),
                        ]),
                        html.Div(className='mode-selector', style={'marginTop': '12px'}, children=[
                            html.Label(
                                className='mode-option selected',
                                id='source-manual-option',
                                n_clicks=0,
                                children=[
                                    html.Span(className='mode-label', children="Manual Schedule"),
                                    html.Span(className='mode-description', children="Random or CSV-generated schedule"),
                                ]
                            ),
                            html.Label(
                                className='mode-option',
                                id='source-api-option',
                                n_clicks=0,
                                children=[
                                    html.Span(className='mode-label', children="API Schedule"),
                                    html.Span(className='mode-description', children="Fetched from Istentore API"),
                                ]
                            ),
                        ]),
                        dcc.RadioItems(
                            id='active-source-selector',
                            options=[
                                {'label': ' Manual', 'value': 'manual'},
                                {'label': ' API', 'value': 'api'}
                            ],
                            value='manual',
                            style={'display': 'none'}
                        ),
                    ]),
                    
                    # System Status
                    html.Div(className='status-bar', children=[
                        html.Div(id='status-indicator', className='status-indicator status-unknown', children=[
                            html.Span(className='status-dot'),
                            "Unknown"
                        ]),
                        html.Div(id='active-source-display', className='status-info', children="Source: Manual"),
                        html.Div(id='data-fetcher-status-display', className='status-info', children="API: Not connected"),
                        html.Div(id='last-update', className='status-info', children=""),
                    ]),
                    
                    # Control Buttons
                    html.Div(className='card', children=[
                        html.Div(className='form-row', children=[
                            html.Button(children=[html.Span("▶"), " Start"], id='start-button', n_clicks=0, className='btn btn-success', disabled=True),
                            html.Button(children=[html.Span("■"), " Stop"], id='stop-button', n_clicks=0, className='btn btn-danger'),
                        ]),
                    ]),
                    
                    # Live Graph
                    html.Div(className='graph-container', children=[
                        dcc.Graph(id='live-graph', style={'height': '550px'}),
                    ]),
                    
                ]),  # End status tab
                
            ]),  # End tab content
            
            # Hidden stores
            dcc.Store(id='preview-schedule'),
            dcc.Store(id='active-tab', data='manual'),
            dcc.Store(id='system-status', data='stopped'),
            
            # Refresh interval - increased to 2 seconds for better performance
            dcc.Interval(id='interval-component', interval=2*1000, n_intervals=0),
            
        ]),  # End app container
    ])
    
    # ============================================================
    # TAB SWITCHING CALLBACK
    # ============================================================
    @app.callback(
        [Output('tab-manual-btn', 'className'),
         Output('tab-api-btn', 'className'),
         Output('tab-status-btn', 'className'),
         Output('manual-tab', 'className'),
         Output('api-tab', 'className'),
         Output('status-tab', 'className'),
         Output('active-tab', 'data')],
        [Input('tab-manual-btn', 'n_clicks'),
         Input('tab-api-btn', 'n_clicks'),
         Input('tab-status-btn', 'n_clicks')]
    )
    def switch_tab(manual_clicks, api_clicks, status_clicks):
        ctx = callback_context
        if not ctx.triggered:
            return ['tab-button active', 'tab-button', 'tab-button', '', 'hidden', 'hidden', 'manual']
        
        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        if button_id == 'tab-api-btn':
            return ['tab-button', 'tab-button active', 'tab-button', 'hidden', '', 'hidden', 'api']
        elif button_id == 'tab-status-btn':
            return ['tab-button', 'tab-button', 'tab-button active', 'hidden', 'hidden', '', 'status']
        return ['tab-button active', 'tab-button', 'tab-button', '', 'hidden', 'hidden', 'manual']
    
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
    # ACTIVE SOURCE SELECTION
    # ============================================================
    @app.callback(
        [Output('active-source-selector', 'value'),
         Output('source-manual-option', 'className'),
         Output('source-api-option', 'className')],
        [Input('source-manual-option', 'n_clicks'),
         Input('source-api-option', 'n_clicks')]
    )
    def select_active_source(manual_clicks, api_clicks):
        ctx = callback_context
        if not ctx.triggered:
            return 'manual', 'mode-option selected', 'mode-option'
        
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        try:
            if trigger_id == 'source-api-option':
                with shared_data['lock']:
                    shared_data['active_schedule_source'] = 'api'
                return 'api', 'mode-option', 'mode-option selected'
            else:
                with shared_data['lock']:
                    shared_data['active_schedule_source'] = 'manual'
                return 'manual', 'mode-option selected', 'mode-option'
        except Exception as e:
            logging.error(f"Error selecting source: {e}")
            return 'manual', 'mode-option selected', 'mode-option'
    
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
            client = ModbusClient(
                host=config["PLANT_MODBUS_HOST"],
                port=config["PLANT_MODBUS_PORT"]
            )
            if not client.open():
                logging.error("Dashboard: Could not connect to Plant.")
                return
            
            is_ok = client.write_single_register(config["PLANT_ENABLE_REGISTER"], value_to_write)
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
        [Input('interval-component', 'n_intervals')]
    )
    def update_status_and_graphs(n):
        nonlocal last_modbus_status
        
        # Modbus status check (with timeout to prevent blocking)
        actual_status = last_modbus_status
        
        # Try to update modbus status occasionally
        if n % 3 == 0:  # Every 6 seconds
            def check_modbus():
                nonlocal last_modbus_status
                from pyModbusTCP.client import ModbusClient
                client = ModbusClient(
                    host=config["PLANT_MODBUS_HOST"],
                    port=config["PLANT_MODBUS_PORT"]
                )
                try:
                    if client.open():
                        regs = client.read_holding_registers(config["PLANT_ENABLE_REGISTER"], 1)
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
        
        # Determine status display
        if actual_status == 1:
            status_text = "Running"
            status_class = "status-indicator status-running"
            start_disabled = True
            stop_disabled = False
        elif actual_status == 0:
            status_text = "Stopped"
            status_class = "status-indicator status-stopped"
            start_disabled = False
            stop_disabled = True
        else:
            status_text = "Unknown"
            status_class = "status-indicator status-unknown"
            start_disabled = False
            stop_disabled = False
        
        # Read all shared data with brief locks
        with shared_data['lock']:
            active_source = shared_data.get('active_schedule_source', 'manual')
            df_status = shared_data.get('data_fetcher_status', {}).copy()
            measurements_df = shared_data.get('measurements_df', pd.DataFrame()).copy()
            if active_source == 'api':
                schedule_df = shared_data.get('api_schedule_df', pd.DataFrame()).copy()
            else:
                schedule_df = shared_data.get('manual_schedule_df', pd.DataFrame()).copy()
        
        source_text = f"Source: {'API' if active_source == 'api' else 'Manual'}"
        
        # Data fetcher status
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
            plot_bgcolor='#f5f5f0', paper_bgcolor='#ffffff',
        )
        fig.update_yaxes(title_text="Power (kW)", row=1, col=1, gridcolor='#e2e8f0')
        fig.update_yaxes(title_text="SoC (pu)", row=2, col=1, gridcolor='#e2e8f0')
        fig.update_yaxes(title_text="Power (kvar)", row=3, col=1, gridcolor='#e2e8f0')
        fig.update_xaxes(title_text="Time", row=3, col=1, gridcolor='#e2e8f0')
        
        return fig, status_class, [html.Span(className='status-dot'), status_text], source_text, df_text, last_update, start_disabled, stop_disabled
    
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
