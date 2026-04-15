import logging
import time

from pyModbusTCP.server import ModbusServer

from modbus.codec import decode_engineering_value, encode_engineering_value
from modbus.units import external_to_internal, internal_to_external
from runtime.contracts import local_endpoint_uses_emulator
from runtime.soc_estimation import clamp_soc_pu, resolve_startup_soc_seed
from time_utils import get_config_tz

AGGREGATE_SETPOINT_POINT_NAMES = ("p_setpoint", "q_setpoint")
PER_PHASE_P_SETPOINT_POINT_NAMES = ("p_u_setpoint", "p_v_setpoint", "p_w_setpoint")
PER_PHASE_Q_SETPOINT_POINT_NAMES = ("q_u_setpoint", "q_v_setpoint", "q_w_setpoint")


def plant_agent(config, shared_data):
    """Run local emulation servers for LIB and VRFB simultaneously."""
    logging.info("Plant agent started.")

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    plants_cfg = config.get("PLANTS", {})
    dt_s = float(config.get("PLANT_PERIOD_S", 1.0))
    dt_h = dt_s / 3600.0
    tz = get_config_tz(config)

    servers = {}
    states = {}
    emulated_plant_ids = []

    def _ensure_seed_control_maps():
        lock = shared_data.get("lock")
        if lock is None:
            return
        with lock:
            request_map = shared_data.setdefault("local_emulator_soc_seed_request_by_plant", {})
            result_map = shared_data.setdefault("local_emulator_soc_seed_result_by_plant", {})
            for plant_id in plant_ids:
                request_map.setdefault(plant_id, None)
                result_map.setdefault(
                    plant_id,
                    {"request_id": None, "status": "idle", "soc_pu": None, "message": None},
                )

    def _read_seed_request(plant_id):
        lock = shared_data.get("lock")
        if lock is None:
            return None
        with lock:
            request_map = (shared_data.get("local_emulator_soc_seed_request_by_plant", {}) or {})
            request = request_map.get(plant_id)
            if not isinstance(request, dict):
                return None
            return dict(request)

    def _complete_seed_request(plant_id, request_id, *, status, soc_pu=None, message=None):
        lock = shared_data.get("lock")
        if lock is None:
            return
        with lock:
            request_map = shared_data.setdefault("local_emulator_soc_seed_request_by_plant", {})
            result_map = shared_data.setdefault("local_emulator_soc_seed_result_by_plant", {})
            current = request_map.get(plant_id)
            if isinstance(current, dict) and current.get("request_id") == request_id:
                request_map[plant_id] = None
                result_map[plant_id] = {
                    "request_id": request_id,
                    "status": str(status),
                    "soc_pu": (float(soc_pu) if soc_pu is not None else None),
                    "message": None if message is None else str(message),
                }

    def db_has_point(endpoint_cfg, point_name):
        return str(point_name) in dict(endpoint_cfg.get("points", {}) or {})

    def db_uses_aggregate_setpoints(endpoint_cfg):
        return all(db_has_point(endpoint_cfg, point_name) for point_name in AGGREGATE_SETPOINT_POINT_NAMES)

    def db_uses_per_phase_setpoints(endpoint_cfg):
        return all(db_has_point(endpoint_cfg, point_name) for point_name in PER_PHASE_P_SETPOINT_POINT_NAMES + PER_PHASE_Q_SETPOINT_POINT_NAMES)

    def db_read_point_eng(db, endpoint_cfg, point_name):
        if not db_has_point(endpoint_cfg, point_name):
            return None
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

    def db_write_optional_point_eng(db, endpoint_cfg, point_name, eng_value):
        if not db_has_point(endpoint_cfg, point_name):
            return False
        db_write_point_eng(db, endpoint_cfg, point_name, eng_value)
        return True

    def db_initialize_setpoints(db, endpoint_cfg):
        if db_uses_aggregate_setpoints(endpoint_cfg):
            db_write_point_eng(db, endpoint_cfg, "p_setpoint", 0.0)
            db_write_point_eng(db, endpoint_cfg, "q_setpoint", 0.0)
            return
        if db_uses_per_phase_setpoints(endpoint_cfg):
            for point_name in PER_PHASE_P_SETPOINT_POINT_NAMES + PER_PHASE_Q_SETPOINT_POINT_NAMES:
                db_write_point_eng(db, endpoint_cfg, point_name, 0.0)
            return
        raise ValueError("Local emulator endpoint must define aggregate or full per-phase setpoints.")

    def db_read_dispatch_setpoints_eng(db, endpoint_cfg):
        if db_uses_aggregate_setpoints(endpoint_cfg):
            return (
                db_read_point_eng(db, endpoint_cfg, "p_setpoint"),
                db_read_point_eng(db, endpoint_cfg, "q_setpoint"),
            )
        if db_uses_per_phase_setpoints(endpoint_cfg):
            p_values = [db_read_point_eng(db, endpoint_cfg, point_name) for point_name in PER_PHASE_P_SETPOINT_POINT_NAMES]
            q_values = [db_read_point_eng(db, endpoint_cfg, point_name) for point_name in PER_PHASE_Q_SETPOINT_POINT_NAMES]
            if any(value is None for value in p_values + q_values):
                return (None, None)
            return (sum(float(value) for value in p_values), sum(float(value) for value in q_values))
        return (None, None)

    def _resolve_local_v_poi_kv(db, endpoint_cfg, default_kv):
        points = dict(endpoint_cfg.get("points", {}) or {})
        if "v_poi_write" in points:
            return db_read_point_eng(db, endpoint_cfg, "v_poi_write")
        return default_kv

    try:
        _ensure_seed_control_maps()
        for plant_id in plant_ids:
            plant_cfg = plants_cfg.get(plant_id, {})
            local_cfg = (plant_cfg.get("modbus", {}) or {}).get("local", {})
            model = plant_cfg.get("model", {})
            power_limits = model.get("power_limits", {})
            host = local_cfg.get("host", "localhost")
            port = int(local_cfg.get("port", 5020 if plant_id == "lib" else 5021))

            if not local_endpoint_uses_emulator(config, plant_id):
                logging.info(
                    "Plant agent: skipping local emulator for %s because local host %s is non-loopback (port=%s).",
                    plant_id.upper(),
                    host,
                    port,
                )
                continue

            startup_soc_seed = resolve_startup_soc_seed(config, plant_id, tz, caller_file=__file__)
            initial_soc_pu = float(startup_soc_seed["soc_pu"])

            server = ModbusServer(host=host, port=port, no_block=True)
            server.start()
            emulated_plant_ids.append(plant_id)
            servers[plant_id] = {
                "server": server,
                "endpoint": local_cfg,
                "name": plant_cfg.get("name", plant_id.upper()),
            }

            capacity_kwh = float(model.get("capacity_kwh", 50.0))
            states[plant_id] = {
                "capacity_kwh": capacity_kwh,
                "soc_kwh": initial_soc_pu * capacity_kwh,
                "poi_voltage_kv": float(model.get("poi_voltage_kv", 20.0)),
                "p_max_kw": float(power_limits.get("p_max_kw", 1000.0)),
                "p_min_kw": float(power_limits.get("p_min_kw", -1000.0)),
                "q_max_kvar": float(power_limits.get("q_max_kvar", 600.0)),
                "q_min_kvar": float(power_limits.get("q_min_kvar", -600.0)),
            }

            db = server.data_bank
            db_write_point_eng(db, local_cfg, "enable", 0)
            db_write_optional_point_eng(db, local_cfg, "soc", initial_soc_pu)
            db_initialize_setpoints(db, local_cfg)
            db_write_point_eng(db, local_cfg, "p_battery", 0.0)
            db_write_point_eng(db, local_cfg, "q_battery", 0.0)
            db_write_point_eng(db, local_cfg, "p_poi", 0.0)
            db_write_point_eng(db, local_cfg, "q_poi", 0.0)
            db_write_optional_point_eng(db, local_cfg, "q_control_mode", 1)
            initial_v_poi_kv = _resolve_local_v_poi_kv(db, local_cfg, states[plant_id]["poi_voltage_kv"])
            if initial_v_poi_kv is not None:
                db_write_point_eng(db, local_cfg, "v_poi", initial_v_poi_kv)

            startup_seed_source = str(startup_soc_seed.get("source", "unknown"))
            startup_seed_path = startup_soc_seed.get("file_path")
            logging.info(
                "Plant agent: startup SoC seed for %s = %.4f pu (source=%s%s).",
                plant_id.upper(),
                initial_soc_pu,
                startup_seed_source,
                f" path={startup_seed_path}" if startup_seed_path else "",
            )

            logging.info("Plant emulator %s started on %s:%s", plant_id.upper(), host, port)

        if not emulated_plant_ids:
            logging.info("Plant agent: no loopback-backed local emulators configured; nothing to bind.")

        while not shared_data["shutdown_event"].is_set():
            loop_start = time.time()

            for plant_id in emulated_plant_ids:
                try:
                    entry = servers[plant_id]
                    server = entry["server"]
                    endpoint_cfg = entry["endpoint"]
                    st = states[plant_id]

                    db = server.data_bank

                    p_sp_kw, q_sp_kvar = db_read_dispatch_setpoints_eng(db, endpoint_cfg)
                    enable_value = db_read_point_eng(db, endpoint_cfg, "enable")
                    if p_sp_kw is None or q_sp_kvar is None or enable_value is None:
                        continue

                    seed_request = _read_seed_request(plant_id)
                    if seed_request:
                        request_id = seed_request.get("request_id")
                        try:
                            requested_soc_pu = float(seed_request.get("soc_pu"))
                        except (TypeError, ValueError):
                            requested_soc_pu = None

                        if request_id is None or requested_soc_pu is None:
                            _complete_seed_request(
                                plant_id,
                                request_id,
                                status="error",
                                message="invalid seed request payload",
                            )
                        elif int(enable_value) == 1:
                            _complete_seed_request(
                                plant_id,
                                request_id,
                                status="skipped",
                                message="plant enabled; refusing mid-run soc reset",
                            )
                        else:
                            requested_soc_pu = clamp_soc_pu(requested_soc_pu, st["soc_kwh"] / st["capacity_kwh"] if st["capacity_kwh"] > 0 else 0.0)
                            st["soc_kwh"] = requested_soc_pu * st["capacity_kwh"]
                            db_write_optional_point_eng(db, endpoint_cfg, "soc", requested_soc_pu)
                            _complete_seed_request(
                                plant_id,
                                request_id,
                                status="applied",
                                soc_pu=requested_soc_pu,
                                message=f"source={seed_request.get('source', 'unknown')}",
                            )
                            logging.info(
                                "Plant agent: applied local SoC seed for %s (id=%s soc=%.4f pu source=%s).",
                                plant_id.upper(),
                                request_id,
                                requested_soc_pu,
                                seed_request.get("source", "unknown"),
                            )

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
                    v_poi_kv = _resolve_local_v_poi_kv(db, endpoint_cfg, st["poi_voltage_kv"])

                    db_write_point_eng(db, endpoint_cfg, "p_battery", p_act_kw)
                    db_write_point_eng(db, endpoint_cfg, "q_battery", q_act_kvar)
                    db_write_optional_point_eng(db, endpoint_cfg, "soc", soc_pu)
                    db_write_point_eng(db, endpoint_cfg, "p_poi", p_poi_kw)
                    db_write_point_eng(db, endpoint_cfg, "q_poi", q_poi_kvar)
                    if v_poi_kv is not None:
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
