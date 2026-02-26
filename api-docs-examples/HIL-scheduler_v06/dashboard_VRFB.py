from dataclasses import dataclass

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from pymodbus.client import ModbusTcpClient


# =========================
# Configuración
# =========================
IP = "10.117.133.26"
PORT = 502
UNIT_ID = 1
POLL_S = 1.0  # segundos


# =========================
# Helpers
# =========================
def int16(u16_val: int) -> int:
    return u16_val - 65536 if u16_val >= 32768 else u16_val

def u16(x: int) -> int:
    return int(x) & 0xFFFF

def u32_from_regs(hi: int, lo: int) -> int:
    return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)

@dataclass
class Reg:
    name: str
    addr_excel: int     # 1..29
    typ: str            # "INT16", "UINT16", "UINT32"
    scale: float = 1.0
    unit: str = ""


# =========================
# Mapa (ajusta unidades/escala si quieres)
# =========================
REGS = [
    Reg("P_SP", 1, "INT16", 10.0, "kW"),
    Reg("Q_SP", 2, "INT16", 10.0, "kVAr"),
    Reg("P_SP_feedback", 3, "INT16", 10.0, "kW"),
    Reg("Q_SP_feedback", 4, "INT16", 10.0, "kVAr"),
    Reg("Mode_set", 5, "UINT16", 1.0, ""),
    Reg("Mode_feedback", 6, "UINT16", 1.0, ""),
    Reg("Enable_modbus_ctrl", 7, "UINT16", 1.0, ""),

    Reg("Va", 8, "UINT16", 10.0, "V"),
    Reg("Vb", 9, "UINT16", 10.0, "V"),
    Reg("Vc", 10, "UINT16", 10.0, "V"),
    Reg("Ia", 11, "UINT16", 10.0, "A"),
    Reg("Ib", 12, "UINT16", 10.0, "A"),
    Reg("Ic", 13, "UINT16", 10.0, "A"),

    Reg("P_out", 14, "INT16", 10.0, "kW"),
    Reg("Q_out", 15, "INT16", 10.0, "kVAr"),
    Reg("S_out", 16, "UINT16", 10.0, "kVA"),

    Reg("f_pll", 17, "UINT16", 100.0, "Hz"),
    Reg("power_factor", 18, "UINT16", 100.0, ""),

    Reg("Vdc", 19, "UINT16", 10.0, "V"),
    Reg("Idc", 20, "INT16", 10.0, "A"),
    Reg("Pdc", 21, "INT16", 10.0, "kW"),
    Reg("SOC", 22, "UINT16", 100.0, "%"),

    Reg("E_total_charge", 23, "UINT32", 1.0, "kWh"),      # 23-24
    Reg("E_total_discharge", 25, "UINT32", 1.0, "kWh"),   # 25-26
    Reg("E_avail_charge", 27, "UINT16", 1.0, "kWh"),
    Reg("E_avail_discharge", 28, "UINT16", 1.0, "kWh"),
    Reg("V_out_rms", 29, "UINT16", 1000.0, "kV"),
]


# =========================
# Direccionamiento
# (tu caso: address = "dirección del Excel")
# =========================
def mb_addr(addr_excel: int) -> int:
    return addr_excel


def decode(reg: Reg, raw: list[int]) -> float:
    if reg.typ == "UINT16":
        return raw[0] / reg.scale
    if reg.typ == "INT16":
        return int16(raw[0]) / reg.scale
    if reg.typ == "UINT32":
        return u32_from_regs(raw[0], raw[1]) / reg.scale
    raise ValueError(f"Tipo no soportado: {reg.typ}")


def read_hr_block(client: ModbusTcpClient, start_excel: int, count: int) -> list[int]:
    rr = client.read_holding_registers(
        address=mb_addr(start_excel),
        count=count,
        slave=UNIT_ID
    )
    if rr.isError():
        raise RuntimeError(f"Modbus read error: {rr}")
    return rr.registers


def write_hr_u16(client: ModbusTcpClient, addr_excel: int, value_u16: int) -> None:
    wr = client.write_register(
        address=mb_addr(addr_excel),
        value=value_u16,
        slave=UNIT_ID
    )
    if wr.isError():
        raise RuntimeError(f"Modbus write error: {wr}")


# =========================
# Streamlit
# =========================
st.set_page_config(page_title="Modbus TCP – Dashboard", layout="wide")
st.title("Modbus TCP – Dashboard")

# Session init
if "client" not in st.session_state:
    st.session_state["client"] = ModbusTcpClient(IP, port=PORT, timeout=2)
if "values" not in st.session_state:
    st.session_state["values"] = {}
if "status" not in st.session_state:
    st.session_state["status"] = "—"
if "raw_regs" not in st.session_state:
    st.session_state["raw_regs"] = None
if "run_poll" not in st.session_state:
    st.session_state["run_poll"] = False


# =========================
# Polling ANTES de pintar UI
# =========================
if st.session_state["run_poll"]:
    st_autorefresh(interval=int(POLL_S * 1000), key="poll_refresh")

    try:
        client = st.session_state["client"]
        if not client.connect():
            st.session_state["status"] = "❌ No conectado"
            st.session_state["raw_regs"] = None
            st.session_state["values"] = {}
        else:
            # Importante: si tu servidor tiene HR 1..29 en addresses 1..29
            regs = read_hr_block(client, start_excel=1, count=29)
            st.session_state["raw_regs"] = regs

            values = {}
            for reg in REGS:
                idx = reg.addr_excel - 1  # índice dentro del bloque (1..29 -> 0..28)
                if reg.typ == "UINT32":
                    values[reg.name] = decode(reg, regs[idx:idx+2])
                else:
                    values[reg.name] = decode(reg, regs[idx:idx+1])

            st.session_state["values"] = values
            st.session_state["status"] = "✅ Conectado"
    except Exception as e:
        st.session_state["status"] = f"⚠️ Error: {e}"
        st.session_state["raw_regs"] = None
        st.session_state["values"] = {}


# =========================
# UI
# =========================
colA, colB, colC = st.columns([2, 2, 3])

with colA:
    st.subheader("Conexión")
    st.write(f"IP: `{IP}`   Puerto: `{PORT}`")
    st.write(st.session_state["status"])

    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ Start polling", use_container_width=True, disabled=st.session_state["run_poll"]):
            st.session_state["run_poll"] = True
            st.rerun()
    with c2:
        if st.button("⏹ Stop polling", use_container_width=True, disabled=not st.session_state["run_poll"]):
            st.session_state["run_poll"] = False
            st.rerun()

    with st.expander("DEBUG: 29 HR crudos (lo que devuelve el servidor)", expanded=False):
        st.write(st.session_state["raw_regs"])

with colB:
    st.subheader("Control")
    v = st.session_state["values"]

    enable = st.toggle("Habilitar control por Modbus (HR7=1)", value=(v.get("Enable_modbus_ctrl", 0) == 1))
    if st.button("Enviar enable", use_container_width=True):
        try:
            write_hr_u16(st.session_state["client"], 7, 1 if enable else 0)
            st.success("Enable enviado")
        except Exception as e:
            st.error(str(e))

    mode_map = {"Shutdown (0)": 0, "Start (1)": 1, "Run (2)": 2}
    mode_sel = st.selectbox("Modo operación (HR5)", list(mode_map.keys()), index=2)
    if st.button("Enviar modo", use_container_width=True):
        try:
            write_hr_u16(st.session_state["client"], 5, mode_map[mode_sel])
            st.success("Modo enviado")
        except Exception as e:
            st.error(str(e))

    p_set = st.number_input("P setpoint (kW)", value=float(v.get("P_SP", 0.0)), step=0.1, format="%.2f")
    q_set = st.number_input("Q setpoint (kVAr)", value=float(v.get("Q_SP", 0.0)), step=0.1, format="%.2f")

    if st.button("Enviar P/Q", use_container_width=True):
        try:
            p_raw = int(round(p_set * 10))
            q_raw = int(round(q_set * 10))
            write_hr_u16(st.session_state["client"], 1, u16(p_raw))
            write_hr_u16(st.session_state["client"], 2, u16(q_raw))
            st.success("P/Q enviados")
        except Exception as e:
            st.error(str(e))

with colC:
    st.subheader("Medidas")
    v = st.session_state["values"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("P (kW)", f"{v.get('P_out', float('nan')):.2f}")
    m2.metric("Q (kVAr)", f"{v.get('Q_out', float('nan')):.2f}")
    m3.metric("Vdc (V)", f"{v.get('Vdc', float('nan')):.1f}")
    m4.metric("SOC (%)", f"{v.get('SOC', float('nan')):.2f}")

    st.divider()

    rows = []
    for r in [
        "Va","Vb","Vc","Ia","Ib","Ic",
        "f_pll","power_factor",
        "P_SP_feedback","Q_SP_feedback","Mode_feedback",
        "E_total_charge","E_total_discharge",
        "E_avail_charge","E_avail_discharge",
        "V_out_rms"
    ]:
        rows.append({"signal": r, "value": v.get(r, None)})
    st.dataframe(pd.DataFrame(rows), use_container_width=True)


# Para que se ejecute el dashboard tengo que: abrir una terminal, navegar a la carpeta donde está este script y ejecutar:
# streamlit run dashboard_VRFB.py
