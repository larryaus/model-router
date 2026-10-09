"""Append-only log of routing decisions, one JSON object per line.

Records never contain prompt text or the launch description.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_MAX_BYTES = 5 * 1024 * 1024


def append_record(
    path: Path, record: Dict[str, Any], max_bytes: int = DEFAULT_MAX_BYTES
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if destination.stat().st_size >= max_bytes:
            os.replace(str(destination), str(destination) + ".1")
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


def read_recent(path: Path, count: int) -> List[Dict[str, Any]]:
    if count <= 0:
        return []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    records: List[Dict[str, Any]] = []
    for line in lines[-count:]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records
