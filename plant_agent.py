import logging
import time

from pyModbusTCP.server import ModbusServer

from modbus_codec import decode_engineering_value, encode_engineering_value
from modbus_units import external_to_internal, internal_to_external


def plant_agent(config, shared_data):
    """Run local emulation servers for LIB and VRFB simultaneously."""
    logging.info("Plant agent started.")

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    plants_cfg = config.get("PLANTS", {})
    dt_s = float(config.get("PLANT_PERIOD_S", 1.0))
    dt_h = dt_s / 3600.0
    startup_initial_soc_pu = float(config.get("STARTUP_INITIAL_SOC_PU", 0.5))

    servers = {}
    states = {}

    def db_read_point_eng(db, endpoint_cfg, point_name):
        point = endpoint_cfg["points"][point_name]
        word_count = int(point["word_count"])
        regs = db.get_holding_registers(int(point["address"]), word_count) or []
        if len(regs) != word_count:
            return None
        external_value = decode_engineering_value(endpoint_cfg, point, regs)
        return external_to_internal(point_name, point.get("unit"), external_value)

    def db_write_point_eng(db, endpoint_cfg, point_name, eng_value):
        point = endpoint_cfg["points"][point_name]
        external_value = internal_to_external(point_name, point.get("unit"), eng_value)
        words = encode_engineering_value(endpoint_cfg, point, external_value)
        db.set_holding_registers(int(point["address"]), [int(word) for word in words])

    try:
        for plant_id in plant_ids:
            plant_cfg = plants_cfg.get(plant_id, {})
            local_cfg = (plant_cfg.get("modbus", {}) or {}).get("local", {})
            model = plant_cfg.get("model", {})
            power_limits = model.get("power_limits", {})

            host = local_cfg.get("host", "localhost")
            port = int(local_cfg.get("port", 5020 if plant_id == "lib" else 5021))

            server = ModbusServer(host=host, port=port, no_block=True)
            server.start()
            servers[plant_id] = {
                "server": server,
                "endpoint": local_cfg,
                "name": plant_cfg.get("name", plant_id.upper()),
            }

            capacity_kwh = float(model.get("capacity_kwh", 50.0))
            states[plant_id] = {
                "capacity_kwh": capacity_kwh,
                "soc_kwh": startup_initial_soc_pu * capacity_kwh,
                "poi_voltage_kv": float(model.get("poi_voltage_kv", 20.0)),
                "p_max_kw": float(power_limits.get("p_max_kw", 1000.0)),
                "p_min_kw": float(power_limits.get("p_min_kw", -1000.0)),
                "q_max_kvar": float(power_limits.get("q_max_kvar", 600.0)),
                "q_min_kvar": float(power_limits.get("q_min_kvar", -600.0)),
            }

            db = server.data_bank
            db_write_point_eng(db, local_cfg, "enable", 0)
            db_write_point_eng(db, local_cfg, "soc", startup_initial_soc_pu)
            db_write_point_eng(db, local_cfg, "p_setpoint", 0.0)
            db_write_point_eng(db, local_cfg, "q_setpoint", 0.0)
            db_write_point_eng(db, local_cfg, "p_battery", 0.0)
            db_write_point_eng(db, local_cfg, "q_battery", 0.0)
            db_write_point_eng(db, local_cfg, "p_poi", 0.0)
            db_write_point_eng(db, local_cfg, "q_poi", 0.0)
            db_write_point_eng(db, local_cfg, "v_poi", states[plant_id]["poi_voltage_kv"])

            logging.info("Plant emulator %s started on %s:%s", plant_id.upper(), host, port)

        while not shared_data["shutdown_event"].is_set():
            loop_start = time.time()

            for plant_id in plant_ids:
                try:
                    entry = servers[plant_id]
                    server = entry["server"]
                    endpoint_cfg = entry["endpoint"]
                    st = states[plant_id]

                    db = server.data_bank

                    p_sp_kw = db_read_point_eng(db, endpoint_cfg, "p_setpoint")
                    q_sp_kvar = db_read_point_eng(db, endpoint_cfg, "q_setpoint")
                    enable_value = db_read_point_eng(db, endpoint_cfg, "enable")
                    if p_sp_kw is None or q_sp_kvar is None or enable_value is None:
                        continue
                    enabled = int(enable_value) == 1

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
                    v_poi_kv = st["poi_voltage_kv"]

                    db_write_point_eng(db, endpoint_cfg, "p_battery", p_act_kw)
                    db_write_point_eng(db, endpoint_cfg, "q_battery", q_act_kvar)
                    db_write_point_eng(db, endpoint_cfg, "soc", soc_pu)
                    db_write_point_eng(db, endpoint_cfg, "p_poi", p_poi_kw)
                    db_write_point_eng(db, endpoint_cfg, "q_poi", q_poi_kvar)
                    db_write_point_eng(db, endpoint_cfg, "v_poi", v_poi_kv)

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
