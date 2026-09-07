"""Rules 0, 2 and 3, asserted rather than documented.

Every check here reads source or imported objects. Nothing spawns a process,
touches a device or needs Appium installed, which is itself part of the point:
if this file cannot run on a bare machine, the domain is not as pure as it
claims.

This is the one test file in the module that corresponds to no source file
(Rule 2 §4). It asserts the *shape* of the module, so it sits at the module root
rather than beside anything.
"""

from __future__ import annotations

import ast
import copy
import inspect
import pickle
from pathlib import Path

import pytest

from app_automating.modules.navigation_memory_module.application import build_navigation_store
from app_automating.modules.navigation_memory_module.domain import ports as ports_module
from app_automating.modules.navigation_memory_module.domain.ports import (
    MemoryMaintenance,
    RouteMemory,
    RouteRecorder,
)
from app_automating.modules.navigation_memory_module.infrastructure.navigation_store import (
    NavigationStore,
    NavigationStoreConfig,
)

pytestmark = pytest.mark.unit

MODULE_ROOT = Path(__file__).resolve().parents[2]
MODULE_PACKAGE = "app_automating.modules.navigation_memory_module"

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
#: whatever the import spelling. `sqlalchemy` matters most here: this module is
#: *about* persistence, which makes it the module most likely to let the ORM
#: leak inward -- a domain that imported a table definition would have made the
#: schema its business rule instead of the other way round.
FRAMEWORK_PACKAGES = frozenset(
    {"fastmcp", "mcp", "appium", "selenium", "sqlalchemy", "aiosqlite", "pydantic"}
)

#: I/O the domain must not reach for directly, even from the standard library.
#:
#: `hashlib`, `xml` and `heapq` are deliberately absent: `fingerprinting.py` and
#: `routing.py` are pure computation over a string and a list of edges, which is
#: exactly what a business rule is allowed to be. The test below that imports
#: every domain module on a bare host is what keeps that honest.
DOMAIN_FORBIDDEN_STDLIB = frozenset(
    {"subprocess", "socket", "shutil", "os", "asyncio", "urllib", "http", "sqlite3"}
)

#: The three ports, and what the adapter must satisfy.
ALL_PORTS = (RouteMemory, RouteRecorder, MemoryMaintenance)


def _source_files(ring: str) -> list[Path]:
    directory = MODULE_ROOT / ring
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*.py") if "tests" not in p.parts)


def _all_source_files() -> list[Path]:
    """Every source file in the module that is not itself a test or a package init."""
    return sorted(
        p for p in MODULE_ROOT.rglob("*.py") if "tests" not in p.parts and p.name != "__init__.py"
    )


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


# ---------------------------------------------------------------------------
# Rule 0: the layers and the direction dependencies point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_IMPORTS))
def test_layers_only_import_what_they_are_allowed_to(layer: str) -> None:
    """Rule 0 §1: the dependency rule, layer by layer."""
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
    """Rule 0 §1: frameworks stay at the edges, and the domain is the centre.

    For this module that means the Appium client itself: the domain describes a
    session, and must not know that a session is a WebDriver connection.
    """
    offenders: list[str] = []
    for path in _source_files("domain"):
        for imported in sorted(_imported_roots(path) & FRAMEWORK_PACKAGES):
            offenders.append(f"{path.relative_to(MODULE_ROOT)} imports {imported}")
    assert not offenders, "frameworks belong in infrastructure/ or presentation/:\n" + "\n".join(
        offenders
    )


def test_domain_does_no_io() -> None:
    """Rule 0 §1: a domain file must be importable on a host with nothing installed."""
    offenders: list[str] = []
    for path in _source_files("domain"):
        for imported in sorted(_imported_roots(path) & DOMAIN_FORBIDDEN_STDLIB):
            offenders.append(f"{path.relative_to(MODULE_ROOT)} imports {imported}")
    assert not offenders, "the domain performs no I/O:\n" + "\n".join(offenders)


def test_the_domain_imports_on_a_host_with_no_database() -> None:
    """The practical form of the rule above: import it and see.

    A domain that cannot be imported without SQLAlchemy is a domain that has
    quietly become a schema.
    """
    import importlib

    for name in ("models", "errors", "ports", "fingerprinting", "routing"):
        module = importlib.import_module(f"{MODULE_PACKAGE}.domain.{name}")
        assert module.__all__


def test_only_the_composition_root_reads_the_environment() -> None:
    """Rule 0 §4: ambient state is resolved in exactly one place."""
    offenders: list[str] = []
    for ring in ("domain", "infrastructure", "presentation"):
        for path in _source_files(ring):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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


@pytest.mark.parametrize("port", ALL_PORTS, ids=lambda p: p.__name__)
def test_the_adapter_satisfies_every_port_it_is_injected_as(port: type) -> None:
    """Rule 0 §5: the adapter is checked against the abstraction, not assumed.

    Built for real -- construction touches no host tool and spawns nothing until
    a method is awaited, which is what keeps this runnable on a bare machine.
    """
    store = build_navigation_store(NavigationStoreConfig())

    assert isinstance(store, port)


def test_the_composition_root_returns_something_that_satisfies_the_ports() -> None:
    """The same check one level up: what `di.py` hands a service is a real port."""
    from app_automating.modules.navigation_memory_module.application.di import navigation_ports

    ports = navigation_ports(build_navigation_store(NavigationStoreConfig()))

    assert len(ports) == len(ALL_PORTS)
    for port_object, port_type in zip(ports, ALL_PORTS, strict=True):
        assert isinstance(port_object, port_type)


def test_ports_cover_the_managers_whole_public_surface() -> None:
    """A new operation must land on a port, not just on the adapter.

    Without this, an adapter grows a method the domain cannot express and the
    application layer starts reaching past the abstraction to get at it.
    """
    on_ports = {
        name
        for port in ALL_PORTS
        for name, _ in inspect.getmembers(port, inspect.isfunction)
        if not name.startswith("_")
    }
    on_manager = {
        name
        for name, value in vars(NavigationStore).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    }
    # aclose is lifecycle management of the adapter itself, not a domain
    # operation, so it is deliberately absent from every port.
    assert on_manager - {"aclose"} == on_ports


def test_the_ports_are_segregated() -> None:
    """Rule 0 §3 (ISP): narrow ports named after a need, not one big interface."""
    seen: set[str] = set()
    for port in ALL_PORTS:
        methods = {
            name
            for name, _ in inspect.getmembers(port, inspect.isfunction)
            if not name.startswith("_")
        }
        assert not (methods & seen), f"{port.__name__} repeats {methods & seen}"
        seen |= methods


def test_every_port_is_exported_from_the_domain() -> None:
    assert set(ports_module.__all__) == {port.__name__ for port in ALL_PORTS}


# ---------------------------------------------------------------------------
# Rule 0 §3 (L): errors reconstruct from their own arguments
# ---------------------------------------------------------------------------


def _error_classes() -> list[type[BaseException]]:
    """Every error class the module defines, domain and adapter alike."""
    import importlib

    found: list[type[BaseException]] = []
    for module_name in (
        f"{MODULE_PACKAGE}.domain.errors",
        f"{MODULE_PACKAGE}.infrastructure.navigation_store.errors",
    ):
        module = importlib.import_module(module_name)
        for value in vars(module).values():
            if inspect.isclass(value) and issubclass(value, BaseException):
                if value.__module__ == module_name:
                    found.append(value)
    return sorted(found, key=lambda cls: cls.__name__)


def test_every_error_class_was_discovered() -> None:
    """Guard the guard: an empty list would make the check below vacuous."""
    assert len(_error_classes()) > 15


@pytest.mark.parametrize("error_type", _error_classes(), ids=lambda cls: cls.__name__)
def test_errors_round_trip_through_their_own_arguments(error_type: type[BaseException]) -> None:
    """Rule 0 §3 (L): `type(exc)(*exc.args)` must reconstruct the exception.

    The adapter's errors have two bases, so this is where a co-operative
    `super().__init__` creeping back in gets caught -- it would dispatch along
    the MRO into the sibling constructor with the wrong arity.
    """
    signature = inspect.signature(error_type.__init__)
    required = [
        name
        for name, parameter in signature.parameters.items()
        if name != "self"
        and parameter.default is inspect.Parameter.empty
        and parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
    ]
    arguments = ["sample"] * len(required)
    try:
        exc = error_type(*arguments)
    except TypeError:
        pytest.skip(f"{error_type.__name__} needs typed arguments; covered by its own test file")

    rebuilt = type(exc)(*exc.args)

    assert rebuilt.args == exc.args
    assert str(rebuilt) == str(exc)
    assert str(pickle.loads(pickle.dumps(exc))) == str(exc)
    assert str(copy.copy(exc)) == str(exc)


# ---------------------------------------------------------------------------
# Rule 2: where tests live, and what they may fake
# ---------------------------------------------------------------------------


def test_every_test_file_lives_in_a_tests_directory() -> None:
    """Rule 2 §1: tests sit in `<dir>/tests/`, never beside the code they cover."""
    strays = [
        str(p.relative_to(MODULE_ROOT))
        for p in MODULE_ROOT.rglob("test_*.py")
        if "tests" not in p.parts
    ]
    assert not strays, "these tests are not inside a tests/ directory:\n" + "\n".join(strays)


def test_every_source_file_has_a_test_file_named_after_it() -> None:
    """Rule 2 §1: `x/service.py` is covered by `x/tests/**/test_service.py`."""
    missing: list[str] = []
    for source in _all_source_files():
        expected = f"test_{source.stem}.py"
        tests_dir = source.parent / "tests"
        if not any(p.name == expected for p in tests_dir.rglob("test_*.py")):
            missing.append(f"{source.relative_to(MODULE_ROOT)} -> {tests_dir.name}/**/{expected}")
    assert not missing, "every source file needs a test file named after it:\n" + "\n".join(missing)


def test_test_directories_only_hold_unit_and_integration() -> None:
    """Rule 2 §1: the kind of test is carried by the path, not only by a marker."""
    offenders: list[str] = []
    for tests_dir in MODULE_ROOT.rglob("tests"):
        if not tests_dir.is_dir():
            continue
        for child in tests_dir.iterdir():
            if child.is_dir() and child.name not in ("unit", "integration", "__pycache__"):
                offenders.append(str(child.relative_to(MODULE_ROOT)))
    assert not offenders, "tests/ may only contain unit/ and integration/:\n" + "\n".join(offenders)


def test_the_application_layer_is_tested_only_with_unit_tests() -> None:
    """Rule 2 §3: wiring is asserted with mocks, so it has no integration tests."""
    integration = list((MODULE_ROOT / "application").rglob("integration/test_*.py"))
    assert not integration, (
        "the application layer is unit-tested with mocks only, found: "
        f"{[str(p.relative_to(MODULE_ROOT)) for p in integration]}"
    )


def test_the_infrastructure_layer_does_not_mock() -> None:
    """Rule 2 §3: this layer tests the real host. A test that patches a
    subprocess is testing the patch.
    """
    forbidden = ("unittest.mock", "MagicMock", "AsyncMock", "monkeypatch.setattr", "mocker.")
    offenders: list[str] = []
    for path in (MODULE_ROOT / "infrastructure").rglob("tests/**/*.py"):
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in source:
                offenders.append(f"{path.relative_to(MODULE_ROOT)} mentions {needle}")
    assert not offenders, "infrastructure tests use the real thing:\n" + "\n".join(offenders)


def test_services_never_import_an_adapter() -> None:
    """Rule 0 §1: `di.py` names adapters; a service is written against ports.

    The layer-wide check cannot catch this, because `application/` as a whole is
    allowed to import `infrastructure`. The restriction is on `services/`.
    """
    offenders: list[str] = []
    for path in (MODULE_ROOT / "application" / "services").rglob("*.py"):
        if "tests" in path.parts:
            continue
        if "infrastructure" in _imported_roots(path):
            offenders.append(str(path.relative_to(MODULE_ROOT)))
    assert not offenders, "a service depends on ports, never on an adapter:\n" + "\n".join(
        offenders
    )


def test_every_service_takes_its_collaborators_through_the_constructor() -> None:
    """Rule 0 §4: no argument may default to a live object."""
    from app_automating.modules.navigation_memory_module.application.services import (
        NavigationMemoryService,
    )

    signature = inspect.signature(NavigationMemoryService.__init__)
    for name, parameter in signature.parameters.items():
        if name == "self":
            continue
        assert parameter.default is inspect.Parameter.empty, (
            f"NavigationMemoryService.{name} has a default; the composition root passes it in"
        )


# ---------------------------------------------------------------------------
# Rule 3: the presentation layer and the MCP surface
# ---------------------------------------------------------------------------

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
    forbidden = ("build_navigation", "from_environment", "NavigationStoreConfig")
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
    real object graph, and this file must keep running on a bare host.
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
    tree = ast.parse(SERVER_FILE.read_text(encoding="utf-8"), filename=str(SERVER_FILE))
    offenders = [
        f"{SERVER_FILE.name}:{node.lineno} defines a tool"
        for node in ast.walk(tree)
        for decorator in getattr(node, "decorator_list", [])
        if "tool" in ast.dump(decorator)
    ]
    assert not offenders, "tools belong to a module's presentation layer:\n" + "\n".join(offenders)


def test_the_server_never_reaches_into_a_modules_infrastructure() -> None:
    """Rule 3 §1: server.py wires through di.py, and names no adapter."""
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


# ---------------------------------------------------------------------------
# the cross-module seam
# ---------------------------------------------------------------------------
#
# This is the only module in the product that names another one, so these are
# the checks nothing else in the suite can make.

OTHER_MODULE = "app_automating.modules.appium_module"


def _appium_imports() -> dict[str, set[str]]:
    """Every ``app_automating.modules.appium_module.*`` path this module imports, by file."""
    found: dict[str, set[str]] = {}
    for path in _all_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        paths: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(OTHER_MODULE):
                paths.add(node.module or "")
            elif isinstance(node, ast.Import):
                paths |= {a.name for a in node.names if a.name.startswith(OTHER_MODULE)}
        if paths:
            found[str(path.relative_to(MODULE_ROOT))] = paths
    return found


def test_only_the_recording_adapter_knows_the_other_module_exists() -> None:
    """The coupling is one component wide, and stays that way.

    A second file reaching for ``appium_module`` means the seam has started to
    spread, which is the thing a single named adapter is supposed to prevent.
    ``application/di.py`` deliberately does not appear here: it constructs
    :class:`RecordingDeviceDriver` (a class of this module's own) and passes its
    ports through untyped, precisely so it never has to import appium's
    Protocols to do it.
    """
    reaching = set(_appium_imports())

    assert reaching <= {"infrastructure/appium_recorder/driver.py"}, (
        f"only the recording adapter may name {OTHER_MODULE}, found: {sorted(reaching)}"
    )


def test_the_seam_touches_only_the_other_modules_domain() -> None:
    """Rule 0 §1 across a module boundary: the outer ring of this module may
    depend on the *innermost* ring of that one, and on nothing else.

    Importing its application layer would couple two composition roots;
    importing its infrastructure would couple two sets of adapters. Both are the
    same mistake as reaching past a port inside one module.
    """
    offenders = [
        f"{where} imports {imported}"
        for where, imports in _appium_imports().items()
        for imported in sorted(imports)
        if not imported.startswith(f"{OTHER_MODULE}.domain")
    ]
    assert not offenders, "the seam is one Protocol and its DTOs:\n" + "\n".join(offenders)


def test_the_other_module_never_imports_this_one() -> None:
    """The arrow points one way. ``appium_module`` declares the port it needs and
    must never learn what satisfies it."""
    other_root = MODULE_ROOT.parent / "appium_module"
    this_module = MODULE_ROOT.name
    offenders: list[str] = []
    for path in other_root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: set[str] = set()
            if isinstance(node, ast.ImportFrom):
                imported = {node.module or ""}
            elif isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
            if any(this_module in name for name in imported):
                offenders.append(f"{path.relative_to(other_root)}:{node.lineno}")
    # Naming this module in a *docstring* is fine and in fact desirable -- di.py
    # says who fills the port-decorator hook today. Importing it is what would
    # invert the arrow, so this reads the AST rather than the text.
    assert not offenders, f"appium_module must not import {this_module}:\n" + "\n".join(offenders)


def test_the_recording_adapter_satisfies_the_ports_it_claims() -> None:
    """Rule 0 §5, across the boundary: checked, not assumed.

    ``RecordingDeviceDriver`` stands in for ``appium_module``'s own
    ``SessionLifecycle`` and ``DeviceInteraction`` -- built here through the same
    ``decorate`` hook the composition root uses, so what is checked is exactly
    what a real wiring produces.
    """
    from app_automating.modules.appium_module.domain.ports import (
        DeviceInteraction,
        SessionLifecycle,
    )
    from app_automating.modules.navigation_memory_module.application.di import (
        build_appium_port_decorator,
    )

    decorate = build_appium_port_decorator(build_navigation_store(NavigationStoreConfig()))
    sessions, interaction = decorate(object(), object(), object())

    assert isinstance(sessions, SessionLifecycle)
    assert isinstance(interaction, DeviceInteraction)
