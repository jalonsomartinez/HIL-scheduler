# Project Plan: HIL Scheduler Improvements

## Overview
This plan outlines potential improvements to the HIL Scheduler project, organized by priority and category. Each improvement includes a description, rationale, and estimated effort level.

---

## Phase 1: Foundation & Stability (High Priority)

### 1.1 Configuration Management
**Description**: Replace hardcoded config.py with external configuration files (JSON/YAML) and command-line argument support.

**Current State**: Configuration is hardcoded in [`config.py`](config.py) with two modes (local/remote).

**Proposed Changes**:
- Add JSON/YAML config file support
- Implement CLI argument parsing using `argparse`
- Support environment variables for sensitive data (IP addresses, ports)
- Config validation on startup

**Benefits**:
- No code changes needed for configuration adjustments
- Easier deployment across different environments
- Better separation of code and configuration

**Estimated Effort**: Medium

### 1.2 Error Handling & Resilience
**Description**: Improve error handling across all agents for better resilience.

**Current Issues**:
- Some agents may crash on Modbus connection failures
- No retry logic with exponential backoff
- Limited validation of configuration values

**Proposed Changes**:
- Implement retry decorators for Modbus operations
- Add exponential backoff for connection failures
- Validate all config values at startup with clear error messages
- Add circuit breaker pattern for external connections

**Estimated Effort**: Medium

### 1.3 Logging Enhancement
**Description**: Improve logging structure and add structured logging support.

**Proposed Changes**:
- Add JSON formatted logging option
- Include correlation IDs for tracking requests across agents
- Separate log files per agent
- Add log rotation to prevent disk space issues
- Configurable log levels per agent

**Estimated Effort**: Low

---

## Phase 2: Testing & Quality (High Priority)

### 2.1 Unit Tests
**Description**: Add comprehensive unit tests for all agents and utilities.

**Current State**: No tests present.

**Proposed Coverage**:
- `test_utils.py`: Unit conversion functions
- `test_data_fetcher.py`: Schedule generation and interpolation
- `test_battery_agent.py`: SoC calculations and power limiting
- `test_config.py`: Configuration loading and validation

**Tools**: pytest, pytest-asyncio for async tests, pytest-cov for coverage

**Estimated Effort**: High

### 2.2 Integration Tests
**Description**: Add integration tests for full workflow scenarios.

**Test Scenarios**:
- Complete schedule execution cycle
- Battery SoC boundary handling
- Modbus communication between agents
- Dashboard start/stop controls
- Graceful shutdown sequence

**Tools**: pytest, Docker for isolated test environment

**Estimated Effort**: High

### 2.3 Code Quality Tools
**Description**: Add linting, formatting, and type checking.

**Tools**:
- `black`: Code formatting
- `flake8` or `ruff`: Linting
- `mypy`: Type checking
- `pre-commit`: Git hooks for quality checks

**Estimated Effort**: Low

---

## Phase 3: Core Features (Medium Priority)

### 3.1 Schedule Management Improvements
**Description**: Enhance schedule generation and management capabilities.

**Proposed Features**:
- **Custom Schedule Import**: Support importing schedules from various formats (CSV, Excel, JSON)
- **Schedule Templates**: Pre-defined schedule patterns (peak shaving, arbitrage, etc.)
- **Schedule Preview**: Visualize schedule before execution
- **Schedule Validation**: Check for impossible setpoints or SoC violations
- **Multi-day Schedules**: Support schedules spanning multiple days

**Estimated Effort**: High

### 3.2 Data Persistence & History
**Description**: Improve data storage and add historical analysis.

**Proposed Features**:
- **Database Support**: SQLite or PostgreSQL for measurements
- **Data Compression**: Compress old CSV files
- **Historical Dashboard**: View past executions
- **Performance Metrics**: Calculate and store efficiency metrics
- **Export Tools**: Export data in various formats (CSV, Excel, Parquet)

**Estimated Effort**: High

### 3.3 Enhanced Dashboard
**Description**: Add more features to the web dashboard.

**Proposed Features**:
- **Real-time Statistics**: Current SoC, power, and efficiency
- **Alarm Display**: Show active warnings and alarms
- **Schedule Upload UI**: Upload custom schedules through web interface
- **Mobile Responsiveness**: Better mobile experience
- **Dark Mode**: UI theme toggle
- **Download Buttons**: Download measurements.csv directly from UI

**Estimated Effort**: Medium

---

## Phase 4: Advanced Features (Lower Priority)

### 4.1 API Interface
**Description**: Add REST API for external integration.

**Proposed Endpoints**:
- `GET /status`: Current system status
- `POST /schedule`: Upload new schedule
- `GET /measurements`: Retrieve measurement data
- `POST /control/start`: Start the scheduler
- `POST /control/stop`: Stop the scheduler
- `GET /config`: View current configuration

**Tools**: Flask-RESTful or FastAPI

**Estimated Effort**: High

### 4.2 Docker Support
**Description**: Containerize the application for easy deployment.

**Deliverables**:
- `Dockerfile` for the main application
- `docker-compose.yml` for local development
- Separate containers for local vs remote modes
- Documentation for Docker deployment

**Estimated Effort**: Medium

### 4.3 Simulation Mode
**Description**: Enhanced simulation capabilities for testing without hardware.

**Proposed Features**:
- **Scenario Replay**: Replay historical measurement data
- **Fault Injection**: Simulate communication failures
- **Speed Control**: Run simulation faster than real-time
- **Virtual Battery Models**: Different battery chemistry models

**Estimated Effort**: High

### 4.4 Alerting System
**Description**: Add notification capabilities for important events.

**Proposed Features**:
- Email notifications for errors
- Webhook support for integration with monitoring systems
- Configurable alert thresholds (SoC limits, power deviations)
- Alert history log

**Estimated Effort**: Medium

---

## Recommended Implementation Order

1. **Configuration Management** - Foundation for other improvements
2. **Code Quality Tools** - Establish standards early
3. **Unit Tests** - Critical for confident refactoring
4. **Error Handling** - Improve stability
5. **Enhanced Dashboard** - Immediate user value
6. **Data Persistence** - Foundation for historical features
7. **Integration Tests** - Ensure system reliability
8. **API Interface** - Enable external integrations
9. **Docker Support** - Simplify deployment
10. **Advanced Features** - Based on user feedback

---

## Quick Wins (Low Effort, High Value)

These improvements can be implemented quickly for immediate benefit:

1. **Add requirements-dev.txt**: Separate dev dependencies (pytest, black, flake8)
2. **Improve README.md**: Add quick start guide and architecture diagram
3. **Add .gitignore**: Ignore venv/, __pycache__/, *.pyc, measurements.csv, schedule_source.csv
4. **Config Validation**: Add basic validation to config.py
5. **Log Rotation**: Add RotatingFileHandler to logging setup

---

## Technical Debt to Address

1. **Hardcoded paths**: Remove hardcoded file paths, use configurable paths
2. **Magic numbers**: Replace hardcoded register addresses with constants
3. **Thread safety**: Review all shared data access patterns
4. **Exception handling**: Ensure all exceptions are caught and logged appropriately
5. **Resource cleanup**: Verify all Modbus connections are properly closed
