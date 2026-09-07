"""The adapter that lets this module record what ``appium_module`` drives.

One component, and the only place in the product where two modules meet.
``appium_module`` exposes a generic "wrap my ports before you build the
service" hook (``PortDecorator`` in its own ``application/di.py``) and knows
nothing about who might use it or why; this package is what fills it in for
this product, and the composition root is the only thing that connects the two.

The direction is deliberate. This adapter imports ``appium_module.domain`` --
the innermost, framework-free ring of the other module -- and nothing else from
it: not its services, not its infrastructure, not its tools. Appium imports
nothing from here at all. So the two modules are coupled through appium's own
Protocols and DTOs, unmodified, and either could be deleted without the other's
inner layers noticing.

It lives on this side because this is the module offering the capability. Put
the other way round, ``appium_module`` would have to know that navigation
memory exists, which is exactly what keeping the hook generic prevents.
"""

from .driver import RecordingDeviceDriver

__all__ = ["RecordingDeviceDriver"]
