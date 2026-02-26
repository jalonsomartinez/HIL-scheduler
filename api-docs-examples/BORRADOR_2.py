from pymodbus.client import ModbusTcpClient

# Configuración de conexión
IP = '10.117.133.26'    # dirección IP del dispositivo
PORT = 502              # puerto Modbus por defecto

# Crear cliente
client = ModbusTcpClient(IP, port=PORT)
connection = client.connect()

if connection:
    # Leer 1 holding register desde la dirección
    result = client.read_holding_registers(address=0, count=1)
    if not result.isError():
        print(f"Valor del holding register: {result.registers[0]}")
    else:
        print("Error al leer el registro:", result)