"""Unit tests for the component's pure app_automating.modules.

Only ``models``, ``errors``, ``parsing`` and the pure half of ``config`` are
testable this way, and Rule 1 §6 still applies: **no mocking, patching or
stubbing**. These are unit tests because the code under test is genuinely pure,
not because its collaborators were replaced. Anything that needs adb, an AVD or
a child process belongs in ``../integration/``.
"""
