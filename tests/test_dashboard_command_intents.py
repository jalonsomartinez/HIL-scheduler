import unittest

from dashboard.command_intents import command_intent_from_control_trigger, transport_switch_intent_from_confirm


class DashboardCommandIntentsTests(unittest.TestCase):
    def test_single_plant_control_mappings(self):
        self.assertEqual(
            command_intent_from_control_trigger("start-lib"),
            {"kind": "plant.start", "payload": {"plant_id": "lib"}},
        )
        self.assertEqual(
            command_intent_from_control_trigger("record-stop-vrfb"),
            {"kind": "plant.record_stop", "payload": {"plant_id": "vrfb"}},
        )
        self.assertEqual(
            command_intent_from_control_trigger("dispatch-enable-lib"),
            {"kind": "plant.dispatch_enable", "payload": {"plant_id": "lib"}},
        )
        self.assertEqual(
            command_intent_from_control_trigger("dispatch-disable-vrfb"),
            {"kind": "plant.dispatch_disable", "payload": {"plant_id": "vrfb"}},
        )

    def test_bulk_confirm_mappings(self):
        self.assertEqual(
            command_intent_from_control_trigger("bulk-control-confirm", bulk_request="start_all"),
            {"kind": "fleet.start_all", "payload": {}},
        )
        self.assertEqual(
            command_intent_from_control_trigger("bulk-control-confirm", bulk_request="stop_all"),
            {"kind": "fleet.stop_all", "payload": {}},
        )

    def test_invalid_trigger_returns_none(self):
        self.assertIsNone(command_intent_from_control_trigger("unknown-btn"))
        self.assertIsNone(command_intent_from_control_trigger("bulk-control-confirm", bulk_request=None))

    def test_transport_confirm_mapping(self):
        self.assertEqual(
            transport_switch_intent_from_confirm("transport-switch-confirm", stored_mode="local"),
            {"kind": "transport.switch", "payload": {"mode": "remote"}, "requested_mode": "remote"},
        )
        self.assertEqual(
            transport_switch_intent_from_confirm("transport-switch-confirm", stored_mode="remote"),
            {"kind": "transport.switch", "payload": {"mode": "local"}, "requested_mode": "local"},
        )
        self.assertIsNone(transport_switch_intent_from_confirm("transport-switch-cancel", stored_mode="local"))


if __name__ == "__main__":
    unittest.main()
