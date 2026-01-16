from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AppConfig:
    raw_music_directory: str = ""
    catalog_file: str = ""


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return AppConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    return AppConfig(
        raw_music_directory=str(data.get("raw_music_directory", "")),
        catalog_file=str(data.get("catalog_file", "")),
    )


def save_config(cfg: AppConfig, config_path: Optional[Path] = None) -> None:
    path = config_path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "raw_music_directory": cfg.raw_music_directory,
                "catalog_file": cfg.catalog_file,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)
