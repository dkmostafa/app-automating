"""The Appium module's infrastructure: the adapters that touch the outside world.

One package per component (Rule 1 §1), each named after the class it exposes and
split by kind inside. Today there is one: ``appium_device_manager``, which owns
the Appium CLI, the Appium server process, and every live automation session.

This layer imports ``..domain`` in order to implement it, and imports nothing
from ``..application`` or ``..presentation``. That is the arrow that makes the
whole scheme work: at runtime a service calls an adapter, but at compile time
the adapter depends on the domain's abstraction and never the reverse.
"""
