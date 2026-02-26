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
    """
    logging.info("Dashboard agent started.")

    # Suppress the default Werkzeug server logs to keep the console clean
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    app = Dash(__name__, suppress_callback_exceptions=True)

    app.layout = html.Div(children=[
        html.H1(children='HIL Scheduler Dashboard'),

        html.Div(children='''
            Real-time visualization of power setpoints and battery SoC. Use the buttons to control the PPC.
        '''),

        # Controls and Indicator
        html.Div([
            html.Button('Start', id='start-button', n_clicks=0, style={'marginRight': '10px', 'fontSize': '16px'}),
            html.Button('Stop', id='stop-button', n_clicks=0, style={'marginRight': '20px', 'fontSize': '16px'}),
            html.Div(id='status-indicator', style={
                'display': 'inline-block',
                'padding': '10px',
                'border': '1px solid black',
                'borderRadius': '5px',
                'fontWeight': 'bold'
            }),
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
            measurements_df = pd.read_csv(config["MEASUREMENTS_CSV"], parse_dates=['timestamp'])
            schedule_df = pd.read_csv(config["SCHEDULE_SOURCE_CSV"], parse_dates=['datetime'])
            
            # Ensure 'datetime' is present in measurements_df, if not create it.
            if 'datetime' not in measurements_df.columns:
                measurements_df['datetime'] = measurements_df['timestamp']

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
        """Handles start/stop button clicks by writing to the PPC Modbus server."""
        ctx = dash.callback_context
        if not ctx.triggered:
            raise PreventUpdate

        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        value_to_write = -1

        if button_id == 'start-button':
            value_to_write = 1
            logging.info("Dashboard: Start button clicked. Sending command to enable PPC.")
        elif button_id == 'stop-button':
            value_to_write = 0
            logging.info("Dashboard: Stop button clicked. Sending command to disable PPC.")

        if value_to_write in [0, 1]:
            client = ModbusClient(host=config["PPC_MODBUS_HOST"], port=config["PPC_MODBUS_PORT"])
            if not client.open():
                logging.error("Dashboard: Could not connect to PPC to send command.")
                return "Error: Connection to PPC failed."
            
            is_ok = client.write_single_register(config["PPC_ENABLE_REGISTER"], value_to_write)
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
            'display': 'inline-block', 'padding': '10px', 'border': '1px solid black',
            'borderRadius': '5px', 'fontWeight': 'bold'
        }
        status_style = base_style.copy()
        status_style['backgroundColor'] = 'lightgrey'

        client = ModbusClient(host=config["PPC_MODBUS_HOST"], port=config["PPC_MODBUS_PORT"])
        if client.open():
            regs = client.read_holding_registers(config["PPC_ENABLE_REGISTER"], 1)
            client.close()
            if regs:
                status_text = "Running" if regs[0] == 1 else "Stopped"
                status_style['backgroundColor'] = 'lightgreen' if regs[0] == 1 else 'lightcoral'
            else:
                status_text = "Read Error"
                status_style['backgroundColor'] = 'orange'
        
        # --- Graph Logic ---
        measurements_df, schedule_df = load_and_process_data()

        if measurements_df.empty or schedule_df.empty:
            return go.Figure(), status_text, status_style

        # Create a figure with 2 subplots that share the x-axis
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1)

        # Power Graph traces (top subplot)
        fig.add_trace(go.Scatter(x=schedule_df['datetime'], y=schedule_df['power_setpoint_kw'], mode='lines', line_shape='hv', name='Source Setpoint'), row=1, col=1)
        fig.add_trace(go.Scatter(x=measurements_df['datetime'], y=measurements_df['original_setpoint_kw'], mode='lines', line_shape='hv', name='Desired Setpoint'), row=1, col=1)
        fig.add_trace(go.Scatter(x=measurements_df['datetime'], y=measurements_df['actual_setpoint_kw'], mode='lines', line_shape='hv', name='Actual Setpoint'), row=1, col=1)

        # SoC Graph trace (bottom subplot)
        fig.add_trace(go.Scatter(x=measurements_df['datetime'], y=measurements_df['soc_pu'], mode='lines', name='SoC (pu)'), row=2, col=1)

        # Update layout properties
        fig.update_layout(height=700, title_text="HIL Scheduler Status", uirevision='constant')

        # Update y-axis titles
        fig.update_yaxes(title_text="Power (kW)", row=1, col=1)
        fig.update_yaxes(title_text="SoC (pu)", row=2, col=1)
        fig.update_xaxes(title_text="Time", row=2, col=1) # The title is shown on the bottom-most axis

        return fig, status_text, status_style

    # Run the Dash app in a separate thread
    def run_app():
        app.run(debug=False)  # Set debug=False for production

    dashboard_thread = threading.Thread(target=run_app)
    dashboard_thread.daemon = True  # Allow the main program to exit even if the dashboard is running
    dashboard_thread.start()

    while not shared_data['shutdown_event'].is_set():
        time.sleep(1)  # Keep the agent alive, letting the dashboard run

    logging.info("Dashboard agent stopped.")