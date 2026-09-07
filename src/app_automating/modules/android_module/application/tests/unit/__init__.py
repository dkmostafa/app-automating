"""Unit tests for the wiring, with mocks.

The application layer is the one place mocking is not only allowed but required
(Rule 2). Its job is to connect objects, so what needs asserting is *which
object was handed to which constructor* -- and building the real collaborators
would drag a real Android SDK into a test that is not about the SDK at all.

Contrast Rule 1 §6: the infrastructure layer may not mock, because there what
needs asserting is that the real tools behave as expected.
"""
