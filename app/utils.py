from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Iterable, Optional, Tuple

from mutagen import File as MutagenFile


AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav", ".flac", ".m4a", ".aac"}


def iter_audio_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            yield path


def sha1_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def audio_length_seconds(path: Path) -> Optional[float]:
    try:
        mf = MutagenFile(str(path))
        if mf is None or not hasattr(mf, "info") or mf.info is None:
            return None
        length = getattr(mf.info, "length", None)
        if length is None:
            return None
        return float(length)
    except Exception:
        return None


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def is_subpath(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def relpath_posix(path: Path, root: Path) -> str:
    rel = path.resolve().relative_to(root.resolve())
    return rel.as_posix()


def safe_join(root: Path, rel_posix: str) -> Path:
    # rel_posix is stored with forward slashes
    p = Path(root, *rel_posix.split("/"))
    rp = p.resolve()
    if not is_subpath(rp, root):
        raise ValueError("Invalid path outside root")
    return rp

