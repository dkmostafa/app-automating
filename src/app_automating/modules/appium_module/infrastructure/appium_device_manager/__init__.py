"""The Appium adapter, as one package with one public surface.

Rule 1 §1: callers import from here, never from ``.manager`` or ``.models``.
The split inside is by *kind of thing*, in a fixed acyclic order:

    models -> errors -> parsing  \\
    config -> process -> server   >-- manager
                        sessions /

* ``models.py``   -- value objects that never cross a port. Imports nothing here.
* ``errors.py``   -- every typed failure; each is a domain error too.
* ``parsing.py``  -- pure: caller input and tool output become models or errors.
* ``config.py``   -- the injected settings, and the only reader of the environment.
* ``process.py``  -- tool resolution and the child-process lifecycle.
* ``server.py``   -- the Appium server process: probe, launch, own, kill.
* ``sessions.py`` -- live sessions, and the thread boundary in front of the
  synchronous Appium client.
* ``manager.py``  -- the class, and nothing else.

The names shared across these files are public within the package
(``run_command``, ``resolve_locator``, ``CommandRunner``) rather than
underscore-prefixed; a leading underscore here means "private to this one file".
"""

from .config import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT, ENV_PREFIX, AppiumConfig
from .errors import (
    AppFileNotFoundError,
    AppiumComponentError,
    AppiumToolNotFoundError,
    CommandFailedError,
    CommandTimeoutError,
    DriverInstallCommandError,
    DriverMissingError,
    ElementLookupError,
    InvalidCoordinatesError,
    InvalidDeviceIdError,
    InvalidKeyNameError,
    InvalidLocatorError,
    InvalidScrollError,
    ScreenshotCaptureError,
    ServerLaunchError,
    ServerNotAnsweringError,
    ServerNotReadyError,
    SessionCreateError,
    SessionDeadError,
    UnknownSessionError,
    WebDriverCallError,
)
from .manager import AppiumDeviceManager
from .models import CommandResult, ServerStatus, display_command
from .parsing import (
    ANDROID_KEYCODES,
    LOCATOR_BY,
    classify_interaction_failure,
    classify_session_failure,
    parse_installed_drivers,
    parse_server_status,
    parse_version_output,
    png_dimensions,
    resolve_locator,
    truncate,
    validate_app_path,
    validate_coordinates,
    validate_device_id,
    validate_key,
    validate_locator,
    validate_scroll,
)
from .process import CommandRunner, ToolResolver
from .server import AppiumServer
from .sessions import SessionRegistry

__all__ = [
    # the class
    "AppiumDeviceManager",
    # configuration
    "AppiumConfig",
    "ENV_PREFIX",
    "DEFAULT_SERVER_HOST",
    "DEFAULT_SERVER_PORT",
    # collaborators the composition root assembles
    "CommandRunner",
    "ToolResolver",
    "AppiumServer",
    "SessionRegistry",
    # value objects
    "CommandResult",
    "ServerStatus",
    "display_command",
    # pure helpers
    "ANDROID_KEYCODES",
    "LOCATOR_BY",
    "resolve_locator",
    "validate_device_id",
    "validate_locator",
    "validate_coordinates",
    "validate_key",
    "validate_scroll",
    "validate_app_path",
    "parse_version_output",
    "parse_installed_drivers",
    "parse_server_status",
    "png_dimensions",
    "truncate",
    "classify_session_failure",
    "classify_interaction_failure",
    # errors
    "AppiumComponentError",
    "AppiumToolNotFoundError",
    "CommandFailedError",
    "CommandTimeoutError",
    "DriverMissingError",
    "DriverInstallCommandError",
    "ServerLaunchError",
    "ServerNotReadyError",
    "ServerNotAnsweringError",
    "InvalidDeviceIdError",
    "InvalidLocatorError",
    "InvalidCoordinatesError",
    "InvalidKeyNameError",
    "InvalidScrollError",
    "AppFileNotFoundError",
    "SessionCreateError",
    "SessionDeadError",
    "UnknownSessionError",
    "ElementLookupError",
    "WebDriverCallError",
    "ScreenshotCaptureError",
]
