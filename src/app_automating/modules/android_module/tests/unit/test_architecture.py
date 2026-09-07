"""Rule 0, asserted rather than documented.

Every check here reads source or imported objects. Nothing spawns a process,
touches a device or needs an Android SDK, which is itself part of the point: if
this file cannot run on a bare machine, the domain is not as pure as it claims.
"""

from __future__ import annotations

import ast
import copy
import inspect
import pickle
import pkgutil
from pathlib import Path

import pytest

from app_automating.modules.android_module.application import build_android_emulator_manager
from app_automating.modules.android_module.domain import ports as ports_module
from app_automating.modules.android_module.domain.ports import (
    DeviceCatalog,
    EmulatorLifecycle,
    SystemImageInstaller,
)
from app_automating.modules.android_module.infrastructure.android_emulator_manager import (
    AndroidEmulatorManager,
    AndroidSdkConfig,
)

pytestmark = pytest.mark.unit

MODULE_ROOT = Path(__file__).resolve().parents[2]
MODULE_PACKAGE = "app_automating.modules.android_module"

#: Rule 0 §1: the four layers every module has. A module missing one, or
#: inventing a fifth, fails `test_every_module_has_exactly_the_four_layers`.
LAYERS = ("domain", "infrastructure", "application", "presentation")

#: What each layer may not import.
#:
#: `application` is deliberately absent from this table: wiring is the one job
#: that requires naming a concrete adapter, so it may import every other layer.
#: `presentation` may reach domain and application but never infrastructure -- a
#: tool that touches an adapter directly has bypassed the composition root.
FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "domain": ("infrastructure", "application", "presentation"),
    "infrastructure": ("application", "presentation"),
    "presentation": ("infrastructure",),
}

#: Frameworks belong at the edges. None of these may appear under domain/,
#: whatever the import spelling.
FRAMEWORK_PACKAGES = frozenset(
    {"fastmcp", "mcp", "appium", "selenium", "sqlalchemy", "aiosqlite", "pydantic"}
)

#: I/O the domain must not reach for directly, even from the standard library.
DOMAIN_FORBIDDEN_STDLIB = frozenset({"subprocess", "socket", "shutil", "os", "asyncio"})


def _source_files(ring: str) -> list[Path]:
    directory = MODULE_ROOT / ring
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*.py") if "tests" not in p.parts)


def _imported_roots(path: Path) -> set[str]:
    """Every top-level name this file imports, plus the ring a relative import
    escapes into."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    roots.add(node.module.split(".")[0])
                continue
            # A relative import: resolve it against this file's package so
            # `from ...domain.errors import X` is seen as reaching "domain".
            package_parts = path.relative_to(MODULE_ROOT).parent.parts
            base = package_parts[: len(package_parts) - (node.level - 1)]
            target = (*base, *(node.module.split(".") if node.module else ()))
            if target:
                roots.add(target[0])
    return roots


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_IMPORTS))
def test_layers_only_import_what_they_are_allowed_to(layer: str) -> None:
    """Rule 0 §1: the dependency rule, layer by layer.

    ``application`` has no row in the table on purpose -- it is the wiring layer
    and may name anything. Every other layer is fenced.
    """
    forbidden = FORBIDDEN_IMPORTS[layer]
    offenders: list[str] = []
    for path in _source_files(layer):
        for imported in sorted(_imported_roots(path) & set(forbidden)):
            offenders.append(f"{path.relative_to(MODULE_ROOT)} imports {imported}")
    assert not offenders, f"{layer}/ may not import {forbidden}:\n" + "\n".join(offenders)


def test_every_module_has_exactly_the_four_layers() -> None:
    """Rule 0 §1: the same four layers in every module -- not three, not five."""
    present = {
        child.name
        for child in MODULE_ROOT.iterdir()
        if child.is_dir() and (child / "__init__.py").exists() and child.name != "tests"
    }
    assert present == set(LAYERS), f"expected exactly {sorted(LAYERS)}, found {sorted(present)}"


def test_the_domain_is_framework_free() -> None:
    """Rule 0 §1: frameworks stay at the edges, and the domain is the centre."""
    offenders: list[str] = []
    for path in _source_files("domain"):
        for imported in sorted(_imported_roots(path) & FRAMEWORK_PACKAGES):
            offenders.append(f"{path.relative_to(MODULE_ROOT)} imports {imported}")
    assert not offenders, "frameworks belong in infrastructure/ or presentation/:\n" + "\n".join(
        offenders
    )


def test_domain_does_no_io() -> None:
    """Rule 0 §1: a domain file must be importable on a host with no SDK."""
    offenders: list[str] = []
    for path in _source_files("domain"):
        for imported in sorted(_imported_roots(path) & DOMAIN_FORBIDDEN_STDLIB):
            offenders.append(f"{path.relative_to(MODULE_ROOT)} imports {imported}")
    assert not offenders, "the domain performs no I/O:\n" + "\n".join(offenders)


def test_only_the_composition_root_reads_the_environment() -> None:
    """Rule 0 §4: ambient state is resolved in exactly one place."""
    offenders: list[str] = []
    for ring in ("domain", "infrastructure", "presentation"):
        for path in _source_files(ring):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or node.attr != "environ":
                    continue
                # config.py is the component's declared reader (Rule 1 §4); it
                # is called by the composition root and by nothing below it.
                if path.name == "config.py":
                    continue
                offenders.append(f"{path.relative_to(MODULE_ROOT)}:{node.lineno} reads os.environ")
    assert not offenders, (
        "only config.py and the composition root read the environment:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "port", [DeviceCatalog, EmulatorLifecycle, SystemImageInstaller], ids=lambda p: p.__name__
)
def test_the_adapter_satisfies_every_port_it_is_injected_as(port: type) -> None:
    """Rule 0 §2 and §5: the concrete adapter still matches the abstraction.

    ``isinstance`` against a runtime-checkable Protocol only proves the methods
    are present, so the signatures are compared too -- a port whose request type
    drifted from the adapter's would otherwise pass silently.
    """
    manager = AndroidEmulatorManager.__new__(AndroidEmulatorManager)
    assert isinstance(manager, port)

    for name, declared in inspect.getmembers(port, inspect.isfunction):
        if name.startswith("_"):
            continue
        implemented = getattr(AndroidEmulatorManager, name)
        assert inspect.iscoroutinefunction(implemented), f"{name} must be async"
        assert inspect.signature(implemented) == inspect.signature(declared), (
            f"{AndroidEmulatorManager.__name__}.{name} has drifted from {port.__name__}.{name}"
        )


def test_the_composition_root_returns_something_that_satisfies_the_ports() -> None:
    """Rule 0 §4: what the composition root builds is what the ports promise."""
    manager = build_android_emulator_manager(AndroidSdkConfig(sdk_root=None))
    assert isinstance(manager, DeviceCatalog)
    assert isinstance(manager, EmulatorLifecycle)
    assert isinstance(manager, SystemImageInstaller)


def test_ports_cover_the_managers_whole_public_surface() -> None:
    """A new operation must land on a port, not just on the adapter.

    Without this, an adapter grows a method the domain cannot express and the
    application layer starts reaching past the abstraction to get at it.
    """
    on_ports = {
        name
        for port in (DeviceCatalog, EmulatorLifecycle, SystemImageInstaller)
        for name, _ in inspect.getmembers(port, inspect.isfunction)
        if not name.startswith("_")
    }
    on_manager = {
        name
        for name, value in vars(AndroidEmulatorManager).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    }
    # aclose is lifecycle management of the adapter itself, not a domain
    # operation, so it is deliberately absent from every port.
    assert on_manager - {"aclose"} == on_ports


# ---------------------------------------------------------------------------
# Rule 2: where tests live
# ---------------------------------------------------------------------------


def _all_source_files() -> list[Path]:
    """Every source file in the module that is not itself a test or a package init."""
    return sorted(
        p for p in MODULE_ROOT.rglob("*.py") if "tests" not in p.parts and p.name != "__init__.py"
    )


def test_every_test_file_lives_in_a_tests_directory() -> None:
    """Rule 2: tests sit in `<dir>/tests/`, never beside the code they cover."""
    strays = [
        str(p.relative_to(MODULE_ROOT))
        for p in MODULE_ROOT.rglob("test_*.py")
        if "tests" not in p.parts
    ]
    assert not strays, "these tests are not inside a tests/ directory:\n" + "\n".join(strays)


def test_every_source_file_has_a_test_file_named_after_it() -> None:
    """Rule 2: `x/service.py` is covered by `x/tests/**/test_service.py`."""
    missing: list[str] = []
    for source in _all_source_files():
        expected = f"test_{source.stem}.py"
        tests_dir = source.parent / "tests"
        if not any(p.name == expected for p in tests_dir.rglob("test_*.py")):
            missing.append(f"{source.relative_to(MODULE_ROOT)} -> {tests_dir.name}/**/{expected}")
    assert not missing, "every source file needs a test file named after it:\n" + "\n".join(missing)


def test_test_directories_only_hold_unit_and_integration() -> None:
    """Rule 2: the kind of test is carried by the path, not only by a marker."""
    offenders: list[str] = []
    for tests_dir in MODULE_ROOT.rglob("tests"):
        if not tests_dir.is_dir():
            continue
        for child in tests_dir.iterdir():
            if child.is_dir() and child.name not in ("unit", "integration", "__pycache__"):
                offenders.append(str(child.relative_to(MODULE_ROOT)))
    assert not offenders, "tests/ may only contain unit/ and integration/:\n" + "\n".join(offenders)


def test_the_application_layer_is_tested_only_with_unit_tests() -> None:
    """Rule 2: wiring is asserted with mocks, so it has no integration tests."""
    integration = list((MODULE_ROOT / "application").rglob("integration/test_*.py"))
    assert not integration, (
        "the application layer is unit-tested with mocks only, found: "
        f"{[str(p.relative_to(MODULE_ROOT)) for p in integration]}"
    )


def test_services_never_import_an_adapter() -> None:
    """Rule 0 §1: `di.py` names adapters; a service is written against ports.

    The layer-wide check cannot catch this, because `application/` as a whole is
    allowed to import `infrastructure`. The restriction is on `services/`.
    """
    services_dir = MODULE_ROOT / "application" / "services"
    offenders = [
        f"{path.relative_to(MODULE_ROOT)} imports infrastructure"
        for path in services_dir.rglob("*.py")
        if "tests" not in path.parts and "infrastructure" in _imported_roots(path)
    ]
    assert not offenders, "a service depends on ports, not on adapters:\n" + "\n".join(offenders)


def test_every_service_takes_its_collaborators_through_the_constructor() -> None:
    """Rule 0 §4: a service that builds its own collaborator is not injectable."""
    import importlib

    services = importlib.import_module(f"{MODULE_PACKAGE}.application.services")
    exported = [getattr(services, name) for name in services.__all__]
    assert exported, "no services are exported"
    for service in exported:
        parameters = list(inspect.signature(service.__init__).parameters)
        assert parameters[0] == "self"
        assert len(parameters) > 1, f"{service.__name__} takes no collaborators"
        for name in parameters[1:]:
            annotation = inspect.signature(service.__init__).parameters[name].annotation
            assert annotation is not inspect.Parameter.empty, (
                f"{service.__name__}.{name} must be typed as a port"
            )
            default = inspect.signature(service.__init__).parameters[name].default
            assert default is inspect.Parameter.empty, (
                f"{service.__name__}.{name} has a default; injection must be required"
            )


def test_the_infrastructure_layer_does_not_mock() -> None:
    """Rule 1 §6: a test that patches a subprocess is testing the patch.

    The ban is on the infrastructure layer specifically -- `application/` is
    required to mock, and this check deliberately does not look there.
    """
    banned = ("unittest.mock", "MagicMock", "monkeypatch.setattr", "mocker.")
    offenders: list[str] = []
    for test_file in (MODULE_ROOT / "infrastructure").rglob("tests/**/*.py"):
        source = test_file.read_text(encoding="utf-8")
        for needle in banned:
            if needle in source:
                offenders.append(f"{test_file.relative_to(MODULE_ROOT)} uses {needle}")
    assert not offenders, "the infrastructure layer tests the real thing:\n" + "\n".join(offenders)


def _every_error_class() -> list[type[BaseException]]:
    """Both hierarchies: the domain's vocabulary and every adapter's subclasses."""
    import importlib

    found: dict[str, type[BaseException]] = {}
    package = importlib.import_module(MODULE_PACKAGE)
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{MODULE_PACKAGE}."):
        if ".tests" in info.name:
            continue
        if not info.name.endswith("errors"):
            continue
        for name, value in vars(importlib.import_module(info.name)).items():
            if (
                isinstance(value, type)
                and issubclass(value, BaseException)
                and value.__module__.startswith(MODULE_PACKAGE)
            ):
                found[f"{value.__module__}.{name}"] = value
    return list(found.values())


def test_every_error_class_was_discovered() -> None:
    """Guard the guard: a walk that silently found nothing would pass everything."""
    assert len(_every_error_class()) >= 30


@pytest.mark.parametrize("error_type", _every_error_class(), ids=lambda t: t.__name__)
def test_errors_round_trip_through_their_own_arguments(error_type: type[BaseException]) -> None:
    """Rule 0 §3 (LSP): ``type(exc)(*exc.args)`` must reconstruct the exception.

    An error that hands ``super().__init__`` a pre-rendered string instead of its
    real arguments is not copyable, not picklable, and cannot cross a process
    boundary -- it fails here with the ``AttributeError`` its consumers would see.
    """
    sample = _sample_arguments(error_type)
    original = error_type(*sample)

    rebuilt = type(original)(*original.args)
    assert str(rebuilt) == str(original)

    assert str(copy.copy(original)) == str(original)
    assert str(pickle.loads(pickle.dumps(original))) == str(original)


def _sample_arguments(error_type: type[BaseException]) -> tuple[object, ...]:
    """Plausible constructor arguments, from the signature's annotations."""
    from app_automating.modules.android_module.infrastructure.android_emulator_manager import (
        CommandResult,
    )

    # A base class that never defines __init__ inherits Exception's *args form,
    # which round-trips by construction. inspect.signature cannot read it.
    if "__init__" not in vars(error_type):
        return ("sample",)

    signature = inspect.signature(error_type)
    values: list[object] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        if parameter.default is not parameter.empty:
            continue
        annotation = str(parameter.annotation)
        if "CommandResult" in annotation:
            values.append(
                CommandResult(
                    argv=("adb", "devices"),
                    returncode=1,
                    stdout="",
                    stderr="boom",
                    duration_seconds=0.1,
                )
            )
        elif "Path" in annotation:
            values.append(Path("/tmp/at_arch"))
        elif "Sequence" in annotation:
            values.append(("a", "b"))
        elif "int" in annotation or "float" in annotation:
            values.append(1)
        else:
            values.append("sample")
    return tuple(values)


def test_the_domain_imports_on_a_host_with_no_sdk() -> None:
    """The strongest statement of Rule 0 §1, and the cheapest to check."""
    import importlib

    for name in ("models", "errors", "ports"):
        assert importlib.import_module(f"{MODULE_PACKAGE}.domain.{name}") is not None
    assert ports_module.__name__.endswith("ports")


# ---------------------------------------------------------------------------
# Rule 3: the presentation layer and the MCP surface
# ---------------------------------------------------------------------------

#: Rule 3 §5: the sections every tool description must carry, in this order.
#: The client is a model with no other context -- a missing "When not to use
#: it" is how a tool gets called in place of its neighbour.
TOOL_DOC_SECTIONS = (
    "What it does",
    "When to use it",
    "When not to use it",
    "Arguments",
    "Returns",
    "Errors",
    "Example",
)

#: The distribution package root: ``src/app_automating``. The module sits two
#: levels below it, and the server file beside ``modules/`` rather than above it
#: -- everything that ships lives under one importable name.
PACKAGE_ROOT = MODULE_ROOT.parents[1]
SERVER_FILE = PACKAGE_ROOT / "server.py"


def _register_functions() -> dict[str, object]:
    """Every ``register_*_tools`` the module's presentation layer exports."""
    import importlib

    presentation = importlib.import_module(f"{MODULE_PACKAGE}.presentation")
    return {
        name: getattr(presentation, name)
        for name in presentation.__all__
        if name.startswith("register_") and name.endswith("_tools")
    }


async def _registered_tools() -> list:
    """The module's tools, as a client would see them.

    Registered onto a throwaway server with a stand-in service: Rule 3 §2 says
    the register function builds nothing and calls nothing, so a plain object is
    a sufficient collaborator at registration time -- and if that ever stops
    being true, this helper is where it fails.
    """
    from fastmcp import FastMCP

    server = FastMCP("architecture-test")
    for register in _register_functions().values():
        register(server, object())
    return list(await server.list_tools())


def _sections(description: str) -> dict[str, str]:
    """Split a tool description into ``{section name: body}``.

    A section is a line holding exactly the section name, underlined with
    dashes -- the shape the template in Rule 3 §5 prescribes.
    """
    lines = description.splitlines()
    found: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for index, line in enumerate(lines):
        heading = line.strip()
        underlined = index + 1 < len(lines) and set(lines[index + 1].strip()) == {"-"}
        if heading in TOOL_DOC_SECTIONS and underlined:
            if current is not None:
                found[current] = "\n".join(body).strip()
            current, body = heading, []
            continue
        if set(heading) == {"-"} and heading:
            continue
        if current is not None:
            body.append(line)
    if current is not None:
        found[current] = "\n".join(body).strip()
    return found


def test_the_presentation_layer_exports_a_register_function() -> None:
    """Rule 3 §2: a module's tools reach the server through one named function."""
    assert _register_functions(), (
        "presentation/__init__.py exports no register_*_tools; server.py has nothing to call"
    )


@pytest.mark.parametrize("name", sorted(_register_functions()))
def test_a_register_function_takes_the_server_and_a_service_and_returns_none(name: str) -> None:
    """Rule 3 §2: it registers, and it builds nothing."""
    register = _register_functions()[name]
    signature = inspect.signature(register)
    parameters = list(signature.parameters)

    assert parameters[0] == "mcp"
    assert len(parameters) >= 2, f"{name} receives no service; it would have to build one"
    assert signature.return_annotation in (None, "None")
    for parameter in signature.parameters.values():
        assert parameter.default is inspect.Parameter.empty, (
            f"{name}.{parameter.name} has a default; the composition root passes it in"
        )


def test_the_presentation_layer_builds_nothing() -> None:
    """Rule 0 §4 and Rule 3 §2: composition is di.py's job, called by server.py.

    A tool that reaches for a builder has quietly become a second composition
    root, and the service it hands itself is not the one the server owns.
    """
    forbidden = ("build_android", "from_environment", "AndroidSdkConfig")
    offenders: list[str] = []
    for path in _source_files("presentation"):
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in source:
                offenders.append(f"{path.relative_to(MODULE_ROOT)} names {needle}")
    assert not offenders, "presentation receives its collaborators:\n" + "\n".join(offenders)


async def test_every_tool_name_carries_the_module_prefix() -> None:
    """Rule 3 §3: the next module must not be able to collide with this one."""
    prefix = MODULE_ROOT.name.removesuffix("_module") + "_"
    wrong = [tool.name for tool in await _registered_tools() if not tool.name.startswith(prefix)]
    assert not wrong, f"these tool names are missing the {prefix!r} prefix: {wrong}"


async def test_the_module_registers_at_least_one_tool() -> None:
    """Guard the guard: an empty surface would pass every check below."""
    assert await _registered_tools()


async def test_every_tool_describes_itself_in_full() -> None:
    """Rule 3 §5: the docstring is the whole API, and it is all the client sees."""
    offenders: list[str] = []
    for tool in await _registered_tools():
        description = tool.description or ""
        summary = description.strip().splitlines()[0] if description.strip() else ""
        if not summary:
            offenders.append(f"{tool.name}: no summary line")
        sections = _sections(description)
        for section in TOOL_DOC_SECTIONS:
            if section not in sections:
                offenders.append(f"{tool.name}: missing the {section!r} section")
            elif not sections[section]:
                offenders.append(f"{tool.name}: the {section!r} section is empty")
    assert not offenders, "every tool carries the full template:\n" + "\n".join(offenders)


async def test_every_tool_argument_is_documented_and_no_others_are() -> None:
    """Rule 3 §8: the Arguments section and the signature must agree both ways.

    An undocumented parameter is a guess the caller has to make; a documented
    parameter that no longer exists is a call that will fail.
    """
    offenders: list[str] = []
    for tool in await _registered_tools():
        declared = set(tool.parameters.get("properties", {}))
        arguments = _sections(tool.description or "").get("Arguments", "")
        documented = {
            line.strip().rstrip(":")
            for line in arguments.splitlines()
            if line.strip().endswith(":") and not line.startswith((" ", "\t"))
        }
        for missing in sorted(declared - documented):
            offenders.append(f"{tool.name}: {missing} is not documented")
        for extra in sorted(documented - declared):
            offenders.append(f"{tool.name}: documents {extra}, which is not a parameter")
    assert not offenders, "Arguments must match the signature:\n" + "\n".join(offenders)


async def test_every_tool_returns_a_declared_shape() -> None:
    """Rule 3 §4: a payload with a schema, never a bare dict or string."""
    undeclared = [tool.name for tool in await _registered_tools() if not tool.output_schema]
    assert not undeclared, f"these tools return an undeclared shape: {undeclared}"


def test_the_server_registers_every_surface_this_module_exposes() -> None:
    """Rule 3 §1: a module whose tools were never registered is invisible.

    ``src/app_automating/server.py`` is read as text on purpose -- importing it would build the
    real object graph, and this file must keep running on a host with no SDK.
    """
    assert SERVER_FILE.is_file(), f"expected the MCP server at {SERVER_FILE}"
    source = SERVER_FILE.read_text(encoding="utf-8")
    unregistered = [name for name in _register_functions() if f"{name}(" not in source]
    assert not unregistered, (
        f"{SERVER_FILE.name} never calls: {unregistered}. A module's tools reach the "
        "server through one import and one call."
    )


def test_the_server_defines_no_tool_of_its_own() -> None:
    """Rule 3 §1: a `@mcp.tool` in server.py is a presentation layer never written."""
    source = SERVER_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SERVER_FILE))
    offenders = [
        f"{SERVER_FILE.name}:{node.lineno} defines a tool"
        for node in ast.walk(tree)
        for decorator in getattr(node, "decorator_list", [])
        if "tool" in ast.dump(decorator)
    ]
    assert not offenders, "tools belong to a module's presentation layer:\n" + "\n".join(offenders)


def test_the_server_never_reaches_into_a_modules_infrastructure() -> None:
    """Rule 3 §1: server.py wires through di.py, and names no adapter.

    The imports are what is checked, not the text: the file is free to *say*
    that it never touches infrastructure, and this is what makes that true.
    """
    tree = ast.parse(SERVER_FILE.read_text(encoding="utf-8"), filename=str(SERVER_FILE))
    offenders = [
        f"{SERVER_FILE.name}:{node.lineno} imports {node.module}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and "infrastructure" in (node.module or "")
    ]
    offenders += [
        f"{SERVER_FILE.name}:{node.lineno} imports {alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if "infrastructure" in alias.name
    ]
    assert not offenders, "di.py is what builds adapters:\n" + "\n".join(offenders)
