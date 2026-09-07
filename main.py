"""Run the server straight from a checkout: ``uv run main.py``.

The installed package exposes the same thing as a console script
(``app-automating``, declared in ``pyproject.toml``), and that is what an MCP
client should launch. This file exists so that a clone can be started without
installing anything first, and it does nothing the console script does not.
"""

from app_automating.server import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
