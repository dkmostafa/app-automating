"""The AVD home, as the Android tools lay it out on disk.

The component's second contact with the operating system, and the counterpart to
:mod:`process`: that module owns the process table, this one owns a directory.
Splitting it out of the manager is Rule 0 §3 (SRP) -- "avdmanager changed where
it writes an AVD" and "the emulator changed how it reports a boot" are two
unrelated reasons to edit a file, so they are two files.

Like :mod:`process`, it depends on nothing else in the package beyond
:mod:`errors`, and knows nothing about starting or stopping anything.
"""

from __future__ import annotations

from pathlib import Path

from .errors import AvdNotFoundError

__all__ = ["AvdStore"]


class AvdStore:
    """Reads the AVD home. Never writes it -- avdmanager owns that."""

    def __init__(self, avd_home: Path) -> None:
        self._home = avd_home

    @property
    def home(self) -> Path:
        return self._home

    def ini_path(self, name: str) -> Path:
        """The ``.ini`` file, which is what avdmanager treats as the AVD's identity."""
        return self._home / f"{name}.ini"

    def exists(self, name: str) -> bool:
        return self.ini_path(name).exists()

    def require(self, name: str) -> None:
        """Raise :class:`AvdNotFoundError` unless the AVD is on disk."""
        if not self.exists(name):
            raise AvdNotFoundError(name, self._home)

    def names(self) -> tuple[str, ...]:
        """Every AVD on disk. The ``.ini`` stems are the names, so this needs no
        subprocess and works even when avdmanager cannot load one of them."""
        return tuple(sorted(path.stem for path in self._home.glob("*.ini")))

    def directory(self, name: str) -> Path:
        """The ``.avd`` payload directory, read from the ``.ini`` when possible.

        A renamed AVD keeps its original directory name, so ``<name>.avd`` is a
        guess and ``path=`` in the ``.ini`` is the truth.
        """
        try:
            text = self.ini_path(name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return self._home / f"{name}.avd"
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "path" and value.strip():
                return Path(value.strip())
        return self._home / f"{name}.avd"
