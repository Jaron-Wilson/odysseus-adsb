"""python -m adsb_mod                      run the mod (Odysseus starts it this way)
python -m adsb_mod import FILE --into SETTINGS
                                          copy the receiver address from the core's old
                                          adsb.json (or an export) into the mod's settings.json
python -m adsb_mod export SETTINGS        print the settings as an export file

`import` and `export` only read and write the files named, so they work with
Odysseus stopped or running (the core reads the mod's settings.json on every
request). Under Odysseus the settings file is <data dir>/mods/adsb/settings.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def cli(argv) -> int:
    from adsb_mod import settings as cfg
    p = argparse.ArgumentParser(prog="python -m adsb_mod")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="import the core's old adsb.json or an export")
    imp.add_argument("file", help="adsb.json from the Odysseus data folder, or an export from this mod")
    imp.add_argument("--into", required=True, help="the mod's settings.json (<data dir>/mods/adsb/settings.json)")
    imp.add_argument("--dry-run", action="store_true", help="print what would be saved, change nothing")
    exp = sub.add_parser("export", help="print the mod's settings as an export")
    exp.add_argument("settings", help="the mod's settings.json")
    a = p.parse_args(argv)
    if a.cmd == "export":
        try:
            s = cfg.clean(_read_json(a.settings))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(json.dumps(cfg.export(s), indent=2))
        return 0
    try:
        with open(a.file, "r", encoding="utf-8") as f:
            values = cfg.parse_import(f.read())
        current = _read_json(a.into)
        try:
            merged = cfg.clean(current)
        except ValueError:
            merged = dict(cfg.DEFAULTS)
        merged.update(values)
        merged["imported_from"] = os.path.basename(a.file)[:200]
        merged = cfg.clean(merged)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.dry_run:
        print(json.dumps(merged, indent=2))
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(a.into)), exist_ok=True)
    tmp = a.into + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, a.into)
    print(f"Saved {merged['url'] or '(no address)'} to {a.into}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    from adsb_mod.app import main
    main()
