from __future__ import annotations

import base64
import binascii
import hashlib
import io
from statistics import median

from PIL import Image, ImageOps

from .contract import COLS, ROWS, CompiledSurface, TactileFrame


MAX_IMAGE_BYTES = 12 * 1024 * 1024
SUPERSAMPLE = 10


class ImageDecodeError(ValueError):
    pass


def decode_image_data_url(image_data_url: str) -> Image.Image:
    if not isinstance(image_data_url, str) or not image_data_url.startswith("data:image/"):
        raise ImageDecodeError("请上传 PNG、JPEG 或 WebP 图片")
    header, separator, encoded = image_data_url.partition(",")
    if not separator or ";base64" not in header[:128]:
        raise ImageDecodeError("图片必须使用 base64 data URL")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ImageDecodeError("图片数据无法解码") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ImageDecodeError("图片为空或超过 12 MB")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            rgba = image.convert("RGBA")
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            return Image.alpha_composite(white, rgba).convert("L")
    except (OSError, ValueError) as exc:
        raise ImageDecodeError("图片格式无法读取") from exc


def _fit_box(width: int, height: int) -> dict[str, int]:
    scale = min(COLS / width, ROWS / height)
    fit_width = max(1, min(COLS, round(width * scale)))
    fit_height = max(1, min(ROWS, round(height * scale)))
    return {
        "x": (COLS - fit_width) // 2,
        "y": (ROWS - fit_height) // 2,
        "width": fit_width,
        "height": fit_height,
    }


def _border_background(image: Image.Image) -> float:
    width, height = image.size
    pixels = image.load()
    border = []
    for x in range(width):
        border.extend((int(pixels[x, 0]), int(pixels[x, height - 1])))
    for y in range(height):
        border.extend((int(pixels[0, y]), int(pixels[width - 1, y])))
    return float(median(border)) if border else 255.0


def _cell_level(values: list[int], background: float) -> int:
    contrast = sorted(abs(value - background) / 255.0 for value in values)
    mean_signal = sum(contrast) / len(contrast)
    p85 = contrast[min(len(contrast) - 1, int(len(contrast) * 0.85))]
    peak = contrast[-1]
    signal = max(mean_signal, 0.76 * p85, 0.55 * peak)
    if signal >= 0.54:
        return 3
    if signal >= 0.29:
        return 2
    if signal >= 0.105:
        return 1
    return 0


def image_to_height_rows(image: Image.Image) -> tuple[list[list[int]], dict[str, int]]:
    """Contain the complete image in the hardware grid without crop or stretch."""
    if image.width <= 0 or image.height <= 0:
        raise ImageDecodeError("图片尺寸无效")
    box = _fit_box(image.width, image.height)
    fitted = image.resize(
        (box["width"] * SUPERSAMPLE, box["height"] * SUPERSAMPLE),
        Image.Resampling.LANCZOS,
    )
    background = _border_background(fitted)
    pixels = fitted.load()
    rows = [[0 for _ in range(COLS)] for _ in range(ROWS)]
    for cell_y in range(box["height"]):
        for cell_x in range(box["width"]):
            values = [
                int(pixels[x, y])
                for y in range(cell_y * SUPERSAMPLE, (cell_y + 1) * SUPERSAMPLE)
                for x in range(cell_x * SUPERSAMPLE, (cell_x + 1) * SUPERSAMPLE)
            ]
            rows[box["y"] + cell_y][box["x"] + cell_x] = _cell_level(values, background)
    return rows, box


def compile_direct(image_data_url: str, *, frame_id: int) -> CompiledSurface:
    image = decode_image_data_url(image_data_url)
    rows, content_box = image_to_height_rows(image)
    frame = TactileFrame.from_rows(
        rows,
        frame_id=frame_id,
        content_box=content_box,
        source_size={"width": image.width, "height": image.height},
    )
    region_map = [1 if value else 0 for value in frame.pins]
    return CompiledSurface(
        frame=frame,
        mode="direct",
        title="原图直转",
        summary=f"整张图片按原比例完整放入 {COLS}×{ROWS} 点阵，没有裁剪或拉伸。颜色差异被确定性量化为 H0–H3。",
        regions=[{
            "id": 1,
            "name": "整图点阵",
            "description": "原图的完整高密度点阵映射",
            "speech": "这是整张图片按比例缩放后的触觉点阵。",
        }],
        region_map=region_map,
        context={"source_fingerprint": hashlib.sha256(image_data_url.encode("utf-8")).hexdigest()[:12]},
    )
