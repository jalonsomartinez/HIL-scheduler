import logging
import time

from pyModbusTCP.server import ModbusServer

from utils import hw_to_kw, int_to_uint16, kw_to_hw, uint16_to_int


def plant_agent(config, shared_data):
    """Run local emulation servers for LIB and VRFB simultaneously."""
    logging.info("Plant agent started.")

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    plants_cfg = config.get("PLANTS", {})
    dt_s = float(config.get("PLANT_PERIOD_S", 1.0))
    dt_h = dt_s / 3600.0

    servers = {}
    states = {}

    try:
        for plant_id in plant_ids:
            plant_cfg = plants_cfg.get(plant_id, {})
            local_cfg = (plant_cfg.get("modbus", {}) or {}).get("local", {})
            registers = local_cfg.get("registers", {})
            model = plant_cfg.get("model", {})
            power_limits = model.get("power_limits", {})

            host = local_cfg.get("host", "localhost")
            port = int(local_cfg.get("port", 5020 if plant_id == "lib" else 5021))

            server = ModbusServer(host=host, port=port, no_block=True)
            server.start()
            servers[plant_id] = {
                "server": server,
                "registers": registers,
                "name": plant_cfg.get("name", plant_id.upper()),
            }

            capacity_kwh = float(model.get("capacity_kwh", 50.0))
            initial_soc_pu = float(model.get("initial_soc_pu", 0.5))
            states[plant_id] = {
                "capacity_kwh": capacity_kwh,
                "soc_kwh": initial_soc_pu * capacity_kwh,
                "poi_voltage_v": float(model.get("poi_voltage_v", 20000.0)),
                "p_max_kw": float(power_limits.get("p_max_kw", 1000.0)),
                "p_min_kw": float(power_limits.get("p_min_kw", -1000.0)),
                "q_max_kvar": float(power_limits.get("q_max_kvar", 600.0)),
                "q_min_kvar": float(power_limits.get("q_min_kvar", -600.0)),
            }

            db = server.data_bank
            db.set_holding_registers(int(registers.get("enable", 1)), [0])
            db.set_holding_registers(int(registers.get("soc", 281)), [int(initial_soc_pu * 10000)])
            db.set_holding_registers(int(registers.get("p_setpoint_in", 86)), [0])
            db.set_holding_registers(int(registers.get("q_setpoint_in", 88)), [0])
            db.set_holding_registers(int(registers.get("p_battery", 270)), [0])
            db.set_holding_registers(int(registers.get("q_battery", 272)), [0])
            db.set_holding_registers(int(registers.get("p_poi", 290)), [0])
            db.set_holding_registers(int(registers.get("q_poi", 292)), [0])
            db.set_holding_registers(int(registers.get("v_poi", 296)), [100])

            logging.info("Plant emulator %s started on %s:%s", plant_id.upper(), host, port)

        while not shared_data["shutdown_event"].is_set():
            loop_start = time.time()

            for plant_id in plant_ids:
                try:
                    entry = servers[plant_id]
                    server = entry["server"]
                    reg = entry["registers"]
                    st = states[plant_id]

                    db = server.data_bank

                    p_set_regs = db.get_holding_registers(int(reg.get("p_setpoint_in", 86)), 1) or [0]
                    q_set_regs = db.get_holding_registers(int(reg.get("q_setpoint_in", 88)), 1) or [0]
                    enable_regs = db.get_holding_registers(int(reg.get("enable", 1)), 1) or [0]

                    p_sp_kw = hw_to_kw(uint16_to_int(p_set_regs[0]))
                    q_sp_kvar = hw_to_kw(uint16_to_int(q_set_regs[0]))
                    enabled = int(enable_regs[0]) == 1

                    if not enabled:
                        p_sp_kw = 0.0
                        q_sp_kvar = 0.0

                    p_sp_kw = min(max(p_sp_kw, st["p_min_kw"]), st["p_max_kw"])
                    q_act_kvar = min(max(q_sp_kvar, st["q_min_kvar"]), st["q_max_kvar"])

                    # SoC-constrained active power.
                    p_act_kw = p_sp_kw
                    future_soc_kwh = st["soc_kwh"] - (p_act_kw * dt_h)
                    if future_soc_kwh > st["capacity_kwh"]:
                        p_lim_kw = (st["soc_kwh"] - st["capacity_kwh"]) / dt_h
                        p_act_kw = max(p_act_kw, p_lim_kw)
                    elif future_soc_kwh < 0:
                        p_lim_kw = st["soc_kwh"] / dt_h
                        p_act_kw = min(p_act_kw, p_lim_kw)

                    p_act_kw = min(max(p_act_kw, st["p_min_kw"]), st["p_max_kw"])

                    st["soc_kwh"] = min(
                        st["capacity_kwh"],
                        max(0.0, st["soc_kwh"] - (p_act_kw * dt_h)),
                    )
                    soc_pu = 0.0 if st["capacity_kwh"] <= 0 else st["soc_kwh"] / st["capacity_kwh"]

                    p_poi_kw = p_act_kw
                    q_poi_kvar = q_act_kvar
                    v_poi_pu = st["poi_voltage_v"] / 20000.0

                    db.set_holding_registers(int(reg.get("p_battery", 270)), [int_to_uint16(kw_to_hw(p_act_kw))])
                    db.set_holding_registers(int(reg.get("q_battery", 272)), [int_to_uint16(kw_to_hw(q_act_kvar))])
                    db.set_holding_registers(int(reg.get("soc", 281)), [int(max(0, min(65535, soc_pu * 10000)))])
                    db.set_holding_registers(int(reg.get("p_poi", 290)), [int_to_uint16(kw_to_hw(p_poi_kw))])
                    db.set_holding_registers(int(reg.get("q_poi", 292)), [int_to_uint16(kw_to_hw(q_poi_kvar))])
                    db.set_holding_registers(int(reg.get("v_poi", 296)), [int(max(0, min(65535, v_poi_pu * 100)))])

                except Exception as exc:
                    logging.error("Plant agent error (%s): %s", plant_id.upper(), exc)

            elapsed = time.time() - loop_start
            time.sleep(max(0.05, dt_s - elapsed))

    finally:
        for plant_id, entry in servers.items():
            try:
                entry["server"].stop()
                logging.info("Plant emulator %s stopped", plant_id.upper())
            except Exception:
                pass

        logging.info("Plant agent stopped.")
