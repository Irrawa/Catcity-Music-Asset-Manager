from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class LoopInfo(BaseModel):
    can_loop: bool = False
    loop_start_sec: Optional[float] = None
    loop_end_sec: Optional[float] = None
    intro_sec: Optional[float] = None
    outro_sec: Optional[float] = None


class LicensingInfo(BaseModel):
    source_pack: str = ""
    license_type: str = ""
    proof_url_or_file: str = ""
    attribution_required: bool = False
    attribution_text: str = ""


class TrackFingerprint(BaseModel):
    sha1: str
    file_size: int
    modified_time: float  # epoch seconds


class Track(BaseModel):
    track_id: UUID
    # Cluster ID for grouping near-identical tracks (format / loop variants).
    # Optional for backward-compat; the app will auto-backfill on load.
    cluster_id: Optional[UUID] = None

    original_path: str  # relative to RawMusicDirectory
    file_format: str

    # Hints for fallback relinking when SHA-1 fingerprint matching fails.
    # These are derived from the last known path at the time the track was linked.
    raw_file_name: str = ""  # e.g. "battle_theme_01.mp3"
    raw_parent_dir_name: str = ""  # immediate parent folder name, e.g. "PackA"

    fingerprint: TrackFingerprint

    virtual_key: str
    primary_role: str = ""

    tags: Dict[str, List[str]] = Field(default_factory=dict)
    scales: Dict[str, int] = Field(default_factory=dict)

    loop_info: LoopInfo = Field(default_factory=LoopInfo)

    length_sec: Optional[float] = None
    bpm: Optional[int] = None
    notes: str = ""

    licensing: LicensingInfo = Field(default_factory=LicensingInfo)

    missing_file: bool = False
    duplicate_of: Optional[UUID] = None


class ScaleDef(BaseModel):
    """Definition for a numeric scale."""

    min: int = 0
    max: int = 5
    default: int = 0


class Vocab(BaseModel):
    primary_roles: List[str] = Field(default_factory=list)
    tag_vocab: Dict[str, List[str]] = Field(default_factory=dict)

    # New format (preferred): a scale name -> definition
    scale_defs: Dict[str, ScaleDef] = Field(default_factory=dict)

    # Backward-compat field: older catalogs stored only names.
    # The app will auto-upgrade to scale_defs in load.
    scale_names: List[str] = Field(default_factory=list)


class Cluster(BaseModel):
    cluster_id: UUID
    name: str
    created_at: str = Field(default_factory=_utc_now_iso)


class Catalog(BaseModel):
    # schema_version 2 introduces clusters.
    schema_version: int = 2

    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)

    raw_music_directory: str = ""

    vocab: Vocab = Field(default_factory=Vocab)
    aliases: Dict[str, str] = Field(default_factory=dict)

    clusters: List[Cluster] = Field(default_factory=list)
    tracks: List[Track] = Field(default_factory=list)
