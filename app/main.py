from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import re

from .catalog import ensure_catalog_clusters, ensure_track_scales, generate_virtual_key_from_parts, load_or_create_catalog, save_catalog_atomic, scan_and_sync
from .config import AppConfig, load_config, save_config
from .models import Catalog, Cluster, LicensingInfo, Track
from .utils import audio_length_seconds, guess_mime, is_subpath, relpath_posix, safe_join, sha1_file


# Tag groups required by the virtual_key pattern (mood_context_instrument_style_sn)
PROTECTED_TAG_GROUPS = {
    'moods',
    'usable_in_contexts',
    'instruments',
    'styles',
}

# Virtual key component tag groups
VK_GROUPS = {
    'mood': 'moods',
    'context': 'usable_in_contexts',
    'instrument': 'instruments',
    'style': 'styles',
}



def _cluster_id_to_name_map(st: State) -> Dict[str, str]:
    if st.catalog is None:
        return {}
    ensure_catalog_clusters(st.catalog)
    return {str(c.cluster_id): c.name for c in (st.catalog.clusters or [])}


def _ensure_clusters_saved_if_upgraded(st: State) -> None:
    """Ensure catalog clusters exist and persist the upgrade if needed.

    Some older catalog JSON files may not include clusters or track.cluster_id.
    The UI relies on cluster ids being present in /api/tracks.
    This helper performs a safe, one-time schema backfill and saves only when
    it detects that an upgrade/backfill actually happened.
    """
    if st.catalog is None:
        return
    cat = st.catalog

    prev_schema = getattr(cat, 'schema_version', 1)
    prev_clusters_len = len(getattr(cat, 'clusters', []) or [])
    prev_missing_cluster_id = any(getattr(t, 'cluster_id', None) is None for t in (cat.tracks or []))

    ensure_catalog_clusters(cat)

    changed = (
        prev_schema != getattr(cat, 'schema_version', 1)
        or prev_clusters_len != len(getattr(cat, 'clusters', []) or [])
        or prev_missing_cluster_id
    )
    if changed:
        st.save()


def _pick_identity_value(track: Track, group: str, st: State) -> str:
    """Pick the canonical identity value for a VK component group."""
    if track.tags and group in track.tags and track.tags[group]:
        return str(track.tags[group][0])
    # Unknown is represented by selecting nothing.
    return ""


def _get_cluster_tracks(st: State, cluster_id: UUID) -> list[Track]:
    if st.catalog is None:
        return []
    return [t for t in st.catalog.tracks if getattr(t, 'cluster_id', None) == cluster_id]


def _find_cluster(st: State, cluster_id: UUID) -> Optional[Cluster]:
    if st.catalog is None:
        return None
    ensure_catalog_clusters(st.catalog)
    for c in st.catalog.clusters:
        if c.cluster_id == cluster_id:
            return c
    return None


def _rebuild_virtual_key_for_track(st: State, t: Track, mood: str, ctx: str, inst: str, style: str) -> str:
    # Build unique key using the same backend generator.
    existing = {x.virtual_key for x in st.catalog.tracks if x.virtual_key} if st.catalog else set()
    # Temporarily remove current key so we can reassign without colliding with itself
    existing.discard(t.virtual_key)
    vk = generate_virtual_key_from_parts(mood, ctx, inst, style, existing)
    return vk
def _try_native_dialog_select_directory(initial: str = "") -> str:
    """Open a native directory picker (local-first).

    Note: This runs on the machine where the server process is running.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        path = filedialog.askdirectory(
            title="Select Raw Music Directory",
            initialdir=initial or None,
            mustexist=True,
        )
        root.destroy()
        return str(path or "").strip()
    except Exception:
        return ""


def _try_native_dialog_select_catalog_json(initial: str = "") -> str:
    """Open a native file picker for the catalog JSON path.

    Uses a Save dialog so users can choose an existing file or create a new one.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        path = filedialog.asksaveasfilename(
            title="Select Catalog JSON (existing or new)",
            initialdir=initial or None,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*")],
        )
        root.destroy()
        p = str(path or "").strip()
        if p and not p.lower().endswith(".json"):
            p = p + ".json"
        return p
    except Exception:
        return ""


def _try_native_dialog_select_audio_file(initial_dir: str = "") -> str:
    """Open a native file picker for selecting an audio file.

    This is used for manually locating missing tracks.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        path = filedialog.askopenfilename(
            title="Locate audio file",
            initialdir=initial_dir or None,
            filetypes=[
                ("Audio files", "*.mp3 *.ogg *.wav *.flac *.m4a *.aac"),
                ("All files", "*"),
            ],
        )
        root.destroy()
        return str(path or "").strip()
    except Exception:
        return ""


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


class State:
    def __init__(self) -> None:
        self.cfg: AppConfig = load_config()
        self.raw_dir: Optional[Path] = None
        self.catalog_path: Optional[Path] = None
        self.catalog: Optional[Catalog] = None
        # Track on-disk catalog file identity so we can reload if the JSON is
        # updated externally (e.g., git pull / cloud sync).
        self._catalog_stat: Optional[tuple[int, int]] = None  # (mtime_ns, size)

    def is_configured(self) -> bool:
        return bool(self.cfg.raw_music_directory) and bool(self.cfg.catalog_file)

    def load(self) -> None:
        if not self.is_configured():
            self.raw_dir = None
            self.catalog_path = None
            self.catalog = None
            self._catalog_stat = None
            return
        self.raw_dir = Path(self.cfg.raw_music_directory).expanduser().resolve()
        self.catalog_path = Path(self.cfg.catalog_file).expanduser().resolve()
        self.catalog = load_or_create_catalog(self.catalog_path, self.raw_dir)
        self._refresh_catalog_stat()

    def save(self) -> None:
        if self.catalog is None or self.catalog_path is None:
            return
        save_catalog_atomic(self.catalog, self.catalog_path)
        self._refresh_catalog_stat()

    def _refresh_catalog_stat(self) -> None:
        if self.catalog_path is None:
            self._catalog_stat = None
            return
        try:
            st = self.catalog_path.stat()
            self._catalog_stat = (st.st_mtime_ns, st.st_size)
        except Exception:
            self._catalog_stat = None

    def reload_if_changed(self) -> None:
        """Reload catalog from disk if the selected JSON changed externally.

        This is important for workflows like:
        - Device A edits data1.json and pushes to cloud
        - Device B pulls the updated JSON while the app is already running
        Without a restart, the in-memory catalog would otherwise stay stale.

        Non-goal: multi-user concurrent edits. We assume single-writer.
        """
        if self.catalog_path is None or self.raw_dir is None:
            return
        try:
            st = self.catalog_path.stat()
            cur = (st.st_mtime_ns, st.st_size)
        except Exception:
            return
        if self._catalog_stat is None:
            self._catalog_stat = cur
            return
        if cur != self._catalog_stat:
            # Reload from disk. load_or_create_catalog will also refresh
            # raw_music_directory inside the catalog to match the selected raw_dir.
            self.catalog = load_or_create_catalog(self.catalog_path, self.raw_dir)
            # load_or_create_catalog may update/save the JSON (e.g., raw_music_directory
            # portability fix). Refresh stat after load to avoid repeated reloads.
            self._refresh_catalog_stat()


state = State()
state.load()

app = FastAPI(title="Music Catalog Manager", version="0.1.0")

app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")


def require_state() -> State:
    # Reload on demand if not loaded yet
    if state.catalog is None and state.is_configured():
        state.load()
    # If the catalog JSON was updated externally (cloud sync / git pull),
    # reload it automatically so users see the latest version.
    if state.catalog is not None:
        state.reload_if_changed()
    return state


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> HTMLResponse:
    cfg = load_config()
    return TEMPLATES.TemplateResponse(
        "setup.html",
        {
            "request": request,
            "raw_music_directory": cfg.raw_music_directory,
            "catalog_file": cfg.catalog_file,
        },
    )


@app.post("/api/dialog/select_raw_dir")
def api_dialog_select_raw_dir() -> Dict[str, Any]:
    """Open a native OS dialog to choose the RawMusicDirectory.

    Returns an empty string if dialogs are unavailable (e.g., headless environment).
    """
    cfg = load_config()
    chosen = _try_native_dialog_select_directory(cfg.raw_music_directory or "")
    return {"ok": bool(chosen), "path": chosen}


@app.post("/api/dialog/select_catalog_json")
def api_dialog_select_catalog_json() -> Dict[str, Any]:
    """Open a native OS dialog to choose the CatalogFile JSON path."""
    cfg = load_config()
    initial = ""
    if cfg.catalog_file:
        try:
            initial = str(Path(cfg.catalog_file).expanduser().resolve().parent)
        except Exception:
            initial = ""
    chosen = _try_native_dialog_select_catalog_json(initial)
    return {"ok": bool(chosen), "path": chosen}


@app.post("/setup")
async def setup_submit(request: Request) -> RedirectResponse:
    form = await request.form()
    raw_dir = str(form.get("raw_music_directory", "")).strip()
    cat_file = str(form.get("catalog_file", "")).strip()

    if not raw_dir or not cat_file:
        return RedirectResponse(url="/setup", status_code=303)

    cfg = AppConfig(raw_music_directory=raw_dir, catalog_file=cat_file)
    save_config(cfg)

    # refresh global state
    state.cfg = cfg
    state.load()

    return RedirectResponse(url="/", status_code=303)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    st = require_state()
    if st.catalog is None:
        return RedirectResponse(url="/setup", status_code=303)

    return TEMPLATES.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_title": "Music Catalog Manager",
        },
    )


@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None or st.raw_dir is None or st.catalog_path is None:
        return {
            "configured": False,
            "raw_music_directory": st.cfg.raw_music_directory,
            "catalog_file": st.cfg.catalog_file,
        }

    # Ensure older catalogs are upgraded to include clusters before the UI loads.
    # This avoids cases where /api/tracks returns cluster_id=None for every track,
    # which breaks cluster operations like splitting.
    _ensure_clusters_saved_if_upgraded(st)

    return {
        "configured": True,
        "raw_music_directory": str(st.raw_dir),
        "catalog_file": str(st.catalog_path),
        "catalog": {
            "schema_version": st.catalog.schema_version,
            "created_at": st.catalog.created_at,
            "updated_at": st.catalog.updated_at,
            "track_count": len(st.catalog.tracks),
        },
        "vocab": st.catalog.vocab.model_dump(),
        "aliases": st.catalog.aliases,
    }


@app.post("/api/rescan")
def api_rescan() -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None or st.raw_dir is None:
        raise HTTPException(status_code=400, detail="App not configured")

    summary = scan_and_sync(st.raw_dir, st.catalog)
    st.save()
    return {"ok": True, "summary": summary.__dict__, "track_count": len(st.catalog.tracks)}




@app.get("/api/clusters")
def api_clusters() -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    _ensure_clusters_saved_if_upgraded(st)

    # Precompute counts and a representative track for shared metadata.
    tracks_by_cluster: Dict[UUID, list[Track]] = {}
    for t in st.catalog.tracks:
        cid = getattr(t, 'cluster_id', None)
        if cid is None:
            continue
        tracks_by_cluster.setdefault(cid, []).append(t)

    clusters = []
    for c in st.catalog.clusters:
        ctracks = tracks_by_cluster.get(c.cluster_id, [])
        rep = ctracks[0] if ctracks else None
        clusters.append({
            "cluster_id": str(c.cluster_id),
            "name": c.name,
            "track_count": len(ctracks),
            "representative_track_id": str(rep.track_id) if rep else None,
            "mood": _pick_identity_value(rep, VK_GROUPS['mood'], st) if rep else "",
            "context": _pick_identity_value(rep, VK_GROUPS['context'], st) if rep else "",
            "instrument": _pick_identity_value(rep, VK_GROUPS['instrument'], st) if rep else "",
            "style": _pick_identity_value(rep, VK_GROUPS['style'], st) if rep else "",
        })

    # Sort by name for UI
    clusters.sort(key=lambda x: (x.get('name') or '').lower())

    return {"clusters": clusters}


@app.post("/api/clusters/merge")
async def api_clusters_merge(request: Request) -> Dict[str, Any]:
    """Merge one cluster into another.

    All tracks from source cluster move into target cluster.
    The source cluster is removed.

    Merge copies target cluster's shared metadata (bpm, scales, tags, licensing)
    onto moved tracks, but keeps per-file fields (path, format, loop_info, etc.).
    It also aligns VK identity parts (mood/context/instrument/style) and regenerates
    virtual keys to avoid collisions.
    """
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    target_id = str(payload.get('target_cluster_id', '')).strip()
    source_id = str(payload.get('source_cluster_id', '')).strip()

    try:
        target_uuid = UUID(target_id)
        source_uuid = UUID(source_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cluster id")

    if target_uuid == source_uuid:
        raise HTTPException(status_code=400, detail="Cannot merge a cluster into itself")

    ensure_catalog_clusters(st.catalog)

    target_cluster = _find_cluster(st, target_uuid)
    source_cluster = _find_cluster(st, source_uuid)
    if target_cluster is None or source_cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    target_tracks = _get_cluster_tracks(st, target_uuid)
    source_tracks = _get_cluster_tracks(st, source_uuid)

    if not target_tracks:
        raise HTTPException(status_code=400, detail="Target cluster has no tracks")
    if not source_tracks:
        # Nothing to do; remove empty source cluster
        st.catalog.clusters = [c for c in st.catalog.clusters if c.cluster_id != source_uuid]
        st.save()
        return {"ok": True, "moved": 0}

    template = target_tracks[0]

    # Shared metadata to copy
    # NOTE: tags are stored as group -> list[str]. Some groups
    # (mood/context/instrument/style) support multi-choice selections,
    # where the *first* value is the VK primary. During cluster merge we must
    # preserve the full multi-choice lists from the target cluster.
    template_tags = {k: list(v) for k, v in (template.tags or {}).items()}
    template_scales = template.scales or {}
    template_bpm = template.bpm
    template_lic = template.licensing.model_copy(deep=True)

    # VK identity components (selected values, not slugified)
    mood = _pick_identity_value(template, VK_GROUPS['mood'], st)
    ctx = _pick_identity_value(template, VK_GROUPS['context'], st)
    inst = _pick_identity_value(template, VK_GROUPS['instrument'], st)
    style = _pick_identity_value(template, VK_GROUPS['style'], st)

    def _normalize_primary_list(primary: str, values: list[str]) -> list[str]:
        """Ensure `primary` is the first element of `values` (if present).

        - Keeps original order for the remaining values.
        - Removes duplicates while preserving order.
        """
        out: list[str] = []
        seen: set[str] = set()
        if primary:
            out.append(primary)
            seen.add(primary)
        for x in values or []:
            if not x:
                continue
            if x in seen:
                continue
            out.append(x)
            seen.add(x)
        return out

    template_mood_list = _normalize_primary_list(mood, list(template_tags.get(VK_GROUPS['mood'], [])))
    template_ctx_list = _normalize_primary_list(ctx, list(template_tags.get(VK_GROUPS['context'], [])))
    template_inst_list = _normalize_primary_list(inst, list(template_tags.get(VK_GROUPS['instrument'], [])))
    template_style_list = _normalize_primary_list(style, list(template_tags.get(VK_GROUPS['style'], [])))

    moved = 0
    for t in source_tracks:
        # Move cluster
        t.cluster_id = target_uuid

        # Copy shared fields
        t.bpm = template_bpm
        t.scales = dict(template_scales)
        t.tags = {k: list(v) for k, v in template_tags.items()}
        t.licensing = template_lic.model_copy(deep=True)

        # Align identity groups explicitly while PRESERVING multi-choice selections.
        # The first element is the VK primary; additional selected values are kept.
        t.tags[VK_GROUPS['mood']] = list(template_mood_list)
        t.tags[VK_GROUPS['context']] = list(template_ctx_list)
        t.tags[VK_GROUPS['instrument']] = list(template_inst_list)
        t.tags[VK_GROUPS['style']] = list(template_style_list)

        moved += 1

    # After merge, normalize virtual keys for ALL tracks in the target cluster so that
    # they share the same mood/context/instrument/style prefix and only differ by SN.
    # (This changes existing virtual keys in that cluster.)
    target_all = [x for x in st.catalog.tracks if getattr(x, 'cluster_id', None) == target_uuid]
    # Existing keys outside the cluster are kept as reserved.
    reserved = {x.virtual_key for x in st.catalog.tracks if x.virtual_key and getattr(x, 'cluster_id', None) != target_uuid}
    for t in sorted(target_all, key=lambda x: (x.original_path or '', str(x.track_id))):
        try:
            new_vk = generate_virtual_key_from_parts(mood, ctx, inst, style, reserved)
            if new_vk:
                t.virtual_key = new_vk
                reserved.add(new_vk)
        except Exception:
            # If something goes wrong, keep current VK.
            pass

    # Remove source cluster
    st.catalog.clusters = [c for c in st.catalog.clusters if c.cluster_id != source_uuid]

    st.save()
    return {"ok": True, "moved": moved}


@app.post("/api/clusters/split")
async def api_clusters_split(request: Request) -> Dict[str, Any]:
    """Split a cluster into two.

    The selected tracks are moved from the source cluster into a newly created cluster.
    Virtual keys and per-track metadata are preserved.

    Payload:
      - source_cluster_id: str
      - track_ids: list[str] (subset of tracks in the source cluster)
      - new_cluster_name: str (optional)
    """
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    source_id = str(payload.get('source_cluster_id', '')).strip()
    new_name = str(payload.get('new_cluster_name', '')).strip()
    track_ids = payload.get('track_ids', [])

    try:
        source_uuid = UUID(source_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cluster id")

    if not isinstance(track_ids, list):
        raise HTTPException(status_code=400, detail="track_ids must be a list")

    ensure_catalog_clusters(st.catalog)
    source_cluster = _find_cluster(st, source_uuid)
    if source_cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    source_tracks = _get_cluster_tracks(st, source_uuid)
    if not source_tracks:
        raise HTTPException(status_code=400, detail="Source cluster has no tracks")

    # Parse and validate track ids
    chosen: set[UUID] = set()
    for raw in track_ids:
        try:
            tid = UUID(str(raw))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid track_id: {raw}")
        chosen.add(tid)

    if not chosen:
        raise HTTPException(status_code=400, detail="No tracks selected")

    # Ensure all chosen tracks belong to the source cluster
    source_track_ids = {t.track_id for t in source_tracks}
    not_in_cluster = [str(tid) for tid in chosen if tid not in source_track_ids]
    if not_in_cluster:
        raise HTTPException(status_code=400, detail="Some selected tracks are not in the source cluster")

    if len(chosen) >= len(source_tracks):
        raise HTTPException(status_code=400, detail="Cannot split: selection must be a strict subset (leave at least one track)")

    # Create the new cluster
    new_cluster_id = uuid4()

    # Default name: derive from source name, but keep it short and readable.
    if not new_name:
        base = (source_cluster.name or 'cluster').strip()
        new_name = f"{base} (split)"

    st.catalog.clusters.append(Cluster(cluster_id=new_cluster_id, name=new_name))

    moved = 0
    for t in st.catalog.tracks:
        if t.track_id in chosen and getattr(t, 'cluster_id', None) == source_uuid:
            t.cluster_id = new_cluster_id
            moved += 1

    st.save()
    return {"ok": True, "new_cluster_id": str(new_cluster_id), "moved": moved}

@app.get("/api/tracks")
def api_tracks() -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    # Ensure cluster_id is available on all tracks. This is important for
    # cluster split UI which lists tracks per cluster.
    _ensure_clusters_saved_if_upgraded(st)

    # Provide a lighter list payload for the library panel
    cid_to_name = _cluster_id_to_name_map(st)

    tracks_out = []
    for t in st.catalog.tracks:
        cid_str = str(t.cluster_id) if getattr(t, "cluster_id", None) else ""
        tracks_out.append(
            {
                "track_id": str(t.track_id),
                "cluster_id": cid_str or None,
                "cluster_name": cid_to_name.get(cid_str, "") if cid_str else "",
                "virtual_key": t.virtual_key,
                "primary_role": t.primary_role,
                "file_format": t.file_format,
                "original_path": t.original_path,
                "length_sec": t.length_sec,
                "missing_file": t.missing_file,
                "duplicate_of": str(t.duplicate_of) if t.duplicate_of else None,
                "tags": t.tags,
                "scales": t.scales,
                "loop_info": t.loop_info.model_dump(),
                "notes": t.notes,
            }
        )

    return {"tracks": tracks_out}


@app.get("/api/track/{track_id}")
def api_track(track_id: str) -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    try:
        tid = UUID(track_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid track_id")

    t = next((x for x in st.catalog.tracks if x.track_id == tid), None)
    if t is None:
        raise HTTPException(status_code=404, detail="Track not found")

    return {"track": t.model_dump()}


@app.put("/api/track/{track_id}")
async def api_update_track(track_id: str, request: Request) -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    try:
        tid = UUID(track_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid track_id")

    idx = None
    for i, t in enumerate(st.catalog.tracks):
        if t.track_id == tid:
            idx = i
            break
    if idx is None:
        raise HTTPException(status_code=404, detail="Track not found")

    payload = await request.json()
    if not isinstance(payload, dict) or "track" not in payload:
        raise HTTPException(status_code=400, detail="Expected JSON with key 'track'")

    new_track_data = payload["track"]
    if not isinstance(new_track_data, dict):
        raise HTTPException(status_code=400, detail="'track' must be an object")

    # Enforce id immutability
    new_track_data["track_id"] = str(tid)

    # Validate uniqueness of virtual_key
    new_vk = str(new_track_data.get("virtual_key", "")).strip()
    if not new_vk:
        raise HTTPException(status_code=400, detail="virtual_key cannot be empty")
    for j, other in enumerate(st.catalog.tracks):
        if j != idx and other.virtual_key == new_vk:
            raise HTTPException(status_code=409, detail="virtual_key already exists")

    # Build new model
    try:
        new_track = Track.model_validate(new_track_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid track data: {e}")

    st.catalog.tracks[idx] = new_track
    st.save()
    return {"ok": True}


@app.post("/api/track/{track_id}/locate")
def api_locate_track(track_id: str) -> Dict[str, Any]:
    """Manually locate and relink a track to a local audio file.

    Uses a native OS file picker (local-first). The selected file must be
    inside RawMusicDirectory (we never copy or duplicate audio files).
    """
    st = require_state()
    if st.catalog is None or st.raw_dir is None:
        raise HTTPException(status_code=400, detail="App not configured")

    try:
        tid = UUID(track_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid track_id")

    t = next((x for x in st.catalog.tracks if x.track_id == tid), None)
    if t is None:
        raise HTTPException(status_code=404, detail="Track not found")

    chosen = _try_native_dialog_select_audio_file(str(st.raw_dir))
    if not chosen:
        return {"ok": True, "cancelled": True}

    chosen_path = Path(chosen)
    if not chosen_path.exists() or not chosen_path.is_file():
        raise HTTPException(status_code=400, detail="Selected file does not exist")
    if not is_subpath(chosen_path, st.raw_dir):
        raise HTTPException(status_code=400, detail="Selected file must be inside RawMusicDirectory")

    rel_path = relpath_posix(chosen_path, st.raw_dir)

    # Prevent two tracks from pointing to the same rel_path (keeps catalog consistent)
    for other in st.catalog.tracks:
        if other.track_id != t.track_id and other.original_path == rel_path:
            raise HTTPException(status_code=409, detail="That file is already linked to another track")

    stat = chosen_path.stat()
    size = int(stat.st_size)
    mtime = float(stat.st_mtime)
    sha1 = sha1_file(chosen_path)
    fmt = chosen_path.suffix.lower().lstrip(".")
    length = audio_length_seconds(chosen_path)

    t.original_path = rel_path
    t.file_format = fmt
    t.raw_file_name = chosen_path.name
    t.raw_parent_dir_name = chosen_path.parent.name if chosen_path.parent != st.raw_dir else ""
    t.fingerprint.sha1 = sha1
    t.fingerprint.file_size = size
    t.fingerprint.modified_time = mtime
    if length is not None:
        t.length_sec = length
    t.missing_file = False

    st.save()
    return {"ok": True, "track": t.model_dump()}


@app.delete("/api/track/{track_id}")
def api_delete_track(track_id: str) -> Dict[str, Any]:
    """Remove a track from the catalog.

    This is useful for cleaning up missing or duplicate entries.
    """
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    try:
        tid = UUID(track_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid track_id")

    idx = None
    for i, t in enumerate(st.catalog.tracks):
        if t.track_id == tid:
            idx = i
            break
    if idx is None:
        raise HTTPException(status_code=404, detail="Track not found")

    st.catalog.tracks.pop(idx)

    # If other tracks were marked as duplicates of this track, clear the reference.
    for t in st.catalog.tracks:
        if t.duplicate_of == tid:
            t.duplicate_of = None

    st.save()
    return {"ok": True}


@app.post("/api/vocab/add")
async def api_vocab_add(request: Request) -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    kind = str(payload.get("kind", "")).strip()  # 'primary_role' or 'tag'
    value = str(payload.get("value", "")).strip()
    group = str(payload.get("group", "")).strip()  # for tag

    if not value:
        raise HTTPException(status_code=400, detail="value required")

    if kind == "primary_role":
        if value not in st.catalog.vocab.primary_roles:
            st.catalog.vocab.primary_roles.append(value)
            st.save()
        return {"ok": True, "vocab": st.catalog.vocab.model_dump()}

    if kind == "tag":
        if not group:
            raise HTTPException(status_code=400, detail="group required for tag")
        st.catalog.vocab.tag_vocab.setdefault(group, [])
        if value not in st.catalog.vocab.tag_vocab[group]:
            st.catalog.vocab.tag_vocab[group].append(value)
            # also ensure every track has this group key
            for t in st.catalog.tracks:
                t.tags.setdefault(group, [])
            st.save()
        return {"ok": True, "vocab": st.catalog.vocab.model_dump()}

    raise HTTPException(status_code=400, detail="kind must be 'primary_role' or 'tag'")


@app.post("/api/vocab/tag_group/add")
async def api_tag_group_add(request: Request) -> Dict[str, Any]:
    """Add a new tag group (category).

    Updates vocab.tag_vocab and ensures all tracks have the group key in their tags dict.
    """
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    group = str(payload.get("group", "")).strip()
    if not group:
        raise HTTPException(status_code=400, detail="group required")

    if group in PROTECTED_TAG_GROUPS:
        raise HTTPException(status_code=400, detail=f'Tag group "{group}" is required by virtual keys and cannot be deleted.')

    if group not in st.catalog.vocab.tag_vocab:
        st.catalog.vocab.tag_vocab[group] = []
        for t in st.catalog.tracks:
            t.tags.setdefault(group, [])
        st.save()

    return {"ok": True, "vocab": st.catalog.vocab.model_dump()}


@app.post("/api/vocab/tag_group/delete")
async def api_tag_group_delete(request: Request) -> Dict[str, Any]:
    """Delete a whole tag group.

    Removes the group from vocab.tag_vocab and removes the group key from all tracks' tags.
    """
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    group = str(payload.get("group", "")).strip()
    if not group:
        raise HTTPException(status_code=400, detail="group required")

    if group in PROTECTED_TAG_GROUPS:
        raise HTTPException(
            status_code=400,
            detail=f'Tag group "{group}" is required by virtual keys and cannot be deleted.',
        )


    removed_assignments = 0
    if group in st.catalog.vocab.tag_vocab:
        st.catalog.vocab.tag_vocab.pop(group, None)
        for t in st.catalog.tracks:
            if group in t.tags:
                removed_assignments += len(t.tags.get(group, []) or [])
                t.tags.pop(group, None)
        st.save()

    return {"ok": True, "vocab": st.catalog.vocab.model_dump(), "removed_assignments": removed_assignments}


@app.post("/api/vocab/tag_value/delete")
async def api_tag_value_delete(request: Request) -> Dict[str, Any]:
    """Delete a tag value from a group.

    Removes it from vocab.tag_vocab[group] and from all tracks under that group.
    Comparison is case-insensitive to avoid leftover variants.
    """
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    group = str(payload.get("group", "")).strip()
    value = str(payload.get("value", "")).strip()
    if not group:
        raise HTTPException(status_code=400, detail="group required")
    if not value:
        raise HTTPException(status_code=400, detail="value required")

    if group not in st.catalog.vocab.tag_vocab:
        raise HTTPException(status_code=404, detail="tag group not found")

    target_low = value.lower()

    # Remove from vocab list (case-insensitive)
    before = list(st.catalog.vocab.tag_vocab.get(group, []))
    after = [v for v in before if str(v).lower() != target_low]
    st.catalog.vocab.tag_vocab[group] = after
    removed_from_vocab = len(before) - len(after)

    removed_from_tracks = 0
    for t in st.catalog.tracks:
        arr = t.tags.get(group, []) or []
        new_arr = [v for v in arr if str(v).lower() != target_low]
        removed_from_tracks += len(arr) - len(new_arr)
        t.tags[group] = new_arr

    if removed_from_vocab or removed_from_tracks:
        st.save()

    return {
        "ok": True,
        "vocab": st.catalog.vocab.model_dump(),
        "removed_from_vocab": removed_from_vocab,
        "removed_from_tracks": removed_from_tracks,
    }


# ---- Scale admin (add/delete/update scale definitions)


_SCALE_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")


@app.post("/api/vocab/scale/add")
async def api_scale_add(request: Request) -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if not _SCALE_KEY_RE.match(name):
        raise HTTPException(status_code=400, detail="Scale name must be alphanumeric/underscore (e.g., energy, emotional_weight)")

    try:
        mn = int(payload.get("min", 0))
        mx = int(payload.get("max", 5))
    except Exception:
        raise HTTPException(status_code=400, detail="min/max must be integers")

    if mx < mn:
        mn, mx = mx, mn

    default_raw = payload.get("default", mn)
    try:
        dv = int(default_raw)
    except Exception:
        dv = mn
    dv = max(mn, min(mx, dv))

    if name not in st.catalog.vocab.scale_defs:
        from .models import ScaleDef

        st.catalog.vocab.scale_defs[name] = ScaleDef(min=mn, max=mx, default=dv)
        if name not in st.catalog.vocab.scale_names:
            st.catalog.vocab.scale_names.append(name)

    # Ensure all tracks have the scale and clamp
    for t in st.catalog.tracks:
        ensure_track_scales(t, st.catalog.vocab.scale_defs)

    st.save()
    return {"ok": True, "vocab": st.catalog.vocab.model_dump()}


@app.post("/api/vocab/scale/delete")
async def api_scale_delete(request: Request) -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")

    removed_assignments = 0
    if name in st.catalog.vocab.scale_defs:
        st.catalog.vocab.scale_defs.pop(name, None)
        st.catalog.vocab.scale_names = [n for n in (st.catalog.vocab.scale_names or []) if n != name]
        for t in st.catalog.tracks:
            if name in (t.scales or {}):
                removed_assignments += 1
                t.scales.pop(name, None)
        st.save()

    return {"ok": True, "vocab": st.catalog.vocab.model_dump(), "removed_assignments": removed_assignments}


@app.post("/api/vocab/scale/update")
async def api_scale_update(request: Request) -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if name not in st.catalog.vocab.scale_defs:
        raise HTTPException(status_code=404, detail="scale not found")

    try:
        mn = int(payload.get("min", st.catalog.vocab.scale_defs[name].min))
        mx = int(payload.get("max", st.catalog.vocab.scale_defs[name].max))
    except Exception:
        raise HTTPException(status_code=400, detail="min/max must be integers")

    if mx < mn:
        mn, mx = mx, mn

    default_raw = payload.get("default", st.catalog.vocab.scale_defs[name].default)
    try:
        dv = int(default_raw)
    except Exception:
        dv = mn
    dv = max(mn, min(mx, dv))

    st.catalog.vocab.scale_defs[name].min = mn
    st.catalog.vocab.scale_defs[name].max = mx
    st.catalog.vocab.scale_defs[name].default = dv

    # Clamp all tracks to the updated range
    for t in st.catalog.tracks:
        ensure_track_scales(t, st.catalog.vocab.scale_defs)

    st.save()
    return {"ok": True, "vocab": st.catalog.vocab.model_dump()}


@app.post("/api/aliases/set")
async def api_alias_set(request: Request) -> Dict[str, Any]:
    st = require_state()
    if st.catalog is None:
        raise HTTPException(status_code=400, detail="App not configured")

    payload = await request.json()
    src = str(payload.get("src", "")).strip()
    dst = str(payload.get("dst", "")).strip()
    if not src or not dst:
        raise HTTPException(status_code=400, detail="src and dst required")

    st.catalog.aliases[src.lower()] = dst
    st.save()
    return {"ok": True, "aliases": st.catalog.aliases}


@app.get("/audio/{track_id}")
def serve_audio(track_id: str) -> FileResponse:
    st = require_state()
    if st.catalog is None or st.raw_dir is None:
        raise HTTPException(status_code=400, detail="App not configured")

    try:
        tid = UUID(track_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid track_id")

    t = next((x for x in st.catalog.tracks if x.track_id == tid), None)
    if t is None:
        raise HTTPException(status_code=404, detail="Track not found")

    try:
        abs_path = safe_join(st.raw_dir, t.original_path)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="File missing")

    return FileResponse(path=str(abs_path), media_type=guess_mime(abs_path), filename=abs_path.name)
