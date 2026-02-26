"""
Logger configuration module for HIL Scheduler.

Configures logging to:
1. Output to console (existing behavior)
2. Write to daily rotating log files in logs/ folder
3. Capture session logs to shared_data for dashboard display
"""

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from runtime.paths import get_logs_dir


class SessionLogHandler(logging.Handler):
    """
    Custom logging handler that captures log records to shared_data.
    This allows the dashboard to display logs in real-time.
    """
    
    def __init__(self, shared_data):
        super().__init__()
        self.shared_data = shared_data
        
    def emit(self, record):
        """Emit a log record to the session logs in shared_data."""
        try:
            log_entry = {
                'timestamp': datetime.fromtimestamp(record.created).strftime('%H:%M:%S'),
                'level': record.levelname,
                'message': self.format(record).split(' - ', 2)[-1]  # Remove timestamp and level from message
            }
            
            with self.shared_data['log_lock']:
                self.shared_data['session_logs'].append(log_entry)
                # Keep only last 1000 entries to prevent memory bloat
                if len(self.shared_data['session_logs']) > 1000:
                    self.shared_data['session_logs'] = self.shared_data['session_logs'][-1000:]
        except Exception:
            self.handleError(record)


class DateRoutedFileHandler(logging.Handler):
    """File handler that routes records to YYYY-MM-DD files by record timestamp."""

    def __init__(self, logs_dir, timezone_name, shared_data, *, encoding="utf-8"):
        super().__init__()
        self.logs_dir = logs_dir
        self.shared_data = shared_data
        self.encoding = encoding
        self.terminator = "\n"
        try:
            self.timezone = ZoneInfo(timezone_name)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            self.timezone = datetime.now().astimezone().tzinfo

        self._current_date = None
        self._current_path = None
        self._stream = None

    def _build_log_path(self, date_str):
        return os.path.join(self.logs_dir, f"{date_str}_hil_scheduler.log")

    def _update_shared_log_path(self, path):
        lock = self.shared_data.get("log_lock")
        if lock is None:
            self.shared_data["log_file_path"] = path
            return
        with lock:
            self.shared_data["log_file_path"] = path

    def _open_for_date(self, date_str):
        if date_str == self._current_date and self._stream is not None:
            return

        self._close_stream()
        path = self._build_log_path(date_str)
        self._stream = open(path, "a", encoding=self.encoding)
        self._current_date = date_str
        self._current_path = path
        self._update_shared_log_path(path)

    def _close_stream(self):
        if self._stream is None:
            return
        try:
            self._stream.close()
        finally:
            self._stream = None

    def emit(self, record):
        try:
            record_dt = datetime.fromtimestamp(record.created, tz=self.timezone)
            date_str = record_dt.strftime("%Y-%m-%d")
            self._open_for_date(date_str)
            self._stream.write(self.format(record) + self.terminator)
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        try:
            self._close_stream()
        finally:
            super().close()


def setup_logging(config, shared_data):
    """
    Set up logging with console, file, and session handlers.
    
    Args:
        config: Configuration dictionary with LOG_LEVEL
        shared_data: Shared data dictionary to store session logs
        
    Returns:
        logging.Logger: The configured root logger
    """
    log_level = config.get("LOG_LEVEL", logging.INFO)
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # Create logs directory if it doesn't exist
    logs_dir = get_logs_dir(__file__)
    os.makedirs(logs_dir, exist_ok=True)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create formatter
    formatter = logging.Formatter(log_format, datefmt=date_format)
    
    # 1. Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 2. File Handler - Routes each record by its configured-timezone date
    file_handler = DateRoutedFileHandler(
        logs_dir,
        config.get("TIMEZONE_NAME"),
        shared_data,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # 3. Session Handler - Captures to shared_data for dashboard
    session_handler = SessionLogHandler(shared_data)
    session_handler.setLevel(log_level)
    session_handler.setFormatter(formatter)
    root_logger.addHandler(session_handler)
    
    # Suppress Werkzeug logs
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.ERROR)
    
    return root_logger
