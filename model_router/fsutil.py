"""File helpers shared by every module that writes state."""

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write text through a temp file in the same directory, owner-only."""
    # Imported here because most hook runs never write state, and tempfile
    # is one of the slower standard-library imports.
    import tempfile

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + destination.name + ".",
        dir=str(destination.parent),
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(destination))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
