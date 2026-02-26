import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BASE_URL = "https://3mku48kfxf.execute-api.eu-south-2.amazonaws.com/default"
EMAIL = "i-STENTORE"
PASSWORD = "bvS7bumKj86uKNt"


class IstentoreAPI:
    def __init__(self, email=EMAIL, password=PASSWORD):
        self.email = email
        self.password = password
        self.token = None
        self.login()

    def login(self):
        """Obtiene el token de acceso desde la API."""
        url = f"{BASE_URL}/login"
        payload = {"email": self.email, "password": self.password}
        headers = {"Content-Type": "application/json"}

        r = requests.post(url, json=payload, headers=headers)
        r.raise_for_status()
        self.token = r.json()["token"]
        print("✅ Token obtenido correctamente")

    def _get_market_products(self, market_id: int, delivery_period_gte=None, delivery_period_lte=None):
        """Función interna genérica para llamar a /market_products."""
        url = f"{BASE_URL}/market_products"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        params = {"id": market_id}
        if delivery_period_gte:
            params["delivery_period_gte"] = delivery_period_gte
        if delivery_period_lte:
            params["delivery_period_lte"] = delivery_period_lte

        r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        return r.json()

    # Función genérica de posteo
    def _post_measurement(self, measurement_series_id: int, value: float, timestamp: str = None):
        """Envía un valor a la API de measurements."""
        url = f"{BASE_URL}/measurements"

        if timestamp is None:
            timestamp = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d %H:%M:%S")

        payload = {
            "measurement_series": measurement_series_id,
            "measurements": [
                {
                    "timestamp": timestamp,
                    "measurement": value
                }
            ]
        }

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        r = requests.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()



    # ------------------ DAY-AHEAD ------------------
    def day_ahead_greater_than(self, delivery_period_gte: str):
        """Devuelve la información del mercado diario para delivery_period >= delivery_period_gte."""
        return self._get_market_products(market_id=4, delivery_period_gte=delivery_period_gte)

    def day_ahead_between_periods(self, delivery_period_gte: str, delivery_period_lte: str):
        """Devuelve la información del mercado diario entre delivery_period_gte y delivery_period_lte."""
        return self._get_market_products(market_id=4, delivery_period_gte=delivery_period_gte, delivery_period_lte=delivery_period_lte)

    def day_ahead_schedule_between_periods(self, delivery_period_gte: str = None, delivery_period_lte: str = None):
        """Devuelve dos diccionarios:
        1. lib_day_ahead_setpoints_kW: sumando lib_to_vpp_kw - vpp_to_lib_kw
        2. vrfb_day_ahead_setpoints_kW: sumando vrfb_to_vpp_kw - vpp_to_vrfb_kw
        .
        Por tanto la consigna es positiva si es descarga de la batería (hacia VPP) y negativa si es carga (desde VPP).
        Esta función proprciona la información del mercado diario con delivery_periods entre los especificados.
        """
        data_list = self._get_market_products(market_id=4, delivery_period_gte=delivery_period_gte, delivery_period_lte=delivery_period_lte)
        lib_day_ahead_setpoints_kW = {}
        vrfb_day_ahead_setpoints_kW = {}

        if not data_list:
            return lib_day_ahead_setpoints_kW, vrfb_day_ahead_setpoints_kW

        data = data_list[0]
        for period in data.get("delivery_periods", []):
            delivery_period = period.get("delivery_period")
            activation = period.get("activation", [{}])[0]
            lib_day_ahead_setpoints_kW[delivery_period] = activation.get("lib_to_vpp_kw", 0.0) - activation.get("vpp_to_lib_kw", 0.0)
            vrfb_day_ahead_setpoints_kW[delivery_period] = activation.get("vrfb_to_vpp_kw", 0.0) - activation.get("vpp_to_vrfb_kw", 0.0)

        return lib_day_ahead_setpoints_kW, vrfb_day_ahead_setpoints_kW

    # ------------------ MFRR ------------------
    def mfrr_greater_than(self, delivery_period_gte: str):
        """Devuelve la información del mercado mfRR para delivery_period >= delivery_period_gte."""
        return self._get_market_products(market_id=3, delivery_period_gte=delivery_period_gte)

    def mfrr_between_periods(self, delivery_period_gte: str, delivery_period_lte: str):
        """Devuelve la información del mercado mfRR entre delivery_period_gte y delivery_period_lte."""
        return self._get_market_products(market_id=3, delivery_period_gte=delivery_period_gte, delivery_period_lte=delivery_period_lte)

    def mfrr_next_activation(self):
        """Devuelve el diccionario {delivery_period: net_power_kW} de la próxima activación mfRR."""
        now_local = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d %H:%M:%S")
        data_list = self._get_market_products(market_id=3, delivery_period_gte=now_local)

        lib_mfrr_next_activation_kW = {}
        if not data_list:
            return lib_mfrr_next_activation_kW

        market_data = data_list[0]
        delivery_periods = market_data.get("delivery_periods", [])
        if not delivery_periods:
            return lib_mfrr_next_activation_kW

        first_period = delivery_periods[0]
        delivery_period = first_period.get("delivery_period")
        activations = first_period.get("activation", [])
        if not activations:
            return lib_mfrr_next_activation_kW

        activation = activations[0]
        lib_mfrr_next_activation_kW[delivery_period] = activation.get("total_upward_kw", 0.0) - activation.get("total_downward_kw", 0.0)

        return lib_mfrr_next_activation_kW

    # ------------------ FUNCIONES ESPECÍFICAS DE POSTEO ------------------

        # ---------- LIB ----------
    def post_lib_SOC_kWh(self, value: float, timestamp: str = None):
        return self._post_measurement(4, value, timestamp)

    def post_lib_P_W(self, value: float, timestamp: str = None):
        return self._post_measurement(6, value, timestamp)

    def post_lib_Q_VAr(self, value: float, timestamp: str = None):
        return self._post_measurement(7, value, timestamp)

    def post_lib_V_V(self, value: float, timestamp: str = None):
        return self._post_measurement(8, value, timestamp)

    # ---------- VRFB ----------
    def post_vrfb_SOC_kWh(self, value: float, timestamp: str = None):
        return self._post_measurement(5, value, timestamp)

    def post_vrfb_P_W(self, value: float, timestamp: str = None):
        return self._post_measurement(11, value, timestamp)

    def post_vrfb_Q_VAr(self, value: float, timestamp: str = None):
        return self._post_measurement(10, value, timestamp)

    def post_vrfb_V_V(self, value: float, timestamp: str = None):
        return self._post_measurement(9, value, timestamp)


if __name__ == "__main__":
    api = IstentoreAPI()
    # Uso para el mercado day-ahead

    results_day_ahead_greater_than = api.day_ahead_greater_than(delivery_period_gte="2026-02-17T23:00:00+00:00")
    print(results_day_ahead_greater_than)

    # results_day_ahead_between_periods = api.day_ahead_between_periods(delivery_period_gte="2026-02-16T23:00:00+00:00", delivery_period_lte="2026-02-17T22:45:00+00:00")
    # print(results_day_ahead_between_periods)

    ######## Consultaremos así las consignas para el día siguiente.
    # Consultadas hoy (por ejemplo a las 15:00), que ya estarán disponibles als de mañana, con los delivery periods
    # de mañana entre las 00:00 y las 23:45.
    # lib_day_ahead_setpoints_kW, vrfb_day_ahead_setpoints_kW = api.day_ahead_schedule_between_periods(delivery_period_gte="2026-02-16T23:00:00+00:00", delivery_period_lte="2026-02-17T22:45:00+00:00")
    # print("LIB Day-Ahead Setpoints kW:", lib_day_ahead_setpoints_kW)
    # print("VRFB Day-Ahead Setpoints kW:", vrfb_day_ahead_setpoints_kW)


    # Uso para el mercado mfRR

    # results_mfrr_greater_than = api.mfrr_greater_than(delivery_period_gte="2026-01-18T07:45:36.775771+00:00")
    # print(results_mfrr_greater_than)

    # results_mfrr_between_periods = api.mfrr_between_periods(delivery_period_gte="2026-01-28 16:32:00",delivery_period_lte="2026-01-28 17:00:00")
    # print(results_mfrr_between_periods)

    ######## Consultaremos así la siguiente activación mfRR.
    # lib_mfrr_next_activation_kW = api.mfrr_next_activation()
    # print(lib_mfrr_next_activation_kW)

    # Uso para posteo de mediciones

    # response = api.post_lib_SOC_kWh(150.0)
    # print(response)
    #
    # response_timestamp = api.post_lib_SOC_kWh(500.0, timestamp="2026-01-28 12:00:00")
    # print(response_timestamp)


