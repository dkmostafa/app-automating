"""The navigation-memory module's infrastructure: the adapters that persist.

One package per component (Rule 1 §1), each named after the thing it exposes and
split by kind inside. Today there is one: ``navigation_store``, which owns the
SQLite database -- its location, its connection pragmas, its schema, and the
sessions every read and write runs inside.

SQLAlchemy lives here and only here. The layers above reason in plain Python
objects and must never learn that navigation memory is a file on disk, let alone
which columns it has.
"""
