# Contributing to app-automating

Thanks for taking the time. This project has an unusual amount of structure for
its size, and most of it is enforced by tests rather than by review — so the
fastest way to get a change merged is to know which rule your change is under
before you write it.

## Setting up

```bash
git clone https://github.com/dkmostafa/app-automating
cd app-automating
uv sync
uv run pytest -m unit
```

That last command is the one that must pass on any machine. It needs no Android
SDK, no device and no network, and it takes under a second. If it fails on a
fresh clone, that is a bug — please open an issue.

To exercise the rest you also need the Android SDK (`adb`, `emulator`,
`avdmanager`, `sdkmanager` — set `ANDROID_SDK_ROOT`), hardware acceleration, a
downloaded system image, Node.js, the `appium` CLI and the `uiautomator2`
driver. `uv run app-automating` then serves the tools, and
`appium_check_environment` reports whatever is still missing.

## The rules

Read [Rule 0](.claude/rules/clean-architecture-and-solid.md) before anything
else. It outranks the others and is binding on every line under `src/`.

| Rule | Covers |
|---|---|
| [Rule 0](.claude/rules/clean-architecture-and-solid.md) | The four layers, the direction dependencies point, who owns a port, where composition happens |
| [Rule 1](.claude/rules/infrastructure-layer.md) | Adapters: one component per package, dataclasses in and out, typed errors, injected config, owning your subprocesses |
| [Rule 2](.claude/rules/test-placement.md) | Where a test file lives, and what it may fake |
| [Rule 3](.claude/rules/presentation-layer.md) | The MCP surface: tool names, signatures, and the docstring template |

[`CLAUDE.md`](CLAUDE.md) is the map that sits above them.

The three points that catch people most often:

1. **A port is declared by the layer that calls it, not the one that implements
   it.** The `Protocol` lives in `domain/ports.py`; the adapter in
   `infrastructure/` imports it. Anything appearing in a port signature — request
   dataclasses, result dataclasses, errors — belongs to `domain/` too.

2. **Mocking is layer-specific, not uniform.** It is *forbidden* in
   `infrastructure/` (those tests run against the real host; a test that patches
   a subprocess is testing the patch) and *required* in `application/` (those
   tests are about wiring). `domain/` needs no fakes at all — if a domain test
   wants one, the code under test has grown a dependency it should not have.

3. **A tool's docstring is its entire API.** It is the only thing the model
   calling it will ever see. Rule 3 §5 gives a fixed template with eight
   mandatory sections, and the architecture test fails if one is missing or if
   the **Arguments** section does not name every parameter in the signature.

## Adding a tool

1. The operation goes on the service in `application/services/`, named the way a
   user would say it, taking the arguments they actually have.
2. The tool goes in `presentation/<surface>_tools.py`, named
   `<module>_<operation>`, with the full docstring template.
3. Rendering goes in `presentation/rendering.py`, error translation in
   `presentation/errors.py` as a **table row**, not an `if`.
4. Tests go in `presentation/tests/unit/`, with the application mocked.

The docstring's **Returns** example and the assertion in the rendering test
should be the same payload. If they disagree, one of them is lying.

## Adding a module

A new module is four layers, no more and no fewer, plus its own
`tests/unit/test_architecture.py`. Wiring it into the product is one import and
one `register_*_tools` call in `src/app_automating/server.py`. If it takes more
than that, the module's presentation layer is doing too little.

## Tests

```bash
uv run pytest -m unit          # what CI runs, and what must pass everywhere
uv run pytest -m integration   # the real toolchain, on your machine only
uv run pytest                  # everything
```

The split is by **what the code under test touches**, not by how long the test
takes. A pure parser is a unit test against a 200-line sample; a store tested
against `tmp_path` is an integration test that finishes in a millisecond. Mark
every module with the marker matching its directory — the path and the marker
must agree.

> [!WARNING]
> The integration suite boots real emulators and starts real Appium servers on
> your machine. It cleans up after itself, refuses to touch a resource it did not
> create, and skips rather than failing on a host that cannot run it — but it
> takes over your display and costs about a minute. Run it deliberately.

## Style

```bash
uv run ruff check src && uv run ruff format src
```

Line length is 100. CI runs `ruff check` and `ruff format --check`, so format
before you push.

Beyond the linter, the house style is that **comments explain why, not what**.
A comment saying what a line does is noise; a comment saying which trap the line
avoids is the reason the file is readable a year later. Look at any existing
module before writing your first one — the density and the voice are the target.

## Pull requests

- One concern per PR.
- `uv run pytest -m unit` and `ruff check` pass.
- If you touched a layer boundary, say which rule permits it.
- If you could not test something for real, say so. Rule 1 §6 is explicit that
  leaving a behaviour uncovered and naming it beats faking it to green.

## Releasing

For maintainers. `.github/workflows/release.yml` does the work; these are the
parts a workflow cannot do for you.

### One-time: set up Trusted Publishing

There is no API token anywhere in this repository, and there should never be
one. PyPI mints a short-lived credential for this exact workflow instead. Add a
*pending publisher* on both indexes — [PyPI](https://pypi.org/manage/account/publishing/)
and [TestPyPI](https://test.pypi.org/manage/account/publishing/) — with:

| Field | Value |
|---|---|
| PyPI project name | `app-automating` |
| Owner | `dkmostafa` |
| Repository | `app-automating` |
| Workflow name | `release.yml` |
| Environment | `pypi` on PyPI, `testpypi` on TestPyPI |

Then create those two environments under **Settings → Environments** in the
repository. Adding a required reviewer to `pypi` makes every release pause for a
human before it becomes public, which is worth the two seconds.

### Each release

1. Bump `__version__` in `src/app_automating/__init__.py`. That is the only
   place a version is written — `pyproject.toml` reads it from there.
2. Commit it, then tag and push:

   ```bash
   git tag v0.1.0 && git push origin v0.1.0
   ```

The workflow then checks the tag against `__version__` and refuses to publish if
they disagree, runs lint and `pytest -m unit`, builds, checks the metadata with
`twine`, publishes to TestPyPI, publishes to PyPI, and attaches the artifacts to
a generated GitHub release.

To rehearse without tagging anything, run the workflow manually from the Actions
tab and choose `TestPyPI`.

## Reporting a bug

Use the bug report template — it asks for the host details (OS, SDK version,
Appium version, device or emulator) that determine whether a failure is
reproducible at all. For a security issue, see [SECURITY.md](SECURITY.md)
instead; please do not open a public issue for one.
