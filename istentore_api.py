"""
Istentore API Wrapper for HIL Scheduler.

This module provides a wrapper around the Istentore API for fetching day-ahead
schedules. It supports session-based password handling where the password is
provided by the dashboard when needed.
"""

import logging
from datetime import datetime, timezone, timedelta

import requests
from time_utils import DEFAULT_TIMEZONE_NAME, get_timezone

# Default API configuration (email is fixed, password is provided at runtime)
DEFAULT_BASE_URL = "https://3mku48kfxf.execute-api.eu-south-2.amazonaws.com/default"
DEFAULT_EMAIL = "i-STENTORE"


class IstentoreAPIError(Exception):
    """Base exception for Istentore API errors."""
    pass


class AuthenticationError(IstentoreAPIError):
    """Raised when authentication fails."""
    pass


class IstentoreAPI:
    """
    Wrapper class for the Istentore API.
    
    This class provides methods to fetch day-ahead schedules from the Istentore API.
    The password is provided at runtime by the dashboard and stored for the session.
    """
    
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        email: str = DEFAULT_EMAIL,
        timezone_name: str = DEFAULT_TIMEZONE_NAME,
    ):
        """
        Initialize the Istentore API wrapper.
        
        Args:
            base_url: The base URL for the API endpoints
            email: The email for authentication
        """
        self.base_url = base_url
        self.email = email
        self.timezone = get_timezone(timezone_name)
        self._password = None
        self._token = None
    
    def set_password(self, password: str):
        """
        Set the password for the current session.
        
        Args:
            password: The API password
        """
        self._password = password
        self._token = None  # Reset token when password changes
    
    def is_authenticated(self) -> bool:
        """Check if the current session has a valid authentication token."""
        return self._token is not None
    
    def login(self) -> str:
        """
        Authenticate with the API and obtain a token.
        
        Returns:
            The authentication token
            
        Raises:
            AuthenticationError: If authentication fails
        """
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
        except requests.exceptions.HTTPError as e:
            logging.error(f"Istentore API: Authentication failed - {e}")
            raise AuthenticationError(f"Authentication failed: {e}")
        except Exception as e:
            logging.error(f"Istentore API: Unexpected error during authentication - {e}")
            raise IstentoreAPIError(f"Unexpected error: {e}")
    
    def _get_market_products(self, market_id: int, delivery_period_gte: str = None, 
                             delivery_period_lte: str = None) -> list:
        """
        Internal method to call the market_products endpoint.
        
        Args:
            market_id: The market ID (4 for day-ahead, 3 for MFRR)
            delivery_period_gte: Optional start time filter (ISO 8601 UTC)
            delivery_period_lte: Optional end time filter (ISO 8601 UTC)
            
        Returns:
            List of market product data
            
        Raises:
            AuthenticationError: If not authenticated
            IstentoreAPIError: If the request fails
        """
        if not self.is_authenticated():
            self.login()
        
        url = f"{self.base_url}/market_products"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json"
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
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                # Token expired, try to re-authenticate
                logging.warning("Istentore API: Token expired, re-authenticating...")
                self._token = None
                self.login()
                # Retry the request
                return self._get_market_products(market_id, delivery_period_gte, delivery_period_lte)
            logging.error(f"Istentore API: HTTP error - {e}")
            raise IstentoreAPIError(f"HTTP error: {e}")
        except Exception as e:
            logging.error(f"Istentore API: Unexpected error - {e}")
            raise IstentoreAPIError(f"Unexpected error: {e}")
    
    def get_day_ahead_schedule(self, start_time: datetime, end_time: datetime) -> dict:
        """
        Fetch day-ahead schedule for the specified time range.
        
        This method fetches the day-ahead market schedule (market_id=4) for the
        specified time range. The schedule includes active power setpoints for
        the LIB (Lithium Battery) system.
        
        Args:
            start_time: Start time (datetime in any timezone, will be converted to UTC)
            end_time: End time (datetime in any timezone, will be converted to UTC)
            
        Returns:
            Dictionary with datetime keys and power setpoint values in kW.
            Format: {iso8601_datetime: power_kw, ...}
            
        Raises:
            AuthenticationError: If authentication fails
            IstentoreAPIError: If the request fails
        """
        # Convert to UTC if timezone-aware, assume configured timezone if naive
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=self.timezone)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=self.timezone)
        
        start_utc = start_time.astimezone(timezone.utc)
        end_utc = end_time.astimezone(timezone.utc)
        
        # Format as ISO 8601 UTC
        delivery_period_gte = start_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        delivery_period_lte = end_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        
        data_list = self._get_market_products(
            market_id=4,  # Day-ahead market
            delivery_period_gte=delivery_period_gte,
            delivery_period_lte=delivery_period_lte
        )
        
        schedule = {}
        
        if not data_list:
            logging.info("Istentore API: No day-ahead schedule data available for the specified range")
            return schedule
        
        data = data_list[0]
        
        for period in data.get("delivery_periods", []):
            delivery_period_str = period.get("delivery_period")
            
            # Parse the delivery period (UTC string)
            delivery_dt_utc = datetime.strptime(
                delivery_period_str.replace("+00:00", ""), 
                "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            
            activation = period.get("activation", [{}])[0]
            
            # Calculate net power: lib_to_vpp_kw - vpp_to_lib_kw
            # Positive = discharge (towards VPP), negative = charge (from VPP)
            net_power_kw = (
                activation.get("lib_to_vpp_kw", 0.0) - 
                activation.get("vpp_to_lib_kw", 0.0)
            )
            
            # Use ISO 8601 as the key
            schedule[delivery_dt_utc.isoformat()] = net_power_kw
        
        logging.info(f"Istentore API: Fetched {len(schedule)} day-ahead setpoints")
        return schedule
    
    def get_mfrr_next_activation(self) -> dict:
        """
        Fetch the next MFRR (Manual Frequency Restoration Reserve) activation.
        
        Returns:
            Dictionary with datetime key and power setpoint value in kW.
            
        Raises:
            AuthenticationError: If authentication fails
            IstentoreAPIError: If the request fails
        """
        now_utc = datetime.now(self.timezone).astimezone(timezone.utc)
        now_iso = now_utc.isoformat()
        
        data_list = self._get_market_products(
            market_id=3,  # MFRR market
            delivery_period_gte=now_iso
        )
        
        schedule = {}
        
        if not data_list:
            logging.info("Istentore API: No MFRR activation available")
            return schedule
        
        market_data = data_list[0]
        delivery_periods = market_data.get("delivery_periods", [])
        
        if not delivery_periods:
            return schedule
        
        first_period = delivery_periods[0]
        delivery_period_api = first_period.get("delivery_period")
        
        # Parse the delivery period
        delivery_dt_utc = datetime.strptime(
            delivery_period_api.replace("+00:00", ""), 
            "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        
        activations = first_period.get("activation", [])
        if not activations:
            return schedule
        
        activation = activations[0]
        net_power_kw = (
            activation.get("total_upward_kw", 0.0) - 
            activation.get("total_downward_kw", 0.0)
        )
        
        schedule[delivery_dt_utc.isoformat()] = net_power_kw
        logging.info(f"Istentore API: Fetched MFRR activation for {delivery_dt_utc.isoformat()}")
        
        return schedule
    
    def schedule_to_dataframe(self, schedule: dict, default_q_kvar: float = 0.0):
        """
        Convert a schedule dictionary to a pandas DataFrame.
        
        Args:
            schedule: Dictionary with ISO datetime keys and power values
            default_q_kvar: Default reactive power value (kvar)
            
        Returns:
            DataFrame with datetime index and power_setpoint_kw, 
            reactive_power_setpoint_kvar columns
        """
        import pandas as pd
        
        if not schedule:
            return pd.DataFrame(columns=[
                'datetime', 
                'power_setpoint_kw', 
                'reactive_power_setpoint_kvar'
            ]).set_index('datetime')
        
        # Parse datetime strings to actual datetime objects
        data = []
        for dt_str, power_kw in schedule.items():
            # Parse ISO format datetime (may or may not have timezone)
            if '+' in dt_str or 'Z' in dt_str:
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(dt_str)
                dt = dt.replace(tzinfo=timezone.utc)
            
            localized_dt = dt.astimezone(self.timezone)
            data.append({
                'datetime': localized_dt,
                'power_setpoint_kw': power_kw,
                'reactive_power_setpoint_kvar': default_q_kvar
            })
        
        df = pd.DataFrame(data)
        df = df.set_index('datetime')
        df = df.sort_index()
        
        return df


if __name__ == "__main__":
    # Example usage for testing
    import pandas as pd
    
    # Create API instance (password will be set interactively in real usage)
    api = IstentoreAPI()
    
    # For testing, you can set the password directly
    # api.set_password("your_password_here")
    
    # Example: Fetch today's schedule
    today_start = datetime.now(api.timezone).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1) - timedelta(minutes=15)
    
    try:
        schedule = api.get_day_ahead_schedule(today_start, today_end)
        print(f"Fetched {len(schedule)} setpoints:")
        for dt, power in list(schedule.items())[:5]:
            print(f"  {dt}: {power:.2f} kW")
        
        # Convert to DataFrame
        df = api.schedule_to_dataframe(schedule)
        print(f"\nDataFrame shape: {df.shape}")
        print(df.head())
        
    except AuthenticationError:
        print("Authentication failed. Please set the password first.")
    except IstentoreAPIError as e:
        print(f"API error: {e}")
