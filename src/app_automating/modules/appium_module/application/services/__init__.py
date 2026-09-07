"""The Appium module's services: one class per product surface.

A service depends on the domain's ports and never on a concrete adapter, which
is what ``application/tests/unit/`` asserts and what lets the whole layer be
tested with mocks (Rule 2 §3).
"""

from .appium_device_service import AppiumDeviceService

__all__ = ["AppiumDeviceService"]
