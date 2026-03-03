import unittest

from modbus.codec import encode_point_internal_words
from modbus.grouped_reads import build_read_groups, read_points_internal_grouped


def _endpoint():
    return {
        "byte_order": "big",
        "word_order": "msw_first",
        "points": {
            "p_setpoint": {
                "name": "p_setpoint",
                "address": 1,
                "format": "int16",
                "word_count": 1,
                "unit": "kW",
                "eng_per_count": 0.1,
            },
            "q_setpoint": {
                "name": "q_setpoint",
                "address": 2,
                "format": "int16",
                "word_count": 1,
                "unit": "kvar",
                "eng_per_count": 0.1,
            },
            "soc": {
                "name": "soc",
                "address": 22,
                "format": "uint16",
                "word_count": 1,
                "unit": "pu",
                "eng_per_count": 0.0001,
            },
            "v_poi": {
                "name": "v_poi",
                "address": 29,
                "format": "uint16",
                "word_count": 1,
                "unit": "V",
                "eng_per_count": 1.0,
            },
        },
    }


class _FakeClient:
    def __init__(self, register_map):
        self.register_map = dict(register_map)
        self.read_calls = []

    def read_holding_registers(self, address, count):
        self.read_calls.append((int(address), int(count)))
        words = []
        for reg_addr in range(int(address), int(address) + int(count)):
            if reg_addr not in self.register_map:
                return None
            words.append(int(self.register_map[reg_addr]) & 0xFFFF)
        return words


class ModbusGroupedReadsTests(unittest.TestCase):
    def test_build_read_groups_merges_nearby_points(self):
        endpoint = _endpoint()
        point_names = ("p_setpoint", "q_setpoint", "soc", "v_poi")
        groups = build_read_groups(endpoint, point_names, max_gap_words=4, max_block_words=64)

        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0]["address"], 1)
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[1]["address"], 22)
        self.assertEqual(groups[1]["count"], 1)
        self.assertEqual(groups[2]["address"], 29)
        self.assertEqual(groups[2]["count"], 1)

    def test_read_points_internal_grouped_decodes_values(self):
        endpoint = _endpoint()
        point_names = ("p_setpoint", "q_setpoint", "soc", "v_poi")
        register_map = {}
        for name, value in (
            ("p_setpoint", 12.3),
            ("q_setpoint", -4.5),
            ("soc", 0.5678),
            ("v_poi", 0.4),  # internal kV -> external 400 V
        ):
            spec = endpoint["points"][name]
            words = encode_point_internal_words(endpoint, spec, value)
            address = int(spec["address"])
            for offset, word in enumerate(words):
                register_map[address + offset] = int(word)

        client = _FakeClient(register_map)
        groups = build_read_groups(endpoint, point_names, max_gap_words=4, max_block_words=64)
        values = read_points_internal_grouped(client, endpoint, point_names, read_groups=groups)

        self.assertAlmostEqual(values["p_setpoint"], 12.3, places=6)
        self.assertAlmostEqual(values["q_setpoint"], -4.5, places=6)
        self.assertAlmostEqual(values["soc"], 0.5678, places=6)
        self.assertAlmostEqual(values["v_poi"], 0.4, places=6)
        self.assertEqual(len(client.read_calls), 3)


if __name__ == "__main__":
    unittest.main()

