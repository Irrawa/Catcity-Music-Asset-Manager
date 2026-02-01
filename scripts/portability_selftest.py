"""Self-test for portability / cloud-sync scenario.

Run:
  python scripts/portability_selftest.py

This script simulates the scenario described in the chat:
- Device A: music_path_A, data_path_A/data1.json
- Device B: music_path_B, data_path_B/data1.json
- Device A updates data1.json and pushes to cloud
- Device B pulls the updated JSON and uses its local music_path_B

Expected:
- The app loads the pulled JSON (latest) instead of using an older cached state.
- The catalog updates raw_music_directory to the currently selected RawMusicDirectory.
- No "brand new" catalog is created unless the JSON file did not exist.

Note: This is a lightweight unit-style test using dummy audio files
(with audio extensions). Mutagen length detection will return None for them,
which is acceptable.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
import sys

# Ensure project root is on sys.path when running this script directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalog import load_or_create_catalog, save_catalog_atomic


def _write_dummy_audio(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"dummy audio bytes")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Simulate device A
        music_a = root / "deviceA" / "music_path_A"
        data_a = root / "deviceA" / "data_path_A"
        cat_a = data_a / "data1.json"

        _write_dummy_audio(music_a / "Pack1" / "track_01.mp3")
        _write_dummy_audio(music_a / "Pack2" / "nested" / "track_02.ogg")

        catalog_a = load_or_create_catalog(cat_a, music_a)
        assert len(catalog_a.tracks) == 2, "Device A catalog should have 2 tracks"

        # Simulate device B with an older local file (0.9) that has a different track set
        music_b = root / "deviceB" / "music_path_B"
        data_b = root / "deviceB" / "data_path_B"
        cat_b = data_b / "data1.json"

        _write_dummy_audio(music_b / "Pack1" / "track_01.mp3")
        _write_dummy_audio(music_b / "Pack2" / "nested" / "track_02.ogg")

        # Create an older catalog on B (only 1 track) to mimic "version 0.9"
        _write_dummy_audio(music_b / "Old" / "old_only.wav")
        old_catalog_b = load_or_create_catalog(cat_b, music_b)
        # Rescan to pick up the old-only track; load_or_create already scanned.
        assert len(old_catalog_b.tracks) >= 1

        # Now "cloud sync": overwrite device B's data1.json with the newer file from device A
        cat_b.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cat_a, cat_b)

        # Device B selects its local music_path_B + the pulled data1.json.
        # The app must load the pulled file (2 tracks), not the older local cached structure.
        catalog_b = load_or_create_catalog(cat_b, music_b)
        assert len(catalog_b.tracks) == 2, "Device B should load the pulled (latest) JSON with 2 tracks"

        # And it must update raw_music_directory to the selected music_path_B
        assert Path(catalog_b.raw_music_directory).resolve() == music_b.resolve(), "raw_music_directory should update to device B path"

        # Ensure it did not create a brand new file (i.e., didn't wipe tracks)
        on_disk = json.loads(cat_b.read_text(encoding="utf-8"))
        assert "tracks" in on_disk and len(on_disk["tracks"]) == 2

        print("OK: portability scenario passed")


if __name__ == "__main__":
    main()
