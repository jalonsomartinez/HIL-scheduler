import logging
import threading
import time
import pandas as pd
from datetime import timedelta, datetime
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
    
    New features:
    - Mode selection (Random, CSV, API)
    - Controls for each mode
    - Schedule preview graph
    """
    logging.info("Dashboard agent started.")
    
    # Suppress the default Werkzeug server logs to keep the console clean
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    app = Dash(__name__, suppress_callback_exceptions=True)
    
    # Store API password in memory (session-only)
    api_password_memory = {"password": None}
    
    app.layout = html.Div(children=[
        html.H1(children='HIL Scheduler Dashboard'),
        
        html.Div(children='''
            Real-time visualization of power setpoints, battery SoC, and POI measurements.
            Use the buttons to control the plant.
        '''),
        
        # Mode Selection Section
        html.H2("Schedule Mode Selection"),
        html.Div([
            html.Label("Select Schedule Source Mode:"),
            dcc.RadioItems(
                id='mode-selector',
                options=[
                    {'label': ' Random Schedule', 'value': 'random'},
                    {'label': ' CSV File Upload', 'value': 'csv'},
                    {'label': ' Istentore API', 'value': 'api'}
                ],
                value='random',  # Default mode
                inline=True,
                style={'marginBottom': '10px'}
            ),
        ], style={'marginBottom': '20px', 'padding': '10px', 'border': '1px solid #ccc', 'borderRadius': '5px'}),
        
        # Random Mode Controls
        html.Div(id='random-mode-controls', children=[
            html.H3("Random Schedule Settings"),
            html.Div([
                html.Label("Duration (hours):"),
                dcc.Input(id='random-duration', type='number', value=1.0, min=0.1, step=0.1),
                html.Label("Min Power (kW):", style={'marginLeft': '20px'}),
                dcc.Input(id='random-min-power', type='number', value=-1000, step=10),
                html.Label("Max Power (kW):", style={'marginLeft': '20px'}),
                dcc.Input(id='random-max-power', type='number', value=1000, step=10),
            ], style={'marginBottom': '10px'}),
            html.Button('Generate Random Schedule', id='random-generate-btn', n_clicks=0),
            # Schedule preview for Random mode
            dcc.Graph(id='random-schedule-preview', style={'marginTop': '10px'}),
        ], style={'marginBottom': '20px', 'padding': '10px', 'border': '1px solid #ccc', 'borderRadius': '5px'}),
        
        # CSV Mode Controls
        html.Div(id='csv-mode-controls', children=[
            html.H3("CSV Upload Settings"),
            html.Div([
                html.Label("Upload Schedule CSV:"),
                dcc.Upload(
                    id='csv-upload',
                    children=html.Div([
                        'Drag and Drop or ',
                        html.A('Select File')
                    ]),
                    style={
                        'width': '50%',
                        'height': '40px',
                        'lineHeight': '40px',
                        'borderWidth': '1px',
                        'borderStyle': 'dashed',
                        'borderRadius': '10px',
                        'textAlign': 'center',
                        'margin': '10px'
                    },
                    multiple=False
                ),
            ]),
            html.Div(id='csv-filename-display', style={'marginBottom': '10px'}),
            html.Label("Start Time:"),
            dcc.DatePickerSingle(
                id='csv-start-date',
                date=datetime.now().date(),
                min_date_allowed=datetime(2020, 1, 1),
                max_date_allowed=datetime(2030, 12, 31)
            ),
            dcc.Dropdown(
                id='csv-start-hour',
                options=[{'label': f'{h:02d}:00', 'value': h} for h in range(24)],
                value=datetime.now().hour,
                clearable=False,
                style={'width': '80px', 'display': 'inline-block', 'verticalAlign': 'middle', 'marginRight': '5px'}
            ),
            dcc.Dropdown(
                id='csv-start-minute',
                options=[{'label': f'{m:02d}', 'value': m} for m in range(0, 60, 5)],
                value=0,
                clearable=False,
                style={'width': '60px', 'display': 'inline-block', 'verticalAlign': 'middle'}
            ),
            html.Button('Load CSV Schedule', id='csv-load-btn', n_clicks=0, style={'marginLeft': '10px'}),
            # Schedule preview for CSV mode
            dcc.Graph(id='csv-schedule-preview', style={'marginTop': '10px'}),
        ], style={'marginBottom': '20px', 'padding': '10px', 'border': '1px solid #ccc', 'borderRadius': '5px'}),
        
        # API Mode Controls
        html.Div(id='api-mode-controls', children=[
            html.H3("Istentore API Settings"),
            html.Div([
                html.Label("API Password (session-only):"),
                dcc.Input(id='api-password', type='password', placeholder='Enter API password'),
                html.Button('Connect & Fetch', id='api-connect-btn', n_clicks=0, style={'marginLeft': '10px'}),
            ], style={'marginBottom': '10px'}),
            html.Div(id='api-status', children='Not connected'),
            # Schedule preview for API mode
            dcc.Graph(id='api-schedule-preview', style={'marginTop': '10px'}),
        ], style={'marginBottom': '20px', 'padding': '10px', 'border': '1px solid #ccc', 'borderRadius': '5px'}),
        
        # Common Controls
        html.Div([
            html.Button('Clear Schedule', id='clear-schedule-btn', n_clicks=0, 
                       style={'marginRight': '10px', 'backgroundColor': '#ffcccc'}),
            html.Div(id='mode-status', children='Current Mode: None', 
                    style={'display': 'inline-block', 'marginLeft': '20px', 'fontWeight': 'bold'}),
        ], style={'marginBottom': '20px'}),
        
        # Hidden div for button callback output
        html.Div(id='button-output-status', style={'display': 'none'}),
        
        # Controls and Indicator
        html.Div([
            html.Button(
                'Start',
                id='start-button',
                n_clicks=0,
                style={'marginRight': '10px', 'fontSize': '16px'}
            ),
            html.Button(
                'Stop',
                id='stop-button',
                n_clicks=0,
                style={'marginRight': '20px', 'fontSize': '16px'}
            ),
            html.Div(
                id='status-indicator',
                style={
                    'display': 'inline-block',
                    'padding': '10px',
                    'border': '1px solid black',
                    'borderRadius': '5px',
                    'fontWeight': 'bold'
                }
            ),
        ], style={'marginBottom': '20px', 'marginTop': '20px', 'display': 'flex', 'alignItems': 'center'}),
        
        dcc.Graph(id='live-graph'),
        dcc.Interval(
            id='interval-component',
            interval=5 * 1000,  # in milliseconds
            n_intervals=0
        ),
        # Store for uploaded file content
        dcc.Store(id='uploaded-file-content'),
    ])
    
    # Show/hide mode controls based on selection
    @app.callback(
        [Output('random-mode-controls', 'style'),
         Output('csv-mode-controls', 'style'),
         Output('api-mode-controls', 'style')],
        Input('mode-selector', 'value')
    )
    def update_mode_controls(selected_mode):
        base_style = {'marginBottom': '20px', 'padding': '10px', 'border': '1px solid #ccc', 'borderRadius': '5px'}
        hidden_style = {'display': 'none'}
        
        if selected_mode == 'random':
            return base_style, hidden_style, hidden_style
        elif selected_mode == 'csv':
            return hidden_style, base_style, hidden_style
        elif selected_mode == 'api':
            return hidden_style, hidden_style, base_style
        return hidden_style, hidden_style, hidden_style
    
    # Handle CSV upload
    @app.callback(
        [Output('uploaded-file-content', 'data'),
         Output('csv-filename-display', 'children')],
        Input('csv-upload', 'contents'),
        State('csv-upload', 'filename')
    )
    def handle_csv_upload(contents, filename):
        if contents is None:
            return None, ""
        
        # Store file content in dcc.Store (base64 encoded)
        return {'contents': contents, 'filename': filename}, f"Selected: {filename}"
    
    # Update CSV preview when schedule changes
    @app.callback(
        Output('csv-schedule-preview', 'figure'),
        Input('interval-component', 'n_intervals'),
        prevent_initial_call=False
    )
    def update_csv_preview(n):
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            if not sm.is_empty:
                return create_schedule_preview_fig(sm.schedule_df)
        return go.Figure()
    
    # Update API preview when schedule changes
    @app.callback(
        Output('api-schedule-preview', 'figure'),
        Input('interval-component', 'n_intervals'),
        prevent_initial_call=False
    )
    def update_api_preview(n):
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            if not sm.is_empty:
                return create_schedule_preview_fig(sm.schedule_df)
        return go.Figure()
    
    # Update Random preview when schedule changes
    @app.callback(
        Output('random-schedule-preview', 'figure'),
        Input('interval-component', 'n_intervals'),
        prevent_initial_call=False
    )
    def update_random_preview(n):
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            if not sm.is_empty:
                return create_schedule_preview_fig(sm.schedule_df)
        return go.Figure()
    
    # Handle Random mode generate button
    @app.callback(
        [Output('button-output-status', 'children', allow_duplicate=True),
         Output('random-mode-controls', 'children')],
        Input('random-generate-btn', 'n_clicks'),
        State('random-duration', 'value'),
        State('random-min-power', 'value'),
        State('random-max-power', 'value'),
        prevent_initial_call=True
    )
    def generate_random_schedule(n_clicks, duration, min_power, max_power):
        if n_clicks == 0:
            raise PreventUpdate
        
        logging.info(f"Dashboard: Generating random schedule (duration={duration}h, min={min_power}kW, max={max_power}kW)")
        
        # Generate random schedule using schedule_manager
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            sm._generate_random_schedule(
                start_time=datetime.now(),
                duration_h=duration,
                min_power=min_power,
                max_power=max_power,
                q_power=0.0,
                resolution_min=config.get('SCHEDULE_DEFAULT_RESOLUTION_MIN', 5)
            )
        
        return f"Random schedule generated (duration: {duration}h)", dash.no_update
    
    # Handle CSV load button
    @app.callback(
        Output('button-output-status', 'children', allow_duplicate=True),
        Input('csv-load-btn', 'n_clicks'),
        State('uploaded-file-content', 'data'),
        State('csv-start-date', 'date'),
        State('csv-start-hour', 'value'),
        State('csv-start-minute', 'value'),
        prevent_initial_call=True
    )
    def load_csv_schedule(n_clicks, file_data, start_date, start_hour, start_minute):
        if n_clicks == 0 or file_data is None:
            raise PreventUpdate
        
        # Save the uploaded file temporarily
        import base64
        
        content = file_data['contents']
        filename = file_data['filename']
        
        # Decode base64 content
        content_type, content_string = content.split(',')
        decoded = base64.b64decode(content_string)
        
        # Save to a temporary file
        temp_csv_path = f"temp_{filename}"
        with open(temp_csv_path, 'wb') as f:
            f.write(decoded)
        
        # Parse start time with hour and minute
        start_datetime = datetime.strptime(f"{start_date}", "%Y-%m-%d")
        start_datetime = start_datetime.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        
        logging.info(f"Dashboard: Loading CSV schedule from {filename} starting at {start_datetime}")
        
        # Load the CSV to get the data
        csv_df = pd.read_csv(temp_csv_path, parse_dates=['datetime'])
        
        # Calculate the offset from the first timestamp in the file
        first_ts = csv_df['datetime'].iloc[0]
        offset = start_datetime - first_ts
        
        # Apply offset to all timestamps
        csv_df['datetime'] = csv_df['datetime'] + offset
        csv_df = csv_df.set_index('datetime')
        
        # Merge with existing schedule using schedule_manager
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            sm.append_schedule_from_dict(
                dict(zip(csv_df.index.strftime('%Y-%m-%dT%H:%M:%S'), csv_df['power_setpoint_kw'])),
                default_q_kvar=csv_df.get('reactive_power_setpoint_kvar', 0).iloc[0] if 'reactive_power_setpoint_kvar' in csv_df.columns else config.get('SCHEDULE_DEFAULT_Q_POWER_KVAR', 0)
            )
        
        return f"CSV schedule loaded from {filename}"
    
    # Handle API connect button
    @app.callback(
        [Output('api-status', 'children'),
         Output('button-output-status', 'children', allow_duplicate=True)],
        Input('api-connect-btn', 'n_clicks'),
        State('api-password', 'value'),
        prevent_initial_call=True
    )
    def connect_api(n_clicks, password):
        if n_clicks == 0:
            raise PreventUpdate
        
        if not password:
            return "Error: Password required", "Error: Password required"
        
        # Store password in memory
        api_password_memory['password'] = password
        
        logging.info("Dashboard: Connecting to Istentore API")
        
        # Connect to API and fetch current day schedule
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            try:
                sm._api.set_password(password)
                sm._fetch_current_day_schedule()
                return "Connected and schedule fetched", "API mode activated"
            except Exception as e:
                logging.error(f"API connection failed: {e}")
                return f"Error: {str(e)}", f"API error: {str(e)}"
        
        return "Not connected", "Error: Schedule manager not initialized"
    
    # Handle Clear Schedule button
    @app.callback(
        Output('button-output-status', 'children', allow_duplicate=True),
        Input('clear-schedule-btn', 'n_clicks'),
        prevent_initial_call=True
    )
    def clear_schedule(n_clicks):
        if n_clicks == 0:
            raise PreventUpdate
        
        logging.info("Dashboard: Clearing schedule")
        
        # Clear mode in shared data
        if 'schedule_manager' in shared_data:
            shared_data['schedule_manager'].clear_schedule()
        
        # Clear mode indicators
        shared_data.pop('schedule_mode', None)
        shared_data.pop('schedule_mode_params', None)
        
        return "Schedule cleared"
    
    # Mode status is updated by update_graphs_and_status callback
    
    @app.callback(
        Output('button-output-status', 'children'),
        Input('start-button', 'n_clicks'),
        Input('stop-button', 'n_clicks'),
        prevent_initial_call=True
    )
    def handle_control_buttons(start_clicks, stop_clicks):
        """Handles start/stop button clicks by writing to the Plant Modbus server."""
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
        
        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        value_to_write = -1
        
        if button_id == 'start-button':
            value_to_write = 1
            logging.info("Dashboard: Start button clicked. Sending command to enable plant.")
        elif button_id == 'stop-button':
            value_to_write = 0
            logging.info("Dashboard: Stop button clicked. Sending command to disable plant.")
        
        if value_to_write in [0, 1]:
            client = ModbusClient(
                host=config["PLANT_MODBUS_HOST"],
                port=config["PLANT_MODBUS_PORT"]
            )
            if not client.open():
                logging.error("Dashboard: Could not connect to Plant to send command.")
                return "Error: Connection to Plant failed."
            
            is_ok = client.write_single_register(
                config["PLANT_ENABLE_REGISTER"],
                value_to_write
            )
            client.close()
            
            if is_ok:
                msg = f"Successfully sent command for {button_id}."
                logging.info(f"Dashboard: {msg}")
                return msg
            else:
                msg = f"Failed to send command for {button_id}."
                logging.error(f"Dashboard: {msg}")
                return f"Error: {msg}"
        
        raise PreventUpdate
    
    @app.callback(
        [Output('live-graph', 'figure'),
         Output('status-indicator', 'children'),
         Output('status-indicator', 'style'),
         Output('mode-status', 'children')],
        [Input('interval-component', 'n_intervals'),
         Input('random-generate-btn', 'n_clicks'),
         Input('csv-load-btn', 'n_clicks'),
         Input('api-connect-btn', 'n_clicks'),
         Input('clear-schedule-btn', 'n_clicks')],
        prevent_initial_call=False
    )
    def update_graphs_and_status(n, random_clicks, csv_clicks, api_clicks, clear_clicks):
        """Updates the graphs and status indicator with the latest data."""
        # --- Status Indicator Logic ---
        status_text = "Unknown"
        base_style = {
            'display': 'inline-block',
            'padding': '10px',
            'border': '1px solid black',
            'borderRadius': '5px',
            'fontWeight': 'bold'
        }
        status_style = base_style.copy()
        status_style['backgroundColor'] = 'lightgrey'
        
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
                status_text = "Running" if regs[0] == 1 else "Stopped"
                status_style['backgroundColor'] = (
                    'lightgreen' if regs[0] == 1 else 'lightcoral'
                )
            else:
                status_text = "Read Error"
                status_style['backgroundColor'] = 'orange'
        
        # --- Mode Status ---
        mode_status = "Current Mode: None"
        if 'schedule_manager' in shared_data:
            sm = shared_data['schedule_manager']
            if sm.mode:
                mode_status = f"Current Mode: {sm.mode.value}"
                if not sm.is_empty:
                    mode_status += f" ({len(sm.schedule_df)} points)"
        
        # --- Measurements Graph Logic ---
        measurements_df, schedule_df = load_and_process_data()
        
        if measurements_df.empty or schedule_df.empty:
            return go.Figure(), status_text, status_style, mode_status
        
        # Create a figure with 3 subplots that share the x-axis
        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.1,
            subplot_titles=(
                'Active Power (kW)',
                'State of Charge (pu)',
                'Reactive Power (kvar)'
            )
        )
        
        # Active Power Graph traces (row 1)
        fig.add_trace(
            go.Scatter(
                x=schedule_df['datetime'],
                y=schedule_df['power_setpoint_kw'],
                mode='lines',
                line_shape='hv',
                name='P Setpoint (Schedule)',
                line=dict(color='blue')
            ),
            row=1, col=1
        )
        fig.add_trace(
            go.Scatter(
                x=measurements_df['datetime'],
                y=measurements_df['battery_active_power_kw'],
                mode='lines',
                line_shape='hv',
                name='P Battery Actual',
                line=dict(color='green')
            ),
            row=1, col=1
        )
        fig.add_trace(
            go.Scatter(
                x=measurements_df['datetime'],
                y=measurements_df['p_poi_kw'],
                mode='lines',
                line_shape='hv',
                name='P at POI',
                line=dict(color='red', dash='dash')
            ),
            row=1, col=1
        )
        
        # SoC Graph trace (row 2)
        fig.add_trace(
            go.Scatter(
                x=measurements_df['datetime'],
                y=measurements_df['soc_pu'],
                mode='lines',
                name='SoC (pu)',
                line=dict(color='purple')
            ),
            row=2, col=1
        )
        
        # Reactive Power traces (row 3)
        if 'reactive_power_setpoint_kvar' in schedule_df.columns:
            fig.add_trace(
                go.Scatter(
                    x=schedule_df['datetime'],
                    y=schedule_df['reactive_power_setpoint_kvar'],
                    mode='lines',
                    line_shape='hv',
                    name='Q Setpoint (Schedule)',
                    line=dict(color='orange')
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
                    name='Q Battery Actual',
                    line=dict(color='green')
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
                    name='Q at POI',
                    line=dict(color='brown', dash='dash')
                ),
                row=3, col=1
            )
        
        # Update layout properties
        fig.update_layout(
            height=900,
            title_text="HIL Scheduler Status",
            uirevision='constant',
            showlegend=True
        )
        
        # Update y-axis titles
        fig.update_yaxes(title_text="Active Power (kW)", row=1, col=1)
        fig.update_yaxes(title_text="SoC (pu)", row=2, col=1)
        fig.update_yaxes(title_text="Reactive Power (kvar)", row=3, col=1)
        fig.update_xaxes(title_text="Time", row=3, col=1)
        
        return fig, status_text, status_style, mode_status
    
    def load_and_process_data():
        """Loads and preprocesses data for the graphs."""
        try:
            measurements_df = pd.read_csv(
                config["MEASUREMENTS_CSV"],
                parse_dates=['timestamp']
            )
            
            # Try to get schedule from schedule_manager first
            schedule_df = None
            if 'schedule_manager' in shared_data:
                sm = shared_data['schedule_manager']
                if not sm.is_empty:
                    schedule_df = sm.schedule_df.copy()
                    # Reset index to get 'datetime' column
                    schedule_df = schedule_df.reset_index()
                    if 'index' in schedule_df.columns:
                        schedule_df = schedule_df.rename(columns={'index': 'datetime'})
            
            # Fallback to CSV file if no schedule in memory
            if schedule_df is None or schedule_df.empty:
                try:
                    schedule_df = pd.read_csv(
                        config["SCHEDULE_SOURCE_CSV"],
                        parse_dates=['datetime']
                    )
                except FileNotFoundError:
                    schedule_df = pd.DataFrame()
            
            # Ensure 'datetime' is present in measurements_df
            if 'datetime' not in measurements_df.columns:
                measurements_df['datetime'] = measurements_df['timestamp']
            
            # Ensure reactive power column exists in schedule_df
            if not schedule_df.empty and 'reactive_power_setpoint_kvar' not in schedule_df.columns:
                schedule_df['reactive_power_setpoint_kvar'] = 0.0
            
            if not schedule_df.empty:
                # Find the end time for filtering measurements
                end_time = schedule_df['datetime'].max() + timedelta(minutes=15)
                # Filter measurements within the schedule timeframe
                measurements_df = measurements_df[measurements_df['datetime'] <= end_time]
            
            return measurements_df, schedule_df
        except Exception as e:
            logging.error(f"Error loading data for dashboard: {e}")
            return pd.DataFrame(), pd.DataFrame()
    
    def create_schedule_preview_fig(schedule_df):
        """Create a preview figure for the schedule."""
        fig = go.Figure()
        
        if not schedule_df.empty:
            fig.add_trace(
                go.Scatter(
                    x=schedule_df.index,
                    y=schedule_df['power_setpoint_kw'],
                    mode='lines',
                    line_shape='hv',
                    name='P Setpoint',
                    line=dict(color='blue')
                )
            )
            fig.update_layout(
                title="Schedule Preview",
                xaxis_title="Time",
                yaxis_title="Power (kW)",
                height=200,
                margin=dict(l=20, r=20, t=40, b=20),
                uirevision='constant'
            )
        
        return fig
    
    # Run the Dash app in a separate thread
    def run_app():
        app.run(debug=False)  # Set debug=False for production
    
    dashboard_thread = threading.Thread(target=run_app)
    dashboard_thread.daemon = True  # Allow the main program to exit
    dashboard_thread.start()
    
    while not shared_data['shutdown_event'].is_set():
        time.sleep(1)  # Keep the agent alive, letting the dashboard run
    
    logging.info("Dashboard agent stopped.")


if __name__ == "__main__":
    # Test the dashboard
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
    
    # Start dashboard
    dashboard_agent(config, shared_data)
