from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


COLS = 40
ROWS = 50
LEVELS = 4
PIN_COUNT = COLS * ROWS
HEIGHT_MM = (0, 2, 4, 6)
DEVICE_ID = "touchsight_edu_hd_v1"


@dataclass(frozen=True)
class TactileFrame:
    pins: tuple[int, ...]
    frame_id: int = 0
    cols: int = COLS
    rows: int = ROWS
    levels: int = LEVELS
    version: int = 1
    content_box: dict[str, int] | None = None
    source_size: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if (self.cols, self.rows, self.levels) != (COLS, ROWS, LEVELS):
            raise ValueError(f"frame contract must be {COLS}x{ROWS} with four levels")
        if len(self.pins) != PIN_COUNT:
            raise ValueError(f"frame must contain exactly {PIN_COUNT} pins")
        if any(value < 0 or value >= LEVELS for value in self.pins):
            raise ValueError("pin height must be 0, 1, 2, or 3")
        if self.frame_id < 0 or self.frame_id > 0xFFFFFFFF:
            raise ValueError("frame_id must fit uint32")

    @classmethod
    def from_rows(
        cls,
        rows: list[list[int]],
        *,
        frame_id: int = 0,
        content_box: dict[str, int] | None = None,
        source_size: dict[str, int] | None = None,
    ) -> "TactileFrame":
        if len(rows) != ROWS or any(len(row) != COLS for row in rows):
            raise ValueError(f"height_rows must be exactly {ROWS} rows x {COLS} cols")
        return cls(
            pins=tuple(int(value) for row in rows for value in row),
            frame_id=frame_id,
            content_box=content_box,
            source_size=source_size,
        )

    def height_rows(self) -> list[list[int]]:
        return [list(self.pins[y * COLS:(y + 1) * COLS]) for y in range(ROWS)]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pins"] = list(self.pins)
        payload["height_rows"] = self.height_rows()
        payload["height_mm"] = list(HEIGHT_MM)
        payload["orientation"] = "row-major; origin top-left; x right; y down"
        return payload


@dataclass
class TactileScene:
    title: str
    summary: str
    primitives: list[dict[str, Any]]
    regions: list[dict[str, Any]]
    viewport: dict[str, float] = field(
        default_factory=lambda: {"x_min": -3, "x_max": 3, "y_min": -1, "y_max": 8}
    )
    context: dict[str, str] = field(default_factory=dict)
    semantic_kind: str = "diagram"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompiledSurface:
    frame: TactileFrame
    mode: str
    title: str
    summary: str
    regions: list[dict[str, Any]] = field(default_factory=list)
    region_map: list[int] = field(default_factory=lambda: [0] * PIN_COUNT)
    context: dict[str, str] = field(default_factory=dict)
    scene: TactileScene | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.region_map) != PIN_COUNT:
            raise ValueError(f"region_map must contain {PIN_COUNT} values")

    def to_dict(self) -> dict[str, Any]:
        active = sum(value > 0 for value in self.frame.pins)
        return {
            "device_id": DEVICE_ID,
            "mode": self.mode,
            "title": self.title,
            "summary": self.summary,
            "frame": self.frame.to_dict(),
            "regions": self.regions,
            "region_map": self.region_map,
            "context": self.context,
            "scene": self.scene.to_dict() if self.scene else None,
            "warnings": self.warnings,
            "metrics": {
                "pin_count": PIN_COUNT,
                "active_pins": active,
                "active_ratio": round(active / PIN_COUNT, 4),
                "level_counts": {
                    str(level): self.frame.pins.count(level) for level in range(LEVELS)
                },
            },
        }
