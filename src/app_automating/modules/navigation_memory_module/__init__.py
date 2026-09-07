"""The navigation-memory module: what the device did, remembered across runs.

A vertical slice of the product with the same four layers as every other module
(Rule 0 §1). What it adds is *memory*. The Android module creates devices and
the Appium module drives them, but neither remembers anything: every run starts
from a blank screen with no idea how it reached the last one. This module writes
down each route as it is taken, so the next run can look it up instead of
rediscovering it by trial and error.

It records two things at once, and the distinction is the whole design:

* a **journal** -- the honest, append-only sequence of steps actually taken, one
  row per action, grouped into runs. This is what happened.
* a **map** -- deduplicated screens and the transitions between them, with
  traversal and success counts folded in. This is what was learned.

The journal is what you read to debug a run. The map is what a model reads to
plan one: "from the screen I am on, which action has previously reached the
checkout, and how often did it work".
"""
