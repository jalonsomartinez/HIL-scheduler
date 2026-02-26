import unittest

from modbus.codec import (
    decode_engineering_value,
    encode_engineering_value,
    read_point_internal,
    write_point_internal,
)


class ModbusCodecTests(unittest.TestCase):
    def _endpoint(self, *, byte_order="big", word_order="msw_first"):
        return {"byte_order": byte_order, "word_order": word_order}

    def test_int16_scaled_roundtrip(self):
        endpoint = self._endpoint()
        point = {"format": "int16", "eng_per_count": 0.1}
        words = encode_engineering_value(endpoint, point, -123.4)
        self.assertEqual(len(words), 1)
        value = decode_engineering_value(endpoint, point, words)
        self.assertAlmostEqual(value, -123.4, places=4)

    def test_uint16_scaled_roundtrip(self):
        endpoint = self._endpoint()
        point = {"format": "uint16", "eng_per_count": 0.0001}
        words = encode_engineering_value(endpoint, point, 0.5678)
        self.assertEqual(len(words), 1)
        value = decode_engineering_value(endpoint, point, words)
        self.assertAlmostEqual(value, 0.5678, places=4)

    def test_float32_roundtrip_with_endpoint_ordering(self):
        point = {"format": "float32", "eng_per_count": 1.0}
        endpoint_a = self._endpoint(byte_order="big", word_order="msw_first")
        endpoint_b = self._endpoint(byte_order="little", word_order="lsw_first")

        words_a = encode_engineering_value(endpoint_a, point, 12.5)
        words_b = encode_engineering_value(endpoint_b, point, 12.5)
        self.assertNotEqual(words_a, words_b)

        self.assertAlmostEqual(decode_engineering_value(endpoint_a, point, words_a), 12.5, places=5)
        self.assertAlmostEqual(decode_engineering_value(endpoint_b, point, words_b), 12.5, places=5)

    def test_integer_overflow_raises(self):
        endpoint = self._endpoint()
        point = {"format": "int16", "eng_per_count": 0.1}
        with self.assertRaisesRegex(ValueError, "out of range"):
            encode_engineering_value(endpoint, point, 4000.0)

    def test_integer_quantization_truncates_toward_zero(self):
        endpoint = self._endpoint()
        point = {"format": "int16", "eng_per_count": 0.1}
        words = encode_engineering_value(endpoint, point, 12.39)
        value = decode_engineering_value(endpoint, point, words)
        self.assertAlmostEqual(value, 12.3, places=4)

    def test_internal_soc_percent_conversion_roundtrip(self):
        endpoint = {
            **self._endpoint(),
            "points": {
                "soc": {
                    "name": "soc",
                    "address": 10,
                    "format": "uint16",
                    "eng_per_count": 0.1,  # percent per count
                    "unit": "%",
                    "word_count": 1,
                }
            },
        }

        class _Client:
            def __init__(self):
                self.regs = {}

            def write_single_register(self, address, value):
                self.regs[int(address)] = int(value)
                return True

            def read_holding_registers(self, address, count):
                return [self.regs.get(int(address) + idx, 0) for idx in range(int(count))]

        client = _Client()
        self.assertTrue(write_point_internal(client, endpoint, "soc", 0.5))  # internal pu
        value = read_point_internal(client, endpoint, "soc")
        self.assertAlmostEqual(value, 0.5, places=6)

    def test_internal_voltage_v_to_kv_conversion_roundtrip(self):
        endpoint = {
            **self._endpoint(),
            "points": {
                "v_poi": {
                    "name": "v_poi",
                    "address": 20,
                    "format": "uint16",
                    "eng_per_count": 1.0,
                    "unit": "V",
                    "word_count": 1,
                }
            },
        }

        class _Client:
            def __init__(self):
                self.regs = {}

            def write_single_register(self, address, value):
                self.regs[int(address)] = int(value)
                return True

            def read_holding_registers(self, address, count):
                return [self.regs.get(int(address) + idx, 0) for idx in range(int(count))]

        client = _Client()
        self.assertTrue(write_point_internal(client, endpoint, "v_poi", 20.0))  # internal kV
        self.assertEqual(client.regs[20], 20000)
        self.assertAlmostEqual(read_point_internal(client, endpoint, "v_poi"), 20.0, places=6)


if __name__ == "__main__":
    unittest.main()
