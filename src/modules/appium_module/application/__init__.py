"""The Appium module's application layer: services and their wiring.

Two things live here and nothing else:

* ``services/`` -- one class per product surface, holding the orchestration.
  A service depends on the domain's ports and never on a concrete adapter.
* ``di.py`` -- the composition root (Rule 0 §4). The only file in the module
  that names an adapter, and the only one that reads the environment.

It is the one layer permitted to import both ``..domain`` and
``..infrastructure``, because binding one to the other is precisely its job.
"""

from .di import (
    appium_device_ports,
    appium_device_service,
    build_appium_device_manager,
    build_appium_device_service,
)
from .services import AppiumDeviceService

__all__ = [
    # services
    "AppiumDeviceService",
    # wiring
    "build_appium_device_manager",
    "appium_device_ports",
    "build_appium_device_service",
    "appium_device_service",
]
