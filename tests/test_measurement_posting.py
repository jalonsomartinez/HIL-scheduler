import unittest

from measurement.posting import build_post_items


class MeasurementPostingUnitTests(unittest.TestCase):
    def test_voltage_post_uses_v_poi_kv_times_1000(self):
        row = {
            "timestamp": "2026-02-23T12:00:00+01:00",
            "soc_pu": 0.5,
            "p_poi_kw": 100.0,
            "q_poi_kvar": 10.0,
            "v_poi_kV": 20.0,
        }
        model = {"capacity_kwh": 500.0, "poi_voltage_kv": 20.0}
        series = {"soc": 4, "p": 6, "q": 7, "v": 8}

        items = build_post_items(row, model, series, "Europe/Madrid")
        payload_by_metric = {metric: value for metric, _sid, value, _ts in items}
        self.assertEqual(payload_by_metric["v"], 20000.0)


if __name__ == "__main__":
    unittest.main()
