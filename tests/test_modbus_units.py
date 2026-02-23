import unittest

from modbus_units import external_to_internal, internal_to_external, normalize_unit_token, validate_point_unit


class ModbusUnitsTests(unittest.TestCase):
    def test_normalize_percent_to_pc(self):
        self.assertEqual(normalize_unit_token("%"), "pc")

    def test_power_active_units_convert_to_internal_kw(self):
        self.assertAlmostEqual(external_to_internal("p_setpoint", "w", 1500.0), 1.5, places=6)
        self.assertAlmostEqual(external_to_internal("p_setpoint", "kw", 1.5), 1.5, places=6)
        self.assertAlmostEqual(external_to_internal("p_setpoint", "mw", 0.0015), 1.5, places=6)
        self.assertAlmostEqual(internal_to_external("p_setpoint", "w", 1.5), 1500.0, places=6)

    def test_power_reactive_units_convert_to_internal_kvar(self):
        self.assertAlmostEqual(external_to_internal("q_poi", "var", 2500.0), 2.5, places=6)
        self.assertAlmostEqual(external_to_internal("q_poi", "kvar", 2.5), 2.5, places=6)
        self.assertAlmostEqual(external_to_internal("q_poi", "mvar", 0.0025), 2.5, places=6)
        self.assertAlmostEqual(internal_to_external("q_poi", "var", 2.5), 2500.0, places=6)

    def test_soc_pc_and_pu_convert_to_internal_pu(self):
        self.assertAlmostEqual(external_to_internal("soc", "pc", 50.0), 0.5, places=6)
        self.assertAlmostEqual(external_to_internal("soc", "%", 50.0), 0.5, places=6)
        self.assertAlmostEqual(internal_to_external("soc", "pc", 0.5), 50.0, places=6)
        self.assertAlmostEqual(external_to_internal("soc", "pu", 0.5), 0.5, places=6)

    def test_voltage_v_and_kv_convert_to_internal_kv(self):
        self.assertAlmostEqual(external_to_internal("v_poi", "v", 20000.0), 20.0, places=6)
        self.assertAlmostEqual(external_to_internal("v_poi", "kv", 20.0), 20.0, places=6)
        self.assertAlmostEqual(internal_to_external("v_poi", "v", 20.0), 20000.0, places=6)

    def test_invalid_unit_for_point_raises(self):
        with self.assertRaises(ValueError):
            validate_point_unit("p_setpoint", "kv")
        with self.assertRaises(ValueError):
            validate_point_unit("v_poi", "kvar")
        with self.assertRaises(ValueError):
            validate_point_unit("soc", "kw")


if __name__ == "__main__":
    unittest.main()
