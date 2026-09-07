# What this changes

<!-- One paragraph. What behaviour is different after this PR, and why. -->

Closes #

## Type of change

- [ ] Bug fix
- [ ] New tool on an existing module
- [ ] New module
- [ ] Refactor with no behaviour change
- [ ] Docs
- [ ] Build, CI or packaging

## Which layer

<!--
Rule 0 is binding on every line under src/. Tick what you touched, and if you
crossed a boundary, say which rule permits it.
-->

- [ ] `domain/` — entities, ports, the failure vocabulary. No I/O, no frameworks.
- [ ] `infrastructure/` — adapters for host tools, devices, the filesystem, the DB
- [ ] `application/` — services and `di.py`
- [ ] `presentation/` — MCP tools, schemas, rendering, error translation
- [ ] `server.py`, packaging, or docs

## Checks

- [ ] `uv run pytest -m unit` passes
- [ ] `uv run ruff check src && uv run ruff format --check src` passes
- [ ] Every new source file has a `test_<name>.py` under its sibling `tests/`
- [ ] The marker on each test module matches its directory (`unit` / `integration`)
- [ ] No mocks under `infrastructure/`; the application layer is mocked in `presentation/` tests

### If you added or changed a tool

- [ ] The name is `<module>_<operation>` and matches the service method it calls
- [ ] The docstring carries all eight sections of Rule 3 §5, each non-empty
- [ ] **Arguments** names every parameter in the signature, and no others
- [ ] **Returns** shows a literal example payload — the same one the rendering test asserts
- [ ] **Errors** lists every distinguishable failure *with a remedy*
- [ ] Error translation is a new row in `presentation/errors.py`, not a new `if`

## Integration tests

<!--
CI cannot run these: a GitHub runner has no Android SDK and no device. Tell us
what you ran on your own machine, or say that you could not.
-->

- [ ] I ran `uv run pytest -m integration` locally — result:
- [ ] I could not run them, because:

## Anything left uncovered

<!--
Rule 1 §6: if a behaviour cannot be exercised for real, leaving it uncovered and
saying so beats faking it to green. Name anything in that position here.
-->
