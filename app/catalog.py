from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID, uuid4

from .models import Catalog, Cluster, LicensingInfo, LoopInfo, ScaleDef, Track, TrackFingerprint, Vocab
from .utils import audio_length_seconds, iter_audio_files, relpath_posix, safe_join, sha1_file


@dataclass
class ScanSummary:
    new: int = 0
    updated: int = 0
    relinked: int = 0
    missing: int = 0
    duplicates: int = 0


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# Default role vocabulary. These are kept as a runtime default for convenience,
# but can be omitted from the on-disk JSON when unchanged to reduce metadata noise.
DEFAULT_PRIMARY_ROLES: List[str] = [
    "menu",
    "exploration",
    "town",
    "combat",
    "boss",
    "cutscene",
    "ending",
    "ambient",
]


def default_vocab() -> Vocab:
    return Vocab(
        primary_roles=list(DEFAULT_PRIMARY_ROLES),
        tag_vocab={
            # NOTE: "unknown" for VK components is represented by selecting nothing
            # (empty list) in track.tags and "-" in the Virtual Key selector UI.
            # We intentionally do NOT include an explicit "unk" option in vocab.
            "moods": [
                "calm",
                "cozy",
                "playful",
                "lighthearted",
                "hopeful",
                "romantic",
                "mysterious",
                "suspenseful",
                "tense",
                "eerie",
                "ominous",
                "melancholic",
                "heroic",
                "triumphant",
                "epic",
            ],
            "styles": [
                "cinematic",
                "orchestral",
                "ambient",
                "minimalist",
                "electronic",
                "synthwave",
                "chiptune",
                "lofi",
                "rock",
                "jazz",
                "folk_acoustic",
                "world",
                "industrial",
                "horror",
            ],
            "usable_in_contexts": [
                "menu",
                "loading",
                "exploration",
                "hub",
                "town",
                "puzzle",
                "stealth",
                "combat",
                "boss",
                "cutscene",
                "game_over",
                "ending",
                "credits",
            ],
            "instruments": [
                "orchestral",
                "piano",
                "strings",
                "guitar",
                "bass",
                "percussion",
                "synth",
                "choir",
                "ethnic",
            ],
            # Setting tags are *not* used in the Virtual Key combination.
            "for_game_settings": [
                "fantasy",
                "sci-fi",
                "modern",
                "historical",
                "post-apocalyptic",
                "cyberpunk",
                "steampunk",
            ],
        },
        scale_defs={
            "energy": ScaleDef(min=0, max=5, default=0),
            "details": ScaleDef(min=0, max=5, default=0),
            "tension": ScaleDef(min=0, max=5, default=0),
            "diversity": ScaleDef(min=0, max=5, default=0),
            "users_liking": ScaleDef(min=0, max=5, default=0),
        },
        # scale_names is derived from scale_defs (ordering hint) and may be omitted on disk.
        scale_names=["energy", "details", "tension", "diversity", "users_liking"],
    )


def _ensure_vocab_scales(vocab: Vocab) -> None:
    """Backfill scale_defs / scale_names for older catalogs."""
    if not vocab.scale_defs:
        # Upgrade from legacy scale_names
        for name in (vocab.scale_names or []):
            if name and name not in vocab.scale_defs:
                vocab.scale_defs[name] = ScaleDef(min=0, max=5, default=0)
    if not vocab.scale_names:
        vocab.scale_names = list(vocab.scale_defs.keys())


def _strip_vocab_unknown_options(vocab: Vocab) -> None:
    """Remove explicit unknown options (e.g. 'unk') from VK vocab groups.

    In this version, "unknown" is represented by selecting nothing.
    We keep this as a *non-destructive* cleanup:
    - Removes 'unk'/'unknown' tokens if present.
    - Preserves ordering for the remaining items.
    """
    if not getattr(vocab, 'tag_vocab', None):
        vocab.tag_vocab = {}

    for group in ("moods", "usable_in_contexts", "instruments", "styles"):
        vals = vocab.tag_vocab.get(group)
        if vals is None:
            continue
        cleaned: List[str] = []
        seen: Set[str] = set()
        for v in vals:
            s = str(v).strip()
            if not s:
                continue
            low = s.lower()
            if low in ("unk", "unknown", "none", "-"):
                continue
            if low in seen:
                continue
            seen.add(low)
            cleaned.append(s)
        vocab.tag_vocab[group] = cleaned


def ensure_catalog_clusters(catalog: Catalog) -> None:
    """Ensure catalog has clusters and every track has a cluster_id.

    - Catalog schema_version 2 introduces clusters.
    - For backward compatibility, older catalogs may have no clusters and tracks may miss cluster_id.

    Strategy (safe default):
    - Create one cluster per track (cluster name = track.virtual_key).
    - If a track already has cluster_id, ensure the cluster exists.
    """

    # Backfill catalog.clusters field for older JSON files.
    if getattr(catalog, 'clusters', None) is None:
        catalog.clusters = []

    cluster_by_id = {c.cluster_id: c for c in (catalog.clusters or [])}

    # Ensure all tracks have a cluster_id and the cluster exists.
    for t in catalog.tracks:
        if getattr(t, 'cluster_id', None) is not None and t.cluster_id in cluster_by_id:
            continue

        # If track has a cluster_id but the cluster is missing, create the cluster shell.
        if getattr(t, 'cluster_id', None) is not None and t.cluster_id not in cluster_by_id:
            c = Cluster(cluster_id=t.cluster_id, name=t.virtual_key or 'cluster', created_at=utc_now_iso())
            catalog.clusters.append(c)
            cluster_by_id[c.cluster_id] = c
            continue

        # Otherwise create a new cluster for this track.
        cid = uuid4()
        t.cluster_id = cid
        c = Cluster(cluster_id=cid, name=t.virtual_key or 'cluster', created_at=utc_now_iso())
        catalog.clusters.append(c)
        cluster_by_id[cid] = c

    # Upgrade schema version if needed
    if getattr(catalog, 'schema_version', 1) < 2:
        catalog.schema_version = 2



def load_or_create_catalog(catalog_path: Path, raw_music_dir: Path) -> Catalog:
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    if catalog_path.exists():
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog = Catalog.model_validate(data)
        # If the user relocated the raw music folder (e.g., moved the warehouse
        # directory, or using the same catalog on a different device where the
        # raw files live under a different absolute path), we must treat the
        # newly selected RawMusicDirectory as the source of truth.
        #
        # Tracks store `original_path` relative to RawMusicDirectory, so
        # updating the catalog's stored `raw_music_directory` allows the UI to
        # keep showing all tracks and enables playback without requiring a
        # rescan.
        desired_raw_dir = str(raw_music_dir.resolve())
        needs_save = False
        # Older catalogs may have a missing/empty vocab. Do NOT treat an empty
        # primary_roles list as "missing vocab" because we may intentionally
        # omit primary_roles from disk for cleanliness.
        if catalog.vocab is None or (
            not getattr(catalog.vocab, "tag_vocab", None)
            and not getattr(catalog.vocab, "scale_defs", None)
            and not getattr(catalog.vocab, "scale_names", None)
        ):
            catalog.vocab = default_vocab()
        # Ensure role vocab is available at runtime (even if omitted on disk).
        if not catalog.vocab.primary_roles:
            catalog.vocab.primary_roles = list(DEFAULT_PRIMARY_ROLES)
        _ensure_vocab_scales(catalog.vocab)
        _strip_vocab_unknown_options(catalog.vocab)
        if catalog.raw_music_directory != desired_raw_dir:
            catalog.raw_music_directory = desired_raw_dir
            needs_save = True
        # normalize / backfill per-track fields
        for t in catalog.tracks:
            _ensure_track_path_hints(t)
            ensure_track_tags(t, catalog.vocab.tag_vocab)
            ensure_track_scales(t, catalog.vocab.scale_defs)
        ensure_catalog_clusters(catalog)
        # Persist the raw directory update immediately so subsequent launches
        # (or other tooling) won't be stuck with a stale raw_music_directory.
        if needs_save:
            save_catalog_atomic(catalog, catalog_path)
        return catalog

    # New catalog file: create with default schema, then immediately populate by scanning
    # the raw music directory so the library is usable on first open.
    catalog = Catalog(
        schema_version=2,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        raw_music_directory=str(raw_music_dir),
        vocab=default_vocab(),
        aliases={},
        tracks=[],
    )
    # Populate immediately with default metadata.
    # For a brand-new catalog, initialize virtual_key from the raw filename
    # (with _0/_1 suffixes on collisions) so teams can recognize tracks
    # before they re-key them using the Virtual Key Builder.
    scan_and_sync(raw_music_dir, catalog, init_virtual_key_from_filename=True)
    save_catalog_atomic(catalog, catalog_path)
    return catalog


def save_catalog_atomic(catalog: Catalog, catalog_path: Path) -> None:
    catalog.updated_at = utc_now_iso()
    tmp_path = catalog_path.with_suffix(catalog_path.suffix + ".tmp")
    payload = catalog.model_dump(mode="json")
    payload = _prune_redundant_catalog_fields(payload)
    # Use json.dumps to keep compatibility with pydantic versions where
    # model_dump_json does not expose ensure_ascii.
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(catalog_path)


def _prune_redundant_catalog_fields(payload: Dict[str, object]) -> Dict[str, object]:
    """Remove redundant/default fields from the on-disk JSON.

    This keeps the file cleaner while preserving full behavior:
    missing fields are always safe because we load through Pydantic models.
    """
    if not isinstance(payload, dict):
        return payload

    # Drop empty aliases dict.
    if payload.get("aliases") == {}:
        payload.pop("aliases", None)

    vocab = payload.get("vocab")
    if isinstance(vocab, dict):
        # primary_roles: omit if unchanged default (or empty)
        pr = vocab.get("primary_roles")
        if pr == DEFAULT_PRIMARY_ROLES or pr == []:
            vocab.pop("primary_roles", None)

        # scale_names duplicates scale_defs keys; omit if redundant/empty.
        sd = vocab.get("scale_defs")
        sn = vocab.get("scale_names")
        if isinstance(sn, list):
            if not sn:
                vocab.pop("scale_names", None)
            elif isinstance(sd, dict):
                if sn == list(sd.keys()):
                    vocab.pop("scale_names", None)

    return payload


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "track"


def generate_virtual_key(stem: str, existing: Set[str]) -> str:
    base = _slugify(stem)
    key = base
    i = 1
    while key in existing:
        i += 1
        key = f"{base}_{i}"
    return key


def generate_virtual_key_from_filename(filename_stem: str, existing: Set[str]) -> str:
    """Generate a unique virtual_key based on the raw file name.

    Used only when creating a brand-new catalog file.

    - First choice: the raw file's stem (no extension), lightly sanitized.
    - If it clashes, append _0, _1, _2... until unique.

    We keep this filename-derived key even though the app can later
    regenerate a patterned key via the Virtual Key Builder.
    """

    base = str(filename_stem or "").strip()
    # Light sanitization: keep unicode word characters, digits, underscore and hyphen.
    # Replace other characters with underscores.
    base = re.sub(r"[^\w\-]+", "_", base, flags=re.UNICODE).strip("_")
    base = base or "track"

    key = base
    if key not in existing:
        return key

    i = 0
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def _slugify_part(s: str) -> str:
    """Slugify one component used in virtual_key.

    We use '-' inside components so '_' can be reserved as the component separator.
    """
    s = str(s or '').lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unk"


def generate_virtual_key_from_parts(mood: str, context: str, instrument: str, style: str, existing: Set[str]) -> str:
    prefix = f"{_slugify_part(mood)}_{_slugify_part(context)}_{_slugify_part(instrument)}_{_slugify_part(style)}_"
    used = set()
    for k in existing:
        if not isinstance(k, str):
            continue
        if not k.startswith(prefix):
            continue
        tail = k[len(prefix):]
        if tail.isdigit():
            used.add(int(tail))
    sn = 1
    while sn in used:
        sn += 1
    return f"{prefix}{sn:03d}"


def default_vk_components(tag_vocab: Dict[str, List[str]]) -> Dict[str, str]:
    """Pick default components for virtual_key from vocab.

    Default should be an explicit unknown option ("unk") instead of the first
    business-specific option, to reduce bias and team disagreement.
    """

    def pick_unknown_first(group: str, fallback: str = "unk") -> str:
        vals = [str(v) for v in (tag_vocab.get(group) or [])]
        if "unk" in vals:
            return "unk"
        if "unknown" in vals:
            return "unknown"
        return fallback

    return {
        'moods': pick_unknown_first('moods'),
        'usable_in_contexts': pick_unknown_first('usable_in_contexts'),
        'instruments': pick_unknown_first('instruments'),
        'styles': pick_unknown_first('styles'),
    }


def _path_hints_from_relpath(rel_path: str) -> Tuple[str, str]:
    """Return (filename, parent_dir_name) from a stored relative path."""
    try:
        p = Path(rel_path or "")
        fname = p.name
        parent = p.parent.name if str(p.parent) not in (".", "") else ""
        return fname, parent
    except Exception:
        return "", ""


def _ensure_track_path_hints(track: Track) -> None:
    """Backfill Track.raw_file_name/raw_parent_dir_name from original_path."""
    fname, parent = _path_hints_from_relpath(track.original_path)
    if not getattr(track, "raw_file_name", ""):
        track.raw_file_name = fname
    if not getattr(track, "raw_parent_dir_name", ""):
        track.raw_parent_dir_name = parent


def ensure_track_scales(track: Track, scale_defs: Dict[str, ScaleDef]) -> None:
    # Ensure track has all known scales, clamp to per-scale ranges.
    for name, sdef in (scale_defs or {}).items():
        v = track.scales.get(name, sdef.default)
        try:
            iv = int(v)
        except Exception:
            iv = int(sdef.default)

        mn = int(sdef.min)
        mx = int(sdef.max)
        if mx < mn:
            mn, mx = mx, mn
        iv = max(mn, min(mx, iv))
        track.scales[name] = iv


def ensure_track_tags(track: Track, tag_vocab: Dict[str, List[str]]) -> None:
    for group in tag_vocab.keys():
        if group not in track.tags:
            track.tags[group] = []

    # Unknown for VK component tag groups is represented by selecting nothing.
    # (Empty list in track.tags; "-" in the Virtual Key selector UI.)


def normalize_tag_value(value: str, aliases: Dict[str, str]) -> str:
    key = value.strip()
    if not key:
        return ""
    low = key.lower()
    return aliases.get(low, key)


def normalize_track_tags(track: Track, vocab: Vocab, aliases: Dict[str, str]) -> None:
    for group, values in list(track.tags.items()):
        if group not in vocab.tag_vocab:
            continue
        normalized: List[str] = []
        seen: Set[str] = set()
        for v in values:
            nv = normalize_tag_value(str(v), aliases)
            if not nv:
                continue
            # Treat explicit unknown tokens as "no selection" for VK groups.
            if group in ("moods", "usable_in_contexts", "instruments", "styles"):
                if nv.lower() in ("unk", "unknown", "none", "-"):
                    continue
            if nv.lower() in seen:
                continue
            seen.add(nv.lower())
            normalized.append(nv)
        track.tags[group] = normalized


def _file_exists(raw_dir: Path, rel_path: str) -> bool:
    try:
        p = safe_join(raw_dir, rel_path)
        return p.exists()
    except Exception:
        return False


def scan_and_sync(raw_music_dir: Path, catalog: Catalog, *, init_virtual_key_from_filename: bool = False) -> ScanSummary:
    raw_music_dir = raw_music_dir.resolve()

    # Ensure clusters exist (backward compatible)
    ensure_catalog_clusters(catalog)

    # Build quick lookup maps
    track_by_id: Dict[UUID, Track] = {t.track_id: t for t in catalog.tracks}
    track_by_relpath: Dict[str, Track] = {t.original_path: t for t in catalog.tracks if t.original_path}

    existing_keys: Set[str] = {t.virtual_key for t in catalog.tracks if t.virtual_key}

    # sha1 -> canonical track
    canonical_by_sha1: Dict[str, Track] = {}
    for t in catalog.tracks:
        if t.fingerprint and t.fingerprint.sha1:
            if t.duplicate_of is None and t.fingerprint.sha1 not in canonical_by_sha1:
                canonical_by_sha1[t.fingerprint.sha1] = t

    # Backfill missing per-track fields
    for t in catalog.tracks:
        _ensure_track_path_hints(t)
        ensure_track_tags(t, catalog.vocab.tag_vocab)
        ensure_track_scales(t, catalog.vocab.scale_defs)
        normalize_track_tags(t, catalog.vocab, catalog.aliases)

    # Build a fallback map for relinking when the audio bytes changed.
    # Key: (filename, parent_dir_name) in lowercase.
    missing_by_name_parent: Dict[Tuple[str, str], List[Track]] = {}
    for t in catalog.tracks:
        if not t.original_path:
            continue
        if _file_exists(raw_music_dir, t.original_path):
            continue
        fname = (t.raw_file_name or "").lower().strip()
        parent = (t.raw_parent_dir_name or "").lower().strip()
        if not fname:
            continue
        missing_by_name_parent.setdefault((fname, parent), []).append(t)

    def pick_name_parent_candidate(cands: List[Track]) -> Optional[Track]:
        """Pick a safe candidate for auto-relinking.

        We only auto-relink if the match is unambiguous. Prefer canonical tracks
        (duplicate_of is None).
        """
        if not cands:
            return None
        canon = [t for t in cands if t.duplicate_of is None]
        if len(canon) == 1:
            return canon[0]
        if len(cands) == 1:
            return cands[0]
        return None

    used_name_relinks: Set[UUID] = set()

    # Mark all tracks missing initially; we'll flip to False when we see them
    for t in catalog.tracks:
        t.missing_file = True

    # Collect discovered files (compute sha1 with caching where safe)
    discovered: List[Tuple[str, str, int, float, str, Optional[float]]] = []
    # tuple: (rel_path, sha1, size, mtime, file_format, length_sec)

    for file_path in iter_audio_files(raw_music_dir):
        rel_path = relpath_posix(file_path, raw_music_dir)
        fmt = file_path.suffix.lower().lstrip(".")
        stat = file_path.stat()
        size = int(stat.st_size)
        mtime = float(stat.st_mtime)

        # If we already know this exact relpath and file didn't change, reuse stored sha1.
        sha1 = None
        existing = track_by_relpath.get(rel_path)
        if existing is not None and existing.fingerprint:
            if existing.fingerprint.file_size == size and abs(existing.fingerprint.modified_time - mtime) < 1e-6:
                sha1 = existing.fingerprint.sha1

        if sha1 is None:
            sha1 = sha1_file(file_path)

        length = audio_length_seconds(file_path)
        discovered.append((rel_path, sha1, size, mtime, fmt, length))

    summary = ScanSummary()

    # Reconcile discovered files with catalog
    for rel_path, sha1, size, mtime, fmt, length in discovered:
        # If there's already a track entry for this rel_path, prefer updating it.
        existing_track = track_by_relpath.get(rel_path)

        canonical = canonical_by_sha1.get(sha1)

        if canonical is None:
            # No canonical known. Before creating a brand-new entry, try a
            # name-based relink for missing tracks (filename + parent dir).
            if existing_track is None:
                fname, parent = _path_hints_from_relpath(rel_path)
                key = (fname.lower().strip(), parent.lower().strip())
                cand = pick_name_parent_candidate(missing_by_name_parent.get(key, []))
                if cand is not None and cand.track_id not in used_name_relinks:
                    if not _file_exists(raw_music_dir, cand.original_path):
                        old_sha = cand.fingerprint.sha1 if cand.fingerprint else ""
                        old_path = cand.original_path

                        # Update relpath map
                        if old_path in track_by_relpath:
                            track_by_relpath.pop(old_path, None)

                        cand.original_path = rel_path
                        cand.file_format = fmt
                        cand.fingerprint.sha1 = sha1
                        cand.fingerprint.file_size = size
                        cand.fingerprint.modified_time = mtime
                        cand.raw_file_name = fname
                        cand.raw_parent_dir_name = parent
                        if length is not None:
                            cand.length_sec = length
                        cand.missing_file = False

                        track_by_relpath[rel_path] = cand
                        used_name_relinks.add(cand.track_id)

                        # Update canonical sha1 map if this track is canonical
                        if cand.duplicate_of is None:
                            if old_sha and canonical_by_sha1.get(old_sha) is cand:
                                canonical_by_sha1.pop(old_sha, None)
                            canonical_by_sha1[sha1] = cand

                        summary.relinked += 1
                        continue

            # Otherwise: create new track
            tid = uuid4()
            vk_parts = default_vk_components(catalog.vocab.tag_vocab)
            if init_virtual_key_from_filename:
                vk = generate_virtual_key_from_filename(Path(rel_path).stem, existing_keys)
            else:
                vk = generate_virtual_key_from_parts(
                    vk_parts['moods'],
                    vk_parts['usable_in_contexts'],
                    vk_parts['instruments'],
                    vk_parts['styles'],
                    existing_keys,
                )
            existing_keys.add(vk)
            fp = TrackFingerprint(sha1=sha1, file_size=size, modified_time=mtime)
            _new_tags_placeholder = {group: [] for group in catalog.vocab.tag_vocab.keys()}
            for _g in ['moods','usable_in_contexts','instruments','styles']:
                if _g in _new_tags_placeholder and vk_parts.get(_g):
                    _new_tags_placeholder[_g] = [vk_parts[_g]]
            cid = uuid4()
            catalog.clusters.append(Cluster(cluster_id=cid, name=vk, created_at=utc_now_iso()))

            new_track = Track(
                track_id=tid,
                cluster_id=cid,
                original_path=rel_path,
                file_format=fmt,
                raw_file_name=Path(rel_path).name,
                raw_parent_dir_name=Path(rel_path).parent.name if str(Path(rel_path).parent) not in (".", "") else "",
                fingerprint=fp,
                virtual_key=vk,
                primary_role="",
                tags=_new_tags_placeholder,
                scales={name: int(sdef.default) for name, sdef in catalog.vocab.scale_defs.items()},
                loop_info=LoopInfo(),
                length_sec=length,
                bpm=None,
                notes="",
                licensing=LicensingInfo(),
                missing_file=False,
                duplicate_of=None,
            )
            catalog.tracks.append(new_track)
            track_by_id[tid] = new_track
            track_by_relpath[rel_path] = new_track
            canonical_by_sha1[sha1] = new_track
            summary.new += 1
            continue

        # We have a canonical track for this sha1
        canonical.missing_file = False

        if canonical.original_path == rel_path:
            # Same file, maybe updated size/mtime
            if canonical.fingerprint.file_size != size or abs(canonical.fingerprint.modified_time - mtime) >= 1e-6:
                canonical.fingerprint.file_size = size
                canonical.fingerprint.modified_time = mtime
                canonical.file_format = fmt
                if canonical.length_sec is None and length is not None:
                    canonical.length_sec = length
                summary.updated += 1
            continue

        # sha1 matches but path differs
        # If canonical currently points to a missing file (on disk), relink it to this path.
        if not _file_exists(raw_music_dir, canonical.original_path):
            old_path = canonical.original_path
            # Update maps
            if old_path in track_by_relpath:
                track_by_relpath.pop(old_path, None)
            canonical.original_path = rel_path
            canonical.file_format = fmt
            # Update path hints for fallback relinking
            canonical.raw_file_name = Path(rel_path).name
            canonical.raw_parent_dir_name = Path(rel_path).parent.name if str(Path(rel_path).parent) not in (".", "") else ""
            canonical.fingerprint.file_size = size
            canonical.fingerprint.modified_time = mtime
            if canonical.length_sec is None and length is not None:
                canonical.length_sec = length
            track_by_relpath[rel_path] = canonical
            summary.relinked += 1
            continue

        # Otherwise this is a duplicate copy on disk.
        # If we already have an entry with this rel_path, just mark it present and link duplicate_of.
        if existing_track is not None:
            existing_track.missing_file = False
            existing_track.raw_file_name = Path(rel_path).name
            existing_track.raw_parent_dir_name = Path(rel_path).parent.name if str(Path(rel_path).parent) not in (".", "") else ""
            if existing_track.duplicate_of is None and existing_track.track_id != canonical.track_id:
                existing_track.duplicate_of = canonical.track_id
                summary.updated += 1
            continue

        # Create a duplicate entry
        tid = uuid4()
        vk_parts = default_vk_components(catalog.vocab.tag_vocab)
        if init_virtual_key_from_filename:
            vk = generate_virtual_key_from_filename(Path(rel_path).stem, existing_keys)
        else:
            vk = generate_virtual_key_from_parts(
                vk_parts['moods'],
                vk_parts['usable_in_contexts'],
                vk_parts['instruments'],
                vk_parts['styles'],
                existing_keys,
            )
        existing_keys.add(vk)
        fp = TrackFingerprint(sha1=sha1, file_size=size, modified_time=mtime)
        _dup_tags_placeholder = {group: [] for group in catalog.vocab.tag_vocab.keys()}
        for _g in ['moods','usable_in_contexts','instruments','styles']:
            if _g in _dup_tags_placeholder and vk_parts.get(_g):
                _dup_tags_placeholder[_g] = [vk_parts[_g]]
        cid = uuid4()
        catalog.clusters.append(Cluster(cluster_id=cid, name=vk, created_at=utc_now_iso()))

        dup_track = Track(
            track_id=tid,
            cluster_id=cid,
            original_path=rel_path,
            file_format=fmt,
            raw_file_name=Path(rel_path).name,
            raw_parent_dir_name=Path(rel_path).parent.name if str(Path(rel_path).parent) not in (".", "") else "",
            fingerprint=fp,
            virtual_key=vk,
            primary_role="",
            tags=_dup_tags_placeholder,
            scales={name: int(sdef.default) for name, sdef in catalog.vocab.scale_defs.items()},
            loop_info=LoopInfo(),
            length_sec=length,
            bpm=None,
            notes="",
            licensing=LicensingInfo(),
            missing_file=False,
            duplicate_of=canonical.track_id,
        )
        catalog.tracks.append(dup_track)
        track_by_id[tid] = dup_track
        track_by_relpath[rel_path] = dup_track
        summary.duplicates += 1

    # Post-process: mark missing and normalize
    missing_count = 0
    for t in catalog.tracks:
        ensure_track_tags(t, catalog.vocab.tag_vocab)
        ensure_track_scales(t, catalog.vocab.scale_defs)
        normalize_track_tags(t, catalog.vocab, catalog.aliases)
        if t.missing_file:
            missing_count += 1

    summary.missing = missing_count
    return summary
