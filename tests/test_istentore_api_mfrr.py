import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from istentore_api import IstentoreAPI


class IstentoreApiMfrrTests(unittest.TestCase):
    def test_get_mfrr_activations_parses_all_delivery_periods(self):
        api = IstentoreAPI(timezone_name="Europe/Madrid")

        sample_payload = [
            {
                "delivery_periods": [
                    {
                        "delivery_period": "2026-03-13T12:00:00+00:00",
                        "activation": [{"total_upward_kw": 50.0, "total_downward_kw": 10.0}],
                    },
                    {
                        "delivery_period": "2026-03-13T12:15:00+00:00",
                        "activation": [{"total_upward_kw": 0.0, "total_downward_kw": 20.0}],
                    },
                ]
            },
            {
                "delivery_periods": [
                    {
                        "delivery_period": "2026-03-13T12:30:00+00:00",
                        "activation": [{"total_upward_kw": 15.0, "total_downward_kw": 5.0}],
                    }
                ]
            },
        ]
        api._get_market_products = lambda **_kwargs: sample_payload  # type: ignore[assignment]

        start = datetime(2026, 3, 13, 13, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        end = datetime(2026, 3, 13, 15, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        result = api.get_mfrr_activations(start, end)

        self.assertEqual(len(result), 3)
        self.assertEqual(result["2026-03-13T12:00:00+00:00"], 40.0)
        self.assertEqual(result["2026-03-13T12:15:00+00:00"], -20.0)
        self.assertEqual(result["2026-03-13T12:30:00+00:00"], 10.0)

    def test_get_mfrr_next_activation_returns_earliest_point(self):
        api = IstentoreAPI(timezone_name="Europe/Madrid")
        api.get_mfrr_activations = lambda _start, _end: {  # type: ignore[assignment]
            "2026-03-13T12:30:00+00:00": 10.0,
            "2026-03-13T12:00:00+00:00": 40.0,
            "2026-03-13T12:15:00+00:00": -20.0,
        }

        result = api.get_mfrr_next_activation()
        self.assertEqual(result, {"2026-03-13T12:00:00+00:00": 40.0})


if __name__ == "__main__":
    unittest.main()
