"""Repo-root-relative path helpers for runtime modules."""

import os


def _as_directory(path_like=None):
    if path_like is None:
        return os.path.dirname(os.path.abspath(__file__))
    path = os.path.abspath(str(path_like))
    if os.path.isfile(path):
        return os.path.dirname(path)
    return path


def _looks_like_project_root(path):
    if not os.path.isdir(path):
        return False
    has_assets = os.path.isdir(os.path.join(path, "assets"))
    has_marker = (
        os.path.isfile(os.path.join(path, "hil_scheduler.py"))
        or os.path.isfile(os.path.join(path, "config.yaml"))
        or os.path.isdir(os.path.join(path, "memory-bank"))
        or os.path.isdir(os.path.join(path, ".git"))
    )
    return bool(has_assets and has_marker)


def get_project_root(anchor_path=None):
    """
    Return the repository root.

    `anchor_path` may be a file path or directory path. The resolver walks upward
    looking for a directory that matches this repo's structure.
    """
    candidate = _as_directory(anchor_path)
    while True:
        if _looks_like_project_root(candidate):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return os.path.dirname(_as_directory(__file__))


def get_assets_dir(anchor_path=None):
    return os.path.join(get_project_root(anchor_path), "assets")


def get_logs_dir(anchor_path=None):
    return os.path.join(get_project_root(anchor_path), "logs")


def get_data_dir(anchor_path=None):
    return os.path.join(get_project_root(anchor_path), "data")
