# `navigation_memory_module`

Owns **remembering where the device has been**. Every route taken on a device is
written down as it happens, so the next run can look up how it reached a screen
instead of rediscovering it by trial and error.

It does not drive a device and does not list one. Routes are *pushed* into it by
`appium_module` as that module drives — this one decides only how they are
stored and how they are asked for again later.

## The five tools

All of them reads. Nothing on the MCP surface records anything, because
recording happens on its own.

| Tool | What it answers |
|---|---|
| `navigation_memory_where_am_i` | Which screen this session is on, and what has worked from here (with success rates) |
| `navigation_memory_find_path` | The best-known sequence of actions from here to a named destination |
| `navigation_memory_search_screens` | Which screens are known for an app — how a destination gets a name |
| `navigation_memory_get_run` | Replay one run's journal, failures included |
| `navigation_memory_forget` | Drop learned memory by app, by run, or by age |

The first call on any screen is `where_am_i`. If it answers `ScreenNotRecognised`
that is not a fault — it means explore, and what you do next is recorded.

> **Status: complete.** All four layers, five MCP tools, and the cross-module
> seam that fills the map automatically. 20 test files, 325 tests.

---

## The structure

Four tables in two halves, plus one for bookkeeping. The split is the design:

```
      the journal — what happened            the map — what was learned
      ────────────────────────────           ──────────────────────────
      run    one drive of one app            screen      a place, deduplicated
      step   one action, in order            transition  an edge, with counts
```

`step` is append-only and lossless — every action, including the ones that
failed, in sequence. It answers *"what did this run do?"*.

`transition` is those steps folded into knowledge — one row per distinct edge
however many times it was walked. It answers *"what usually works from here?"*.

Keeping both costs one extra write per action and means neither question is
derived at read time.

```
schema_meta  key · value                              -- schema_version lives here

screen       id · package · activity · fingerprint    -- UNIQUE identity
             title · page_source · screenshot_path
             first_seen_at · last_seen_at · visit_count

transition   id · from_screen_id → to_screen_id       -- UNIQUE(from,action,target,to)
             action · target
             traversal_count · success_count          -- CHECK success <= traversal
             first_seen_at · last_seen_at · last_app_version

run          id · device_id · package · app_version
             session_id · started_at · ended_at       -- NULL while in flight

step         id · run_id → run · seq                  -- UNIQUE(run_id, seq)
             from_screen_id · action · target · to_screen_id
             ok · detail · at
```

Indexed for the two queries the module exists for: `ix_transition_from`
("what can I do from here") and `ix_transition_to` ("how do I get there",
searched backwards from a goal).

### Three decisions worth knowing

**A screen is `(package, activity, fingerprint)`, and both halves are stored.**
Activity alone is too coarse — a Compose or React Native app runs its whole UI
in one activity, and matching on it would collapse the graph to a single node.
Fingerprint alone is too brittle — a cart badge going `2` to `3` would mint a
new screen forever. Storing both lets a lookup prefer the fingerprint and
degrade to the activity without a migration. What goes *into* the fingerprint is
the caller's business; this layer stores the hash it is given.

**Every column inside a `UNIQUE` constraint is `NOT NULL`, with `""` for
unknown.** Not tidiness: SQL treats two `NULL`s as distinct inside a unique
index, so one nullable `target` would silently turn "one row per edge" into
"unlimited rows per edge" and break the deduplication the whole map depends on.
`test_every_uniquely_constrained_column_is_not_null` enforces it.

**Timestamps go through `UtcDateTime`.** SQLite has no datetime type and
SQLAlchemy's own `DateTime` drops the offset going in and returns a naive value
coming out. This stores a fixed-width ISO-8601 UTC string instead, so what comes
back is aware, `ORDER BY last_seen_at` is correct on the raw text, and the file
stays readable under the `sqlite3` CLI — which is where a developer will
actually go looking.

## How it is built

```
navigation_memory_module/
└── infrastructure/
    └── navigation_store/                  # one component, split by kind
        ├── models.py     DatabaseLocation · SchemaState — never cross a port
        ├── errors.py     8 typed failures, grouped by what the caller should do
        ├── parsing.py    pure: PRAGMA sets, failure classification, path checks
        ├── tables.py     the schema. Every table, column, index, constraint
        ├── config.py     NavigationStoreConfig + the only AT_NAVIGATION_MEMORY_ reader
        ├── engine.py     NavigationDatabase: engine, PRAGMAs, schema, sessions
        └── tests/        5 unit files · 2 integration files · 127 tests
```

Dependency order, acyclic (Rule 1 §1):

```
models -> errors -> parsing  \
tables                        >-- engine
config                       /
```

**PRAGMAs hang off the `connect` event, not off startup.** SQLite applies
`foreign_keys` and `busy_timeout` *per connection*, and the pool opens new ones
as it grows. Setting them once at startup gives you cascades that work until the
pool grows a second connection. `journal_mode` is skipped for an in-memory
database, where SQLite ignores it anyway.

**An in-memory database gets a `StaticPool`.** With the default pool each
connection gets its own private, empty database and nothing written through one
session is visible to the next — a failure that looks exactly like data loss.

**`create_all` plus a version row, no Alembic.** `connect()` creates the tables
if they are absent (idempotent, so it also heals a half-created file) and then
refuses to run against a file whose `schema_version` is not this build's. The
mismatch raises *inside* the transaction, so a file this build cannot use is
left exactly as it was found rather than half-migrated. Failing once at startup
with both numbers in hand beats a confusing `no such column` three calls later.

**Constraint violations are their own error.** `ConstraintViolated` carries a
`kind` — `unique`, `foreign key`, `check`, `not null` — because it is the one
failure on the write path a caller routinely *handles* rather than propagates:
recording navigation means inserting screens that have very often been seen
before, and `uq_screen_identity` rejecting a duplicate is the schema doing its
job, not a fault to report.

## How the map fills itself

The load-bearing decision. A screen is identified by its page source, and a page
source is 100–200KB of XML — so it cannot travel through a model's context on
every tap. There are only two places it can come from, and `appium_module`
already has it.

`appium_module` itself carries zero knowledge that any of this exists. It
exposes a generic composition hook — `PortDecorator` in its own
`application/di.py` — that lets a caller substitute its session and gesture
ports before its service is built. `appium_module` calls the hook, forgets what
it returned, and never learns who supplied it or why. **This module** is the
one caller that fills it in, entirely from the process-wide composition root:

```
appium_module                        navigation_memory_module
─────────────                        ─────────────────────────
application/di.py                    infrastructure/appium_recorder/
  build_appium_device_service(              RecordingDeviceDriver
    decorate: PortDecorator | None    ◀───wraps sessions + interaction
  )                                         │ translates into
                                            ▼
                                          RouteRecorder ──▶ NavigationStore

application/services/
  AppiumDeviceService just drives the
  device; it never journals anything
```

`src/app_automating/server.py`, the process-wide composition root, is what actually connects
the two: it asks this module's `di.py` for a decorator built around this
module's own `RouteRecorder`, and hands that straight to `appium_module`'s own
`appium_device_service(decorate=...)`.

Three properties hold in `RecordingDeviceDriver`, and all three matter:

* **Recording never changes an outcome.** A gesture that reached the device
  succeeded whether or not it was written down, so every failure in the
  recording path is swallowed. A real tap reported as failed because a database
  was locked would be strictly worse than recording nothing.
* **Failed gestures are recorded too**, with `ok=False`. "Tapping this here does
  nothing" is knowledge; a memory that only remembers successes cannot warn
  anyone off a dead end.
* **Typed text is never recorded.** It is routinely a password or a card
  number, and the map needs to know a field was filled, not with what.

`AT_NAVIGATION_MEMORY_RECORD_INTERACTIONS=false` unhooks the whole thing at the
composition root: `navigation_memory_service` yields `decorate=None`, so
`appium_module` is wired with its raw, unwrapped ports and behaves exactly as
if the hook did not exist.

### The coupling, and how narrow it is

One Protocol and appium's own request/result dataclasses, unmodified — no new
DTOs cross the boundary. Exactly one file,
`infrastructure/appium_recorder/driver.py`, imports `appium_module.domain` —
that module's innermost, framework-free ring — and nothing else from it, not
even `application/di.py`, which constructs `RecordingDeviceDriver` (a class of
this module's own) without ever needing to name appium's Protocols to do so.
`appium_module` imports nothing from here at all; it names this module only in
a docstring saying who fills the hook today. Tests in
`tests/unit/test_architecture.py` hold that line: only the recording adapter
may name the other module, it may touch only that module's `domain/`, and the
arrow may never point back.

## Two business rules that live in the domain

`domain/` has four files rather than three, and the extra two are the reason:
they are *rules about navigation*, not storage details, and a second backend
must not be able to answer them differently.

**`fingerprinting.py` — what makes two observations the same place.** The sorted
`resource-id`s of the visible elements, hashed. Text is excluded, so
`Welcome, Sam` and `Welcome, Alex` are one screen and a cart badge counting up
does not mint a new one. When an app exposes *no* ids — Flutter, some React
Native — it falls back to the shape of the hierarchy, because hashing ids alone
would give every screen in such an app the same fingerprint and silently
collapse its whole map to a single node.

**`routing.py` — which known route is the better one to attempt.** A widest-path
search, not a shortest-path one: a route is scored by its **weakest** hop, so two
steps that work nine times in ten beat one step that worked once. A plan is only
useful if it survives being executed. Confidence is Laplace-smoothed, so 1/1
reads as 0.67 and 40/40 as 0.98 — the ordering is the whole point.

## Where the layers sit

```
navigation_memory_module/
├── domain/            models · errors · ports · fingerprinting · routing
├── infrastructure/
│   ├── navigation_store/    the database, the schema, and the store
│   └── appium_recorder/     the one place two modules meet
├── application/       NavigationMemoryService + di.py
└── presentation/      the 5 tools + register_navigation_tools
```

Three ports, and the split is what makes the read tools safe: `RouteMemory`
(reads), `RouteRecorder` (writes, handed only to `RecordingDeviceDriver`),
`MemoryMaintenance` (deletes). The MCP tools hold the first and the last, so
they are structurally incapable of corrupting the map they read; the recorder
holds only the second, so a bug there cannot wipe anything -- and `appium_module`
holds none of these three at all, because it never touches this module.

## Still open

**Schema migrations.** `create_all` plus a version row, by design — a mismatched
file is refused at startup with both numbers rather than failing later with a
confusing `no such column`. When the schema next changes, that becomes a real
decision: add Alembic, or accept re-learning.

**App versions.** `transition.last_app_version` records which build a route was
last confirmed on, and nothing yet *uses* it. The hook is there for the day a
redesign should make old routes rank lower instead of being forgotten wholesale.
