"""Application services: what the product does, one method per operation.

A service is the layer's only place for orchestration. It depends on the domain
ports and never on a concrete adapter -- the wiring in ``..di`` is what decides
which adapter satisfies them.
"""

from .android_emulator_service import AndroidEmulatorService

__all__ = ["AndroidEmulatorService"]
