"""Compatibility entrypoint for hosts that look for main:app."""

from app.main import app

__all__ = ["app"]
