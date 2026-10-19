"""Refresh console launchers after copying a Windows virtual environment."""
from importlib.metadata import distributions
from pathlib import Path
import sys

from pip._vendor.distlib.scripts import ScriptMaker


def main() -> None:
    maker = ScriptMaker(None, str(Path(sys.prefix) / "Scripts"))
    maker.executable = sys.executable
    maker.clobber = True
    maker.variants = {""}
    count = 0
    for distribution in distributions():
        for entry in distribution.entry_points:
            if entry.group == "console_scripts":
                maker.make(f"{entry.name} = {entry.value}")
                count += 1
    print(f"Refreshed {count} console launchers in {sys.prefix}")


if __name__ == "__main__":
    main()
