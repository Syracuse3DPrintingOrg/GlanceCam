"""Pytest fixtures. Pure logic only: no network, no Docker.

The service directory is put on sys.path so ``import app.*`` works the same way
the app runs, and a per-test tmp data_dir is pointed at so the config and camera
store write into an isolated directory that pytest cleans up.
"""
import sys
from pathlib import Path

import pytest

_SERVICE_DIR = Path(__file__).resolve().parent.parent / "service"
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the live settings at an isolated data dir and reset the stores.

    Yields the Path. Any settings.save() / camera store write in the test lands
    under tmp_path.
    """
    from app.config import settings
    from app.services import cameras as camera_store
    from app.services import credentials as cred_store
    from app.services import layouts as layout_store

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    # The stores cache a StateFile bound to a data_dir path; clear them so they
    # rebind to the tmp dir.
    monkeypatch.setattr(camera_store, "_store", None)
    monkeypatch.setattr(camera_store, "_store_path", None)
    monkeypatch.setattr(cred_store, "_store", None)
    monkeypatch.setattr(cred_store, "_store_path", None)
    monkeypatch.setattr(layout_store, "_store", None)
    monkeypatch.setattr(layout_store, "_store_path", None)
    return tmp_path
