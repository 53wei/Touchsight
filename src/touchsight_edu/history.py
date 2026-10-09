from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any

from .contract import CompiledSurface, TactileFrame, TactileScene


HISTORY_ID = re.compile(r"^\d+_\d+$")


def _surface_from_dict(payload: dict[str, Any]) -> CompiledSurface:
    frame_payload = payload.get("frame")
    if not isinstance(frame_payload, dict):
        raise ValueError("历史记录缺少 frame")
    frame = TactileFrame(
        pins=tuple(int(value) for value in frame_payload.get("pins", [])),
        frame_id=int(frame_payload.get("frame_id", 0)),
        cols=int(frame_payload.get("cols", 0)),
        rows=int(frame_payload.get("rows", 0)),
        levels=int(frame_payload.get("levels", 0)),
        version=int(frame_payload.get("version", 1)),
        content_box=frame_payload.get("content_box"),
        source_size=frame_payload.get("source_size"),
    )
    scene_payload = payload.get("scene")
    scene = None
    if isinstance(scene_payload, dict):
        scene = TactileScene(
            title=str(scene_payload.get("title") or "历史教学图"),
            summary=str(scene_payload.get("summary") or ""),
            primitives=list(scene_payload.get("primitives") or []),
            regions=list(scene_payload.get("regions") or []),
            viewport=dict(scene_payload.get("viewport") or {}),
            context=dict(scene_payload.get("context") or {}),
            semantic_kind=str(scene_payload.get("semantic_kind") or "diagram"),
        )
    return CompiledSurface(
        frame=frame,
        mode=str(payload.get("mode") or "direct"),
        title=str(payload.get("title") or "历史点阵"),
        summary=str(payload.get("summary") or ""),
        regions=list(payload.get("regions") or []),
        region_map=[int(value) for value in payload.get("region_map", [])],
        context=dict(payload.get("context") or {}),
        scene=scene,
        warnings=list(payload.get("warnings") or []),
    )


class HistoryStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, surface: CompiledSurface) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        history_id = f"{time.time_ns()}_{surface.frame.frame_id}"
        path = self.directory / f"{history_id}.json"
        temporary = path.with_suffix(".json.tmp")
        record = {
            "history_id": history_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "surface": surface.to_dict(),
        }
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
        return history_id

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        records = []
        for path in sorted(self.directory.glob("*.json"), reverse=True)[:limit]:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(record, dict) and isinstance(record.get("surface"), dict):
                    records.append(record)
            except (OSError, json.JSONDecodeError):
                continue
        return records

    def restore(self, history_id: str, *, frame_id: int) -> CompiledSurface:
        if not HISTORY_ID.fullmatch(history_id):
            raise ValueError("历史记录编号无效")
        path = self.directory / f"{history_id}.json"
        if not path.is_file():
            raise ValueError("找不到这条历史记录")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("历史记录无法读取") from exc
        surface = _surface_from_dict(record.get("surface") or {})
        surface.frame = replace(surface.frame, frame_id=frame_id)
        return surface
