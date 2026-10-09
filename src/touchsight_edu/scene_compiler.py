from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contract import COLS, ROWS, CompiledSurface, TactileFrame, TactileScene


def _empty() -> list[list[int]]:
    return [[0 for _ in range(COLS)] for _ in range(ROWS)]


def _set(
    heights: list[list[int]],
    regions: list[list[int]],
    x: int,
    y: int,
    height: int,
    region_id: int,
) -> None:
    if 0 <= x < COLS and 0 <= y < ROWS and height >= heights[y][x]:
        heights[y][x] = max(0, min(3, int(height)))
        regions[y][x] = max(0, int(region_id)) if height else 0


def _draw_line(
    heights: list[list[int]],
    regions: list[list[int]],
    start: tuple[int, int],
    end: tuple[int, int],
    height: int,
    region_id: int,
) -> None:
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    step_x = 1 if x0 < x1 else -1
    step_y = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        _set(heights, regions, x0, y0, height, region_id)
        if (x0, y0) == (x1, y1):
            break
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += step_x
        if doubled <= dx:
            error += dx
            y0 += step_y


class SceneCompiler:
    """Mechanically rasterize an AI-planned scene into the current tactile matrix."""

    @staticmethod
    def _viewport(scene: TactileScene) -> tuple[float, float, float, float]:
        viewport = scene.viewport
        x_min = float(viewport.get("x_min", -3))
        x_max = float(viewport.get("x_max", 3))
        y_min = float(viewport.get("y_min", -1))
        y_max = float(viewport.get("y_max", 8))
        if x_max <= x_min or y_max <= y_min:
            raise ValueError("scene viewport is invalid")
        return x_min, x_max, y_min, y_max

    @staticmethod
    def _project(
        x: float,
        y: float,
        viewport: tuple[float, float, float, float],
    ) -> tuple[int, int]:
        x_min, x_max, y_min, y_max = viewport
        col = round((x - x_min) / (x_max - x_min) * (COLS - 1))
        row = round((y_max - y) / (y_max - y_min) * (ROWS - 1))
        return col, row

    def compile(self, scene: TactileScene, *, frame_id: int, warnings: list[str] | None = None) -> CompiledSurface:
        heights = _empty()
        region_grid = _empty()
        viewport = self._viewport(scene)
        for primitive in scene.primitives:
            self._render_primitive(primitive, viewport, heights, region_grid)
        frame = TactileFrame.from_rows(heights, frame_id=frame_id)
        return CompiledSurface(
            frame=frame,
            mode="ai",
            title=scene.title,
            summary=scene.summary,
            regions=deepcopy(scene.regions),
            region_map=[value for row in region_grid for value in row],
            context=deepcopy(scene.context),
            scene=deepcopy(scene),
            warnings=list(warnings or []),
        )

    def _render_primitive(
        self,
        primitive: dict[str, Any],
        viewport: tuple[float, float, float, float],
        heights: list[list[int]],
        regions: list[list[int]],
    ) -> None:
        kind = str(primitive.get("type", ""))
        height = max(0, min(3, int(primitive.get("height", 1))))
        region_id = max(0, int(primitive.get("region_id", 0)))
        x_min, x_max, y_min, y_max = viewport

        if kind == "axes":
            if y_min <= 0 <= y_max:
                _draw_line(
                    heights, regions,
                    self._project(x_min, 0, viewport),
                    self._project(x_max, 0, viewport),
                    height, region_id,
                )
            if x_min <= 0 <= x_max:
                _draw_line(
                    heights, regions,
                    self._project(0, y_min, viewport),
                    self._project(0, y_max, viewport),
                    height, region_id,
                )
            return

        if kind == "quadratic":
            a = float(primitive.get("a", 1))
            b = float(primitive.get("b", 0))
            c = float(primitive.get("c", 0))
            sample_min = max(x_min, float(primitive.get("x_min", x_min)))
            sample_max = min(x_max, float(primitive.get("x_max", x_max)))
            # Quantize once per physical column. Dense pre-quantization samples can
            # paint several neighbouring rows in one column and make a thin curve
            # look like a block. Adjacent column samples form a clean 1-pin path.
            previous: tuple[int, int] | None = None
            previous_col: int | None = None
            for col in range(COLS):
                x = x_min + (x_max - x_min) * col / (COLS - 1)
                if not sample_min <= x <= sample_max:
                    previous = None
                    previous_col = None
                    continue
                y = a * x * x + b * x + c
                if not y_min <= y <= y_max:
                    previous = None
                    previous_col = None
                    continue
                current = (col, self._project(x, y, viewport)[1])
                if previous is not None and previous_col == col - 1:
                    _draw_line(heights, regions, previous, current, height, region_id)
                else:
                    _set(heights, regions, current[0], current[1], height, region_id)
                previous = current
                previous_col = col
            return

        if kind in {"polyline", "polygon"}:
            raw_points = primitive.get("points")
            if not isinstance(raw_points, list) or len(raw_points) < 2:
                return
            points = [
                self._project(float(point[0]), float(point[1]), viewport)
                for point in raw_points
                if isinstance(point, list) and len(point) == 2
            ]
            pairs = list(zip(points, points[1:]))
            if kind == "polygon" and len(points) >= 3:
                pairs.append((points[-1], points[0]))
            for start, end in pairs:
                _draw_line(heights, regions, start, end, height, region_id)
            return

        if kind == "point":
            x, y = self._project(float(primitive.get("x", 0)), float(primitive.get("y", 0)), viewport)
            radius = max(0, min(1, int(primitive.get("radius", 0))))
            for row in range(y - radius, y + radius + 1):
                for col in range(x - radius, x + radius + 1):
                    _set(heights, regions, col, row, height, region_id)
