"""
Logger configuration module for HIL Scheduler.

Configures logging to:
1. Output to console (existing behavior)
2. Write to daily rotating log files in logs/ folder
3. Capture session logs to shared_data for dashboard display
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime


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
    logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
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
    
    # 2. File Handler - Daily rotation at midnight
    log_file_path = os.path.join(logs_dir, 'hil_scheduler.log')
    file_handler = TimedRotatingFileHandler(
        log_file_path,
        when='midnight',
        interval=1,
        backupCount=30,  # Keep 30 days of logs
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    # Customize the suffix to include date
    file_handler.suffix = "%Y-%m-%d.log"
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
