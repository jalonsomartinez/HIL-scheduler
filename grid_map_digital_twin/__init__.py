"""Vendored digital twin assets used by the grid-map runtime.

This package intentionally avoids importing the simulator at import time,
because the simulator depends on pandapower and some test environments do not
have that dependency installed.
"""
