"""The Appium module: driving any attached Android device through Appium.

A vertical slice of the product with the same four layers as every other module
(Rule 0 §1). What it adds is *control*: the Android module creates and boots
devices, and this one drives whatever is attached -- an emulator or a physical
phone, identically, because at this level both are an adb serial.

The module owns the Appium toolchain end to end. It reports what the host has,
installs a missing driver on request, launches and supervises the Appium server
itself, and closes every session it opened when the MCP server shuts down.
"""
