"""Install the PRIME UEL Studio entry into Abaqus/CAE."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


PLUGIN_FILES = (
    "prime_uel_plugin.py",
    "prime_uel_plugin_gui.py",
    "prime_uel_plugin_kernel.py",
    "prime_uel_plugin_help.html",
)


def install(destination: Path, launcher: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "abaqus_plugin"
    for name in PLUGIN_FILES:
        shutil.copy2(source / name, destination / name)
    config = {
        "launcher": str(launcher.resolve()),
        "python_launcher": "py",
        "studio_root": str(launcher.parent.resolve()),
    }
    (destination / "prime_uel_plugin_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=Path.home() / "abaqus_plugins")
    parser.add_argument(
        "--launcher",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "launcher.pyw",
    )
    args = parser.parse_args()
    print(install(args.destination, args.launcher))


if __name__ == "__main__":
    main()
