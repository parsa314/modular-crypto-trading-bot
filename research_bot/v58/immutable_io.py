"""Publish complete, immutable evidence directories with an atomic rename."""
from __future__ import annotations

import errno
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile


def _matches(output: Path, files: dict[str, bytes]) -> bool:
    if output.is_symlink() or not output.is_dir():
        return False
    found = {}
    for path in output.rglob("*"):
        if path.is_symlink():
            return False
        if path.is_file():
            found[path.relative_to(output).as_posix()] = path.read_bytes()
    return found == files


def write_immutable_bundle(output: Path, files: dict[str, bytes]) -> None:
    """Publish all files together, or verify an identical existing bundle.

    An empty caller-created output directory is supported. A partial or changed
    existing bundle is rejected without changing any of its bytes. Staging is
    on the same filesystem so readers cannot see a partially published run.
    """
    output = Path(os.path.abspath(output))
    if not files:
        raise ValueError("evidence bundle must not be empty")
    for name, content in files.items():
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or ".." in path.parts
                or path.as_posix() != name or "\\" in name
                or not isinstance(content, bytes)):
            raise ValueError("bundle requires safe relative paths and bytes")
    if output.is_symlink():
        raise ValueError("evidence output must not be a symlink")
    if output.exists() and os.path.samefile(output, Path.cwd()):
        raise ValueError("use a named output directory outside the current working directory")
    if output.exists():
        if _matches(output, files):
            return
        if not output.is_dir() or any(output.iterdir()):
            raise ValueError(f"immutable output differs: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for name, content in sorted(files.items()):
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            os.rename(staging, output)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            if not _matches(output, files):
                raise ValueError(f"immutable output differs: {output}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)
