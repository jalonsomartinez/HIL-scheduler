# Overall functionnality
This is an app that acts as a scheduler. It reads a schedule of power setpoints and executes them in real time, sending all setpoint changes to a grid-connected battery.
It is written in python.

## Agents that will be running in parallel:

It has the following agents that will run each one in its own thread:

1) Data fetcher agent:
This agent periodically reads (every second) a power schedule from a csv file and stores it locally in a dataframe called 'schedule_final' that will be shared among the different agents.
A csv file, called 'schedule_source.csv' will be created with random data when the scheduler is started.
The last setpoint of the day will be zero always.
To generate the power schedule:
- First generate a random schedule with a resolution of 5 minutes
- Interpolate the schedule with a resolution of 1 minute.
The length of the schedule is 2 hours (configurable time).
The csv file has two columns, a column with the datetime of each setpoint and a column for the power setpoint.
The power setpoint units are kW, with a maximum of 1000kW and a minimum of -1000kW. 

2) Scheduler agent:
This agent will check the 'schedule_final' Dataframe and get the power setpoint for the current time every second.
If this setpoint is different than the previous one, it will send the setpoint to the PPC agent through a Modbus connection using the pyModbusTCP library.

3) PPC agent
This agent represents a Power Plant Controller (PPC) that is controlling the battery.
It runs at a time step of 5 seconds, which should be configurable.
The PPC agent runs a Modbus TCP server using the pyModbusTCP library. This modbus server is not the same as the battery agent modbus server, and should have an independent configuration.
The PPC modbus server has 2 registers:
- The power setpoint, that will be initialized to 0
- An enable flag (an integer value), that will be initialized to 0
At each time step it will:
- If the enable flag is equal to 0, send a power setpoint of 0 to the battery agent through Modbus.
- If the enable flag is equal to 1, get the power setpoint in the PPC modbus server and forward it to the battery agent through Modbus.

4) Battery agent:
This agent represents the battery that is applying the power schedule.
It runs at a time step of 5 seconds, which should be configurable.
The battery has a capacity of 50 kWh, which should be configurable.
The SoC (state of charge) of the battery should be tracked. Its units are kWh. 
The SoC should be kept between 0 and the capacity of the battery.
Battery SoC should not be a shared data item, it should be an internal state for the battery agent, only accessible from the outside through Modbus.
The battery agent runs a Modbus TCP server using the pyModbusTCP library to receive the power setpoint from the PPC agent. This modbus server is not the same as the PPC  agent modbus server.
Negative power setpoint for the battery means that the battery will be charged.

At each time step it will:
- Get the latest power setpoint received through Modbus
- Compute what would be the state of charge of the battery at the end of the time step if the received setpoint is applied.
- If the expected state of charge at the end of the time step is outside the boundaries, the setpoint will be limited so that the boundaries are not exceeded. The change in the setpoint should be as small as possibel in absolute value.
- The limited setpoint will be the actual setpoint that will lbe applied, and it should be saved to a Modbus register to expose it.
- The resulting State of Charge of applying the actual setpoint will be saved to a Modbus register to expose it.

Issue a log warning if the power is being limited. However, if there was power limitation the previous time step, do not issue the warning again. When the power limitation disappears, log an info message.

5) Measurement agent:
This agent represents a power measurement.
It runs at a time step of 5 seconds, which should be configurable.
It reads the following data every time step and logs it to a dataframe called 'measurements' including the timestamp:
- Original power setpoint from PPC modbus server
- Actual power setpoint applied after limitation from the battery modbus server
- State of Charge of the battery from the battery modbus server
The dataframe called 'measurements' will be written to a local file in csv format in a file called 'measurements.csv' every 5 seconds. This time should be configurable.

6) Director agent:
This agent will start all the remaining agents and run the scheduler until the last setpoint is reached.
When stopping the agents, enough time should be given to each agents to send the last setpoint, perform the last measurement, log is and write it to the measurements.csv file

7) Dashboard agent:
Include a dashboard using Dash that includes a graph with two subplots:
a) a subplot with:
    - the source power schedule found in 'schedule_source.csv'
    - The original power setpoint found in 'measurements.csv'
    - The actual power setpoint that was finally applied found in 'measurements.csv'
b) a subplot with the state of charge of the battery found in 'measurements.csv'
Both subplots should share the x-axis.
The graph should be updated each 5 seconds.
All power setpoints should be shown as step functions.

The dashboard also should have a 'start' button, a 'stop' button and an indicator, all placed above the graph.
The start button writes a 1 in the ppc_enable register so that the ppc starts forwarding setpoints to the battery.
The stop button writes a 0 in the ppc_enable register so that the ppc stops forwarding setpoints to the battery.
The indicator shows the current state of teh ppc_enable register: stopped or running


# Implementation remarks

1) MODBUS data:
In Modbus, the registers are 16 bit unsigned integers. In order to represent a signed int, such as the power setpoint, use the function get_2comp from pyModbusTCP to convert a signed int into a 16 bit unsigned integer before writing the value to the register, and to convert the register data to a signed int value when reading from the register.

Here is a list of the data types and units of the values written to Modbus, so that the correct conversions will be done when writing and reading them:
- All power setpoints:
    - All representations (python variables, logs, files, etc): float, units: kW
    - Modbus register: signed integer, units: hW
- State of charge:   
    - All other representations (python variables, logs, files, etc): float, units: kWh 
    - Modbus register: unsigned integer, units: hWh
This means that unit conversions should be done exclusively when reading from or writing to Modbus registers.

Unit definitions helpful for conversions:
1 hW = 100 W
1 kW = 1000 W
1 kW = 10 hW
1 hWh = 100 Wh
1 kWh = 1000 Wh
1 kWh = 10 hWh