# Rule 2 — Where tests live, and what they may fake

Status: active. Applies to every layer. Enforced by
`android_module/tests/unit/test_architecture.py`, not by review.

This rule **supersedes Rule 1 §6's** "one integration file per component, not one
per source file". The reasoning behind that sentence — that a test named after a
source file drifts into testing internals — was written when the component was
one large module. Now that a component is split by kind, each source file *is* a
coherent unit with its own public surface, and one test file per source file is
what makes a failure legible: `test_process.py` failing means the process layer
broke, not "something in the emulator flow broke".

---

## 1. A test file sits in `tests/`, beside the file it covers

For any source file `x/service.py`, its tests live in `x/tests/`:

```
<directory>/
├── service.py
├── other.py
└── tests/
    ├── unit/test_service.py
    └── integration/test_other.py
```

* The `tests/` directory is a **sibling of the code**, inside the same directory.
  Never a top-level `tests/` tree, and never a test file beside the source.
* The name is mechanical: `service.py` → `test_service.py`. Every source file
  has one. A source file with nothing worth testing is a source file worth
  deleting.
* `tests/`, `tests/unit/` and `tests/integration/` are all packages
  (`__init__.py`), so files with the same basename in different directories
  cannot collide — and they will collide: `test_config.py` legitimately exists
  in both `unit/` and `integration/`.

A component that is a *package* is the unit that holds the `tests/` directory,
and its own source files are what the test files are named after:

```
infrastructure/android_emulator_manager/
├── config.py  models.py  errors.py  parsing.py
├── process.py  filesystem.py  manager.py
└── tests/
    ├── unit/          test_models.py  test_errors.py
    │                  test_parsing.py  test_config.py
    └── integration/   test_config.py   test_process.py
                       test_filesystem.py  test_manager.py
```

One source file may appear in both `unit/` and `integration/` when it has both a
pure surface and a host-dependent one. `config.py` does: its resolution rules
take an environment mapping and are pure, while "does this actually find the SDK
on this machine" is not.

Shared fixtures go in a `conftest.py` at the narrowest level that needs them.
Hang an autouse session fixture off `integration/` rather than `tests/` — an
autouse guard that depends on a real SDK will otherwise skip the pure unit tests
too, on exactly the machines where they are the only thing that can run.

## 2. `unit/` and `integration/` mean different things, and it is not "fast/slow"

* **`integration/`** — the code under test reaches something the process does not
  control: a binary, a device, a socket, the filesystem, the clock.
* **`unit/`** — it does not.

The split is by *what the code touches*, not by how long the test takes. A test
of a pure parser is a unit test even against a 200-line sample; a test of
`AvdStore` against `tmp_path` is an integration test even though it finishes in
a millisecond, because a real directory is a real directory.

Mark every module with the matching marker (`pytestmark = pytest.mark.unit` or
`pytest.mark.integration`) so `-m unit` selects correctly. The path and the
marker must agree.

## 3. Mocking: forbidden in infrastructure, required in application

This is the part that most often gets applied uniformly and should not be.

| Layer | Kind | Mocks |
|---|---|---|
| `domain` | unit | **never needed** — the domain has no collaborators to fake |
| `infrastructure` | mostly integration | **forbidden** (Rule 1 §6) |
| `application` | unit only | **required** |
| `presentation` | unit | mock the application, never the infrastructure |

* **`infrastructure/` may not mock, patch, stub or fake anything.** Real `adb`,
  real AVDs, a real emulator boot, a real 1ms timeout against a real process. A
  test that patches a subprocess is testing the patch. Unit tests are permitted
  here only for genuinely pure modules — `models`, `errors`, `parsing` — and
  those still may not mock: they are unit tests because the *code* is pure, not
  because its collaborators were replaced.

* **`application/` is tested with unit tests and mocks, and only those.** Its job
  is to connect objects, so what needs asserting is which object was passed to
  which constructor — `runner_cls.assert_called_once_with(config)`. Building the
  real collaborators would drag a real Android SDK into a test that is not about
  the SDK. It has no `integration/` directory at all.

  One exception is worth keeping in that file: a single assertion that what the
  composition root really builds satisfies the ports it claims. A wiring test
  made entirely of mocks would not notice the adapter drifting from its
  abstraction.

* **`domain/` needs no fakes.** If a domain test seems to want one, the code
  under test has grown a dependency it should not have — fix the code, not the
  test.

## 4. The one exception: the architecture test

`<module>/tests/unit/test_architecture.py` corresponds to no source file. It
asserts the *shape* of the module — the layer boundaries of Rule 0, the
placement rules above — so it lives at the module root rather than beside
anything, and it is the only test file permitted to.

## 5. What the enforcement actually checks

In `test_architecture.py`:

* every `test_*.py` in the module is inside a `tests/` directory;
* every source file has a `test_<name>.py` under its sibling `tests/`;
* `tests/` contains nothing but `unit/` and `integration/`;
* `application/` has no `integration/` directory;
* no file under `infrastructure/**/tests/` mentions `unittest.mock`,
  `MagicMock`, `monkeypatch.setattr` or `mocker.`.
