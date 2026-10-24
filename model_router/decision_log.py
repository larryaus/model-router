"""Append-only log of routing decisions, one JSON object per line.

Records never contain prompt text or the launch description.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
# Rotated files kept beside the live one, as .1 (newest) to .5 (oldest).
DEFAULT_KEEP = 5


def _rotated(path: Path, index: int) -> Path:
    return Path("%s.%d" % (path, index))


def _rotate(destination: Path, max_bytes: int, keep: int) -> None:
    # Imported here because a rotation is rare and most hook runs never need it.
    import fcntl

    with open(str(destination) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Another process may have rotated while this one waited. Rotating
        # again would push a full file one step closer to deletion.
        try:
            if destination.stat().st_size < max_bytes:
                return
        except FileNotFoundError:
            return
        for index in range(keep - 1, 0, -1):
            older = _rotated(destination, index + 1)
            try:
                os.replace(str(_rotated(destination, index)), str(older))
            except FileNotFoundError:
                pass
        os.replace(str(destination), str(_rotated(destination, 1)))


def append_record(
    path: Path,
    record: Dict[str, Any],
    max_bytes: int = DEFAULT_MAX_BYTES,
    keep: int = DEFAULT_KEEP,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if destination.stat().st_size >= max_bytes:
            _rotate(destination, max_bytes, keep)
    except FileNotFoundError:
        pass
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(
        str(destination), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600
    )
    try:
        # One write per record keeps concurrent sessions from interleaving.
        os.write(descriptor, line.encode("utf-8"))
    finally:
        os.close(descriptor)


def _parse(lines: List[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _lines(path: Path) -> List[str]:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return handle.readlines()
    except OSError:
        return []


def read_recent(path: Path, count: int) -> List[Dict[str, Any]]:
    """The last `count` records of the live file. Rotated files are not read."""
    if count <= 0:
        return []
    return _parse(_lines(path)[-count:])


def read_all(path: Path, keep: int = DEFAULT_KEEP) -> List[Dict[str, Any]]:
    """Every record still on disk, rotated files included, oldest first."""
    files = [_rotated(Path(path), index) for index in range(keep, 0, -1)] + [Path(path)]
    return [record for name in files for record in _parse(_lines(name))]
