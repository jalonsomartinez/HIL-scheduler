import logging
import threading
import time
import pandas as pd
from datetime import timedelta
import dash
from dash import Dash, dcc, html, Input, Output
from dash.exceptions import PreventUpdate
from pyModbusTCP.client import ModbusClient
import plotly.graph_objects as go
from plotly.subplots import make_subplots


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
    
    app.layout = html.Div(children=[
        html.H1(children='HIL Scheduler Dashboard'),
        
        html.Div(children='''
            Real-time visualization of power setpoints, battery SoC, and POI measurements.
            Use the buttons to control the plant.
        '''),
        
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
        
        # Hidden div for button callback output
        html.Div(id='button-output-status', style={'display': 'none'}),
        
        dcc.Graph(id='live-graph'),
        dcc.Interval(
            id='interval-component',
            interval=5 * 1000,  # in milliseconds
            n_intervals=0
        )
    ])
    
    def load_and_process_data():
        """Loads and preprocesses data for the graphs."""
        try:
            measurements_df = pd.read_csv(
                config["MEASUREMENTS_CSV"],
                parse_dates=['timestamp']
            )
            schedule_df = pd.read_csv(
                config["SCHEDULE_SOURCE_CSV"],
                parse_dates=['datetime']
            )
            
            # Ensure 'datetime' is present in measurements_df, if not create it.
            if 'datetime' not in measurements_df.columns:
                measurements_df['datetime'] = measurements_df['timestamp']
            
            # Ensure reactive power column exists in schedule_df (for backward compatibility)
            if 'reactive_power_setpoint_kvar' not in schedule_df.columns:
                schedule_df['reactive_power_setpoint_kvar'] = 0.0
            
            # Find the end time for filtering measurements
            end_time = schedule_df['datetime'].max() + timedelta(minutes=15)
            
            # Filter measurements within the schedule timeframe
            measurements_df = measurements_df[measurements_df['datetime'] <= end_time]
            
            return measurements_df, schedule_df
        except Exception as e:
            logging.error(f"Error loading data for dashboard: {e}")
            return pd.DataFrame(), pd.DataFrame()
    
    @app.callback(
        Output('button-output-status', 'children'),
        Input('start-button', 'n_clicks'),
        Input('stop-button', 'n_clicks'),
        prevent_initial_call=True
    )
    def handle_control_buttons(start_clicks, stop_clicks):
        """Handles start/stop button clicks by writing to the Plant Modbus server."""
        ctx = dash.callback_context
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
         Output('status-indicator', 'style')],
        Input('interval-component', 'n_intervals')
    )
    def update_graphs_and_status(n):
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
        
        # --- Graph Logic ---
        measurements_df, schedule_df = load_and_process_data()
        
        if measurements_df.empty or schedule_df.empty:
            return go.Figure(), status_text, status_style
        
        # Create a figure with 3 subplots that share the x-axis
        # Row 1: Active Power (setpoint, battery actual, P_poi)
        # Row 2: SoC
        # Row 3: Reactive Power (setpoint, battery actual, Q_poi)
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
        
        return fig, status_text, status_style
    
    # Run the Dash app in a separate thread
    def run_app():
        app.run(debug=False)  # Set debug=False for production
    
    dashboard_thread = threading.Thread(target=run_app)
    dashboard_thread.daemon = True  # Allow the main program to exit
    dashboard_thread.start()
    
    while not shared_data['shutdown_event'].is_set():
        time.sleep(1)  # Keep the agent alive, letting the dashboard run
    
    logging.info("Dashboard agent stopped.")
