"""Istentore API wrapper used by fetcher and measurement agents."""

import logging
from datetime import datetime, timedelta, timezone

import requests

from time_utils import DEFAULT_TIMEZONE_NAME, get_timezone

DEFAULT_BASE_URL = "https://3mku48kfxf.execute-api.eu-south-2.amazonaws.com/default"
DEFAULT_EMAIL = "i-STENTORE"


class IstentoreAPIError(Exception):
    """Base exception for API errors."""


class AuthenticationError(IstentoreAPIError):
    """Authentication failures."""


class IstentoreAPI:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        email: str = DEFAULT_EMAIL,
        timezone_name: str = DEFAULT_TIMEZONE_NAME,
    ):
        self.base_url = base_url
        self.email = email
        self.timezone = get_timezone(timezone_name)
        self._password = None
        self._token = None

    def set_password(self, password: str):
        self._password = password
        self._token = None

    def is_authenticated(self) -> bool:
        return self._token is not None

    def login(self) -> str:
        if not self._password:
            raise AuthenticationError("Password not set. Call set_password() first.")

        url = f"{self.base_url}/login"
        payload = {"email": self.email, "password": self._password}
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            self._token = response.json()["token"]
            logging.info("Istentore API: Authentication successful")
            return self._token
        except requests.exceptions.HTTPError as exc:
            raise AuthenticationError(f"Authentication failed: {exc}") from exc
        except Exception as exc:
            raise IstentoreAPIError(f"Unexpected error during authentication: {exc}") from exc

    def _get_market_products(self, market_id: int, delivery_period_gte: str = None, delivery_period_lte: str = None) -> list:
        if not self.is_authenticated():
            self.login()

        url = f"{self.base_url}/market_products"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        params = {"id": market_id}
        if delivery_period_gte:
            params["delivery_period_gte"] = delivery_period_gte
        if delivery_period_lte:
            params["delivery_period_lte"] = delivery_period_lte

        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 401:
                logging.warning("Istentore API: Token expired, re-authenticating...")
                self._token = None
                self.login()
                return self._get_market_products(market_id, delivery_period_gte, delivery_period_lte)
            raise IstentoreAPIError(f"HTTP error: {exc}") from exc
        except Exception as exc:
            raise IstentoreAPIError(f"Unexpected error: {exc}") from exc

    def get_day_ahead_schedules(self, start_time: datetime, end_time: datetime) -> dict:
        """Return day-ahead schedules for both logical plants: {'lib': {...}, 'vrfb': {...}}."""
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=self.timezone)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=self.timezone)

        start_utc = start_time.astimezone(timezone.utc)
        end_utc = end_time.astimezone(timezone.utc)

        data_list = self._get_market_products(
            market_id=4,
            delivery_period_gte=start_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            delivery_period_lte=end_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        )

        result = {"lib": {}, "vrfb": {}}
        if not data_list:
            return result

        market_data = data_list[0]
        for period in market_data.get("delivery_periods", []):
            delivery_period_str = period.get("delivery_period")
            if not delivery_period_str:
                continue

            delivery_dt_utc = datetime.strptime(
                delivery_period_str.replace("+00:00", ""),
                "%Y-%m-%dT%H:%M:%S",
            ).replace(tzinfo=timezone.utc)

            activation = (period.get("activation") or [{}])[0]
            result["lib"][delivery_dt_utc.isoformat()] = (
                float(activation.get("lib_to_vpp_kw", 0.0)) - float(activation.get("vpp_to_lib_kw", 0.0))
            )
            result["vrfb"][delivery_dt_utc.isoformat()] = (
                float(activation.get("vrfb_to_vpp_kw", 0.0)) - float(activation.get("vpp_to_vrfb_kw", 0.0))
            )

        logging.info(
            "Istentore API: Fetched day-ahead setpoints LIB=%s VRFB=%s",
            len(result["lib"]),
            len(result["vrfb"]),
        )
        return result

    def get_day_ahead_schedule(self, start_time: datetime, end_time: datetime) -> dict:
        """Compatibility wrapper: returns LIB schedule only."""
        schedules = self.get_day_ahead_schedules(start_time, end_time)
        return schedules.get("lib", {})

    def get_mfrr_next_activation(self) -> dict:
        now_utc = datetime.now(self.timezone).astimezone(timezone.utc)
        data_list = self._get_market_products(market_id=3, delivery_period_gte=now_utc.isoformat())

        schedule = {}
        if not data_list:
            return schedule

        market_data = data_list[0]
        delivery_periods = market_data.get("delivery_periods", [])
        if not delivery_periods:
            return schedule

        first_period = delivery_periods[0]
        delivery_period_api = first_period.get("delivery_period")
        if not delivery_period_api:
            return schedule

        delivery_dt_utc = datetime.strptime(
            delivery_period_api.replace("+00:00", ""),
            "%Y-%m-%dT%H:%M:%S",
        ).replace(tzinfo=timezone.utc)

        activation = (first_period.get("activation") or [{}])[0]
        schedule[delivery_dt_utc.isoformat()] = (
            float(activation.get("total_upward_kw", 0.0)) - float(activation.get("total_downward_kw", 0.0))
        )
        return schedule

    def _format_timestamp_iso_utc(self, timestamp=None) -> str:
        if timestamp is None:
            dt_value = datetime.now(timezone.utc)
        elif isinstance(timestamp, datetime):
            dt_value = timestamp
        else:
            text = str(timestamp).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt_value = datetime.fromisoformat(text)

        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=self.timezone)

        return dt_value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    def post_measurement(self, measurement_series_id: int, value: float, timestamp=None) -> dict:
        if not self.is_authenticated():
            self.login()

        url = f"{self.base_url}/measurements"
        payload = {
            "measurement_series": int(measurement_series_id),
            "measurements": [
                {
                    "timestamp": self._format_timestamp_iso_utc(timestamp),
                    "measurement": float(value),
                }
            ],
        }

        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            try:
                response = requests.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 401 and attempt == 0:
                    logging.warning("Istentore API: Token expired while posting, re-authenticating...")
                    self._token = None
                    self.login()
                    continue
                raise IstentoreAPIError(f"HTTP error posting measurements: {exc}") from exc
            except Exception as exc:
                raise IstentoreAPIError(f"Unexpected error posting measurements: {exc}") from exc

        raise IstentoreAPIError("Failed to post measurement after authentication retry.")

    # LIB measurement series
    def post_lib_SOC_kWh(self, value: float, timestamp=None) -> dict:
        return self.post_measurement(4, value, timestamp=timestamp)

    def post_lib_P_W(self, value: float, timestamp=None) -> dict:
        return self.post_measurement(6, value, timestamp=timestamp)

    def post_lib_Q_VAr(self, value: float, timestamp=None) -> dict:
        return self.post_measurement(7, value, timestamp=timestamp)

    def post_lib_V_V(self, value: float, timestamp=None) -> dict:
        return self.post_measurement(8, value, timestamp=timestamp)

    # VRFB measurement series
    def post_vrfb_SOC_kWh(self, value: float, timestamp=None) -> dict:
        return self.post_measurement(5, value, timestamp=timestamp)

    def post_vrfb_P_W(self, value: float, timestamp=None) -> dict:
        return self.post_measurement(11, value, timestamp=timestamp)

    def post_vrfb_Q_VAr(self, value: float, timestamp=None) -> dict:
        return self.post_measurement(10, value, timestamp=timestamp)

    def post_vrfb_V_V(self, value: float, timestamp=None) -> dict:
        return self.post_measurement(9, value, timestamp=timestamp)

    def schedule_to_dataframe(self, schedule: dict, default_q_kvar: float = 0.0):
        import pandas as pd

        if not schedule:
            return pd.DataFrame(
                columns=["datetime", "power_setpoint_kw", "reactive_power_setpoint_kvar"]
            ).set_index("datetime")

        data = []
        for dt_str, power_kw in schedule.items():
            if "+" in dt_str or "Z" in dt_str:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)

            data.append(
                {
                    "datetime": dt.astimezone(self.timezone),
                    "power_setpoint_kw": power_kw,
                    "reactive_power_setpoint_kvar": default_q_kvar,
                }
            )

        df = pd.DataFrame(data).set_index("datetime").sort_index()
        return df


if __name__ == "__main__":
    api = IstentoreAPI()
    today_start = datetime.now(api.timezone).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1) - timedelta(minutes=15)
    schedules = api.get_day_ahead_schedules(today_start, today_end)
    print("LIB points:", len(schedules["lib"]))
    print("VRFB points:", len(schedules["vrfb"]))
