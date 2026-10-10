from __future__ import annotations

from copy import deepcopy
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .contract import TactileScene
from .direct_compiler import decode_image_data_url


SUPPORTED_PRIMITIVES = {"axes", "quadratic", "polyline", "polygon", "point"}

SYSTEM_PROMPT = """你是盲校 STEM 教师和触觉图规划器。根据学科、学习目标、教学内容、教师指令以及可选图片，决定学生现在应该摸到什么。
你不直接输出像素矩阵，而是输出一个可被确定性编译器栅格化的 TactileScene。目标设备是40列×50行、H0-H3四级高度；应保留教学所需结构，同时删掉装饰和题干文字，不生成盲文，不让图形粘成实心块。
如果输入是题目截图，必须区分“教学主体”和“题目呈现条件”。当要求提取主体时，只重建需要触摸的曲线或几何轮廓；公式、题干、选项、坐标文字、刻度和坐标轴都不得进入触觉图。不要把整张截图当作图片描边。
只要提供了图片，当前图片就是唯一的视觉事实来源。不得复用示例、历史图片或文字中提到但图片里没有出现的图形；图片与文字冲突时，以图片为准。如果无法从图片确认具体公式，就用 polyline 重建可见主体轮廓，不得猜成抛物线。

只允许这些 primitive：
- {"type":"axes","height":1..3,"region_id":正整数}
- {"type":"quadratic","a":数字,"b":数字,"c":数字,"x_min":数字,"x_max":数字,"height":1..3,"region_id":正整数}
- {"type":"polyline"或"polygon","points":[[x,y],...],"height":1..3,"region_id":正整数}
- {"type":"point","x":数字,"y":数字,"radius":0或1,"height":1..3,"region_id":正整数}

坐标使用数学坐标，viewport 给出 x_min/x_max/y_min/y_max。只输出单个 JSON object：
{"title":"...","summary":"适合朗读的简短说明","semantic_kind":"graph|geometry|diagram","viewport":{"x_min":-2.5,"x_max":2.5,"y_min":-0.5,"y_max":6},"primitives":[...],"regions":[{"id":1,"name":"...","description":"...","speech":"..."}]}
title、summary、regions 中的 name、description、speech 必须使用简体中文。
每个 primitive 的 region_id 必须在 regions 定义。优先用H3表示当前重点、H2表示对比对象、H1表示辅助结构。"""


SCENE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "tactile_scene",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "semantic_kind": {
                    "type": "string",
                    "enum": ["graph", "geometry", "diagram"],
                },
                "viewport": {
                    "type": "object",
                    "properties": {
                        "x_min": {"type": "number"},
                        "x_max": {"type": "number"},
                        "y_min": {"type": "number"},
                        "y_max": {"type": "number"},
                    },
                    "required": ["x_min", "x_max", "y_min", "y_max"],
                    "additionalProperties": False,
                },
                "primitives": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["axes", "quadratic", "polyline", "polygon", "point"],
                            },
                            "height": {"type": "integer", "enum": [1, 2, 3]},
                            "region_id": {"type": "integer"},
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                            "c": {"type": "number"},
                            "x_min": {"type": "number"},
                            "x_max": {"type": "number"},
                            "points": {
                                "type": "array",
                                "items": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "minItems": 2,
                                    "maxItems": 2,
                                },
                            },
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "radius": {"type": "integer", "enum": [0, 1]},
                        },
                        "required": ["type", "height", "region_id"],
                        "additionalProperties": False,
                    },
                },
                "regions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "speech": {"type": "string"},
                        },
                        "required": ["id", "name", "description", "speech"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "title", "summary", "semantic_kind", "viewport", "primitives", "regions"
            ],
            "additionalProperties": False,
        },
    },
}


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.I)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    candidates = []
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(cleaned, index)
            if isinstance(value, dict):
                candidates.append((end - index, value))
        except json.JSONDecodeError:
            continue
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    raise ValueError("AI 没有返回完整的场景 JSON")


def validate_scene_payload(payload: dict[str, Any], context: dict[str, str]) -> TactileScene:
    primitives = payload.get("primitives")
    regions = payload.get("regions")
    viewport = payload.get("viewport")
    if not isinstance(primitives, list) or not primitives:
        raise ValueError("AI 场景缺少 primitives")
    if not isinstance(regions, list) or not regions:
        raise ValueError("AI 场景缺少 regions")
    if not isinstance(viewport, dict):
        raise ValueError("AI 场景缺少 viewport")
    known_ids = set()
    clean_regions = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        region_id = int(region.get("id", 0))
        if region_id <= 0 or region_id in known_ids:
            raise ValueError("AI 场景区域 id 无效或重复")
        known_ids.add(region_id)
        clean_regions.append({
            "id": region_id,
            "name": str(region.get("name") or f"区域 {region_id}"),
            "description": str(region.get("description") or ""),
            "speech": str(region.get("speech") or region.get("description") or ""),
        })
    clean_primitives = []
    for primitive in primitives:
        if not isinstance(primitive, dict) or primitive.get("type") not in SUPPORTED_PRIMITIVES:
            raise ValueError("AI 使用了不支持的图元")
        region_id = int(primitive.get("region_id", 0))
        if region_id not in known_ids:
            raise ValueError("AI 图元引用了未定义区域")
        height = int(primitive.get("height", 0))
        if height not in {1, 2, 3}:
            raise ValueError("AI 图元高度必须是 H1、H2 或 H3")
        clean_primitive = deepcopy(primitive)
        if clean_primitive.get("type") == "axes":
            clean_primitive["height"] = 1
        clean_primitives.append(clean_primitive)
    clean_viewport = {
        key: float(viewport[key])
        for key in ("x_min", "x_max", "y_min", "y_max")
        if key in viewport
    }
    if len(clean_viewport) != 4:
        raise ValueError("AI viewport 字段不完整")
    scene = TactileScene(
        title=str(payload.get("title") or "AI 教学触觉图"),
        summary=str(payload.get("summary") or "已按本节课目标生成触觉结构。"),
        primitives=clean_primitives,
        regions=clean_regions,
        viewport=clean_viewport,
        context=context,
        semantic_kind=str(payload.get("semantic_kind") or "diagram"),
    )
    if context.get("render_intent") == "extract_subject":
        excluded_region_ids = {
            int(region.get("id", 0))
            for region in clean_regions
            if any(
                keyword in (
                    str(region.get("name") or "")
                    + " "
                    + str(region.get("description") or "")
                ).lower()
                for keyword in (
                    "坐标轴", "x轴", "y轴", "横轴", "纵轴", "刻度", "标签",
                    "公式", "题干", "文字", "数字", "箭头", "axis", "label",
                    "tick", "formula", "text", "number", "arrow",
                )
            )
        }
        scene.primitives = [
            primitive for primitive in scene.primitives
            if primitive.get("type") != "axes"
            and int(primitive.get("region_id", 0)) not in excluded_region_ids
        ]
        if not scene.primitives:
            raise ValueError("视觉模型没有提取出可显示的主体图形")
        retained_ids = {int(item.get("region_id", 0)) for item in scene.primitives}
        scene.regions = [item for item in scene.regions if int(item.get("id", 0)) in retained_ids]
    return scene


class CompatibleVisionPlanner:
    def __init__(self, env_prefix: str = "AI") -> None:
        prefix = env_prefix.strip().upper() or "AI"
        default_base = "https://dashscope.aliyuncs.com/compatible-mode/v1" if prefix == "AI" else ""
        default_model = "qwen-vl-max" if prefix == "AI" else ""
        self.env_prefix = prefix
        self.api_key = os.getenv(f"{prefix}_API_KEY", "").strip()
        self.base_url = os.getenv(f"{prefix}_BASE_URL", default_base).rstrip("/")
        self.model = os.getenv(f"{prefix}_MODEL", default_model).strip()
        self.timeout = float(os.getenv(f"{prefix}_TIMEOUT_SECONDS", "90"))
        self.supports_vision = os.getenv(f"{prefix}_SUPPORTS_VISION", "true").strip().lower() in {
            "1", "true", "yes", "on",
        }

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.api_key),
            "provider": (
                "openai-compatible-multimodal"
                if self.supports_vision
                else "openai-compatible-text"
            ),
            "model": self.model,
            "base_url": self.base_url,
            "supports_vision": self.supports_vision,
            "vision_ready": bool(self.api_key) and self.supports_vision,
            "slot": self.env_prefix.lower(),
        }

    def plan(
        self,
        context: dict[str, str],
        image_data_url: str | None,
    ) -> TactileScene:
        if not self.api_key:
            raise RuntimeError(f"{self.env_prefix}_API_KEY 未配置")
        if not self.base_url or not self.model:
            raise RuntimeError(f"{self.env_prefix} 模型地址或模型标识未配置")
        user_text = (
            f"学科：{context.get('subject') or '未填写'}\n"
            f"学习目标：{context.get('learning_goal') or '未填写'}\n"
            f"教学内容/题目：{context.get('lesson_content') or '未填写'}\n"
            f"教师指令：{context.get('instruction') or '请自主简化'}"
        )
        if context.get("render_intent") == "extract_subject":
            user_text += (
                "\n输出模式：AI看图提取主体。必须只观察本次随消息附带的图片，"
                "不得复用之前图片或被文字暗示但当前图片中不存在的图形。"
                "只重建教师要学生触摸的主要曲线或轮廓；删除题干、公式、坐标轴、"
                "刻度、数字、字母、箭头和其他条件信息。无法确定公式时，用 polyline"
                "忠实近似当前图片里实际可见的主体，不得猜测函数类型。"
            )
        if image_data_url:
            decode_image_data_url(image_data_url)
        if image_data_url and self.supports_vision:
            content: str | list[dict[str, Any]] = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
        else:
            if image_data_url:
                user_text += (
                    "\n能力边界：当前模型不支持图片输入，不得声称看过上传图片。"
                    "仅根据以上文字规划触觉场景。"
                )
            content = user_text
        request_payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        }
        # Do not require response_format here. Free OpenRouter vision endpoints
        # do not consistently advertise JSON-schema support, and local OpenAI-
        # compatible servers vary as well. The prompt requires one JSON object;
        # _extract_json plus validate_scene_payload enforce the contract.
        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:600]
            raise RuntimeError(f"AI 服务 HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"AI 服务请求失败：{exc}") from exc
        try:
            content_text = result["choices"][0]["message"]["content"]
            if isinstance(content_text, list):
                content_text = "\n".join(
                    str(item.get("text", "")) for item in content_text if isinstance(item, dict)
                )
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("AI 服务返回格式不符合预期") from exc
        scene = validate_scene_payload(_extract_json(str(content_text)), context)
        resolved_model = str(result.get("model") or self.model)
        scene.context.update({
            "generation_source": "model",
            "generation_provider": self.env_prefix.lower(),
            "generation_model": resolved_model,
            "generation_input": "image+text" if image_data_url and self.supports_vision else "text",
        })
        return scene


_QUADRATIC_RE = re.compile(
    r"y\s*[=＝]\s*([+-]?\s*(?:\d+(?:\.\d+)?\s*\*?\s*)?)x\s*(?:\^\s*2|\*\*\s*2|²)",
    re.I,
)


def _quadratic_coefficients(text: str) -> list[float]:
    values = []
    for match in _QUADRATIC_RE.finditer(text.replace("−", "-")):
        raw = match.group(1).replace(" ", "").replace("*", "")
        if raw in {"", "+"}:
            value = 1.0
        elif raw == "-":
            value = -1.0
        else:
            value = float(raw)
        if value not in values:
            values.append(value)
    return values


def fallback_scene(context: dict[str, str]) -> TactileScene:
    text = " ".join(context.values())
    coefficients = _quadratic_coefficients(text)
    instruction = context.get("instruction", "")
    requested_only = _quadratic_coefficients(instruction)
    if requested_only and any(word in instruction for word in ("只显示", "先只", "只保留")):
        coefficients = requested_only
    if not coefficients and any(word in text for word in ("二次函数", "抛物线", "函数图")):
        coefficients = [1.0]
    if coefficients:
        coefficients = coefficients[:2]
        nonzero = [abs(value) for value in coefficients if value]
        shallowest = min(nonzero, default=1.0)
        vertical_limit = 6.0
        x_half = min(3.0, max(1.5, (vertical_limit / shallowest) ** 0.5))
        if all(value >= 0 for value in coefficients):
            viewport = {
                "x_min": -x_half, "x_max": x_half,
                "y_min": -0.5, "y_max": vertical_limit,
            }
        elif all(value <= 0 for value in coefficients):
            viewport = {
                "x_min": -x_half, "x_max": x_half,
                "y_min": -vertical_limit, "y_max": 0.5,
            }
        else:
            viewport = {
                "x_min": -x_half, "x_max": x_half,
                "y_min": -vertical_limit, "y_max": vertical_limit,
            }
        primitives = [{"type": "axes", "height": 1, "region_id": 1}]
        regions = [{
            "id": 1,
            "name": "坐标轴",
            "description": "H1 横轴与纵轴",
            "speech": "较低的十字结构是坐标轴。",
        }]
        curve_names = []
        for index, coefficient in enumerate(coefficients):
            region_id = index + 2
            height = 3 if index == 0 else 2
            coefficient_text = "" if coefficient == 1 else "-" if coefficient == -1 else f"{coefficient:g}"
            name = f"y={coefficient_text}x²"
            curve_names.append(name)
            primitives.append({
                "type": "quadratic",
                "a": coefficient,
                "b": 0,
                "c": 0,
                "x_min": -3,
                "x_max": 3,
                "height": height,
                "region_id": region_id,
            })
            regions.append({
                "id": region_id,
                "name": name,
                "description": f"H{height} 二次函数曲线",
                "speech": f"这一层表示 {name}。",
            })
        summary = "H1 是坐标轴；" + "，".join(
            f"H{3 if index == 0 else 2} 是 {name}" for index, name in enumerate(curve_names)
        ) + "。"
        return TactileScene(
            title="二次函数触觉图" if len(curve_names) == 1 else "二次函数对比",
            summary=summary,
            primitives=primitives,
            regions=regions,
            viewport=viewport,
            context=context,
            semantic_kind="graph",
        )

    if any(word in text for word in ("三角形", "三角", "几何")):
        return TactileScene(
            title="三角形结构",
            summary="H3 连续轮廓表示三条边，转折处是三个顶点。",
            primitives=[
                {"type": "polygon", "points": [[-2, 0], [2, 0], [0, 4]], "height": 3, "region_id": 1},
                {"type": "point", "x": -2, "y": 0, "height": 3, "region_id": 2},
                {"type": "point", "x": 2, "y": 0, "height": 3, "region_id": 2},
                {"type": "point", "x": 0, "y": 4, "height": 3, "region_id": 2},
            ],
            regions=[
                {"id": 1, "name": "三条边", "description": "三角形外轮廓", "speech": "连续轮廓组成三角形的三条边。"},
                {"id": 2, "name": "三个顶点", "description": "三条边的转折点", "speech": "轮廓转折位置是三个顶点。"},
            ],
            viewport={"x_min": -3, "x_max": 3, "y_min": -1, "y_max": 5},
            context=context,
            semantic_kind="geometry",
        )
    raise ValueError("当前离线演示先支持二次函数和三角形；配置 AI 模型后可理解更多 STEM 图形。")


class TeachingPlanner:
    def __init__(
        self,
        provider: CompatibleVisionPlanner | None = None,
        vision_provider: CompatibleVisionPlanner | None = None,
    ) -> None:
        self.provider = provider or CompatibleVisionPlanner()
        self.vision_provider = vision_provider or CompatibleVisionPlanner("VISION")

    def _ready_vision_provider(self) -> CompatibleVisionPlanner | None:
        if self.vision_provider.status()["vision_ready"]:
            return self.vision_provider
        if self.provider.status()["vision_ready"]:
            return self.provider
        return None

    def status(self) -> dict[str, Any]:
        primary = self.provider.status()
        vision = self.vision_provider.status()
        active_vision = self._ready_vision_provider()
        return {
            **primary,
            "vision_ready": active_vision is not None,
            "vision_model": active_vision.model if active_vision else vision.get("model"),
            "vision": vision,
            "offline_demo": ["quadratic", "triangle"],
        }

    def plan(
        self,
        *,
        subject: str,
        learning_goal: str,
        lesson_content: str,
        instruction: str,
        image_data_url: str | None,
        render_intent: str = "",
    ) -> tuple[TactileScene, list[str]]:
        context = {
            "subject": subject.strip(),
            "learning_goal": learning_goal.strip(),
            "lesson_content": lesson_content.strip(),
            "instruction": instruction.strip(),
            "render_intent": render_intent.strip(),
        }
        if not any(context.values()) and not image_data_url:
            raise ValueError("请填写教学内容或上传教学图片")
        extracting_subject = context["render_intent"] == "extract_subject"
        selected_provider = self._ready_vision_provider() if extracting_subject else self.provider
        if extracting_subject and selected_provider is None:
            raise RuntimeError("视觉模型尚未配置")
        if selected_provider.status()["configured"]:
            try:
                scene = selected_provider.plan(context, image_data_url)
                warnings = []
                if image_data_url and not selected_provider.status()["supports_vision"]:
                    warnings.append(
                        f"模型 {selected_provider.model} 仅处理文字；上传图片未发送给模型。"
                        "本帧依据学科、目标、题目和教师指令生成。"
                    )
                return scene, warnings
            except (RuntimeError, ValueError) as exc:
                action = "视觉主体提取" if extracting_subject else "AI 教学规划"
                raise RuntimeError(f"{action}失败：{exc}。请检查模型服务后重试，本次没有使用离线模板替代。") from exc
        scene = fallback_scene(context)
        scene.context.update({
            "generation_source": "offline-compiler",
            "generation_provider": "local-rules",
            "generation_model": "none",
            "generation_input": "text",
        })
        return scene, [
            "当前未配置在线视觉模型；本次使用可复现的函数/几何教学编译器，不冒充 AI 识别结果。"
        ]


def revise_scene(scene: TactileScene, command: str) -> TactileScene:
    text = command.strip()
    if not text:
        raise ValueError("请输入下一步教学指令")
    revised = deepcopy(scene)
    quadratics = [item for item in revised.primitives if item.get("type") == "quadratic"]

    added = _quadratic_coefficients(text)
    if added and any(word in text for word in ("加入", "增加", "再", "对比", "添加")):
        existing = {float(item.get("a", 1)) for item in quadratics}
        for coefficient in added:
            if coefficient in existing:
                continue
            region_id = max((int(region["id"]) for region in revised.regions), default=0) + 1
            revised.primitives.append({
                "type": "quadratic", "a": coefficient, "b": 0, "c": 0,
                "x_min": revised.viewport["x_min"], "x_max": revised.viewport["x_max"],
                "height": 2, "region_id": region_id,
            })
            name = f"y={coefficient:g}x²"
            revised.regions.append({
                "id": region_id, "name": name, "description": "H2 对比曲线", "speech": f"H2 表示 {name}。"
            })
            existing.add(coefficient)
        revised.title = "二次函数对比"
        revised.summary = "H3 是原主曲线，H2 是新加入的对比曲线，H1 是坐标轴。"
        return revised

    if any(word in text for word in ("顶点", "放大", "局部")) and quadratics:
        primary = quadratics[0]
        a = float(primary.get("a", 1))
        b = float(primary.get("b", 0))
        c = float(primary.get("c", 0))
        vertex_x = -b / (2 * a) if a else 0
        vertex_y = a * vertex_x * vertex_x + b * vertex_x + c
        revised.viewport = {
            "x_min": vertex_x - 1.5,
            "x_max": vertex_x + 1.5,
            "y_min": vertex_y - 0.5,
            "y_max": vertex_y + 3.5,
        }
        region_id = max((int(region["id"]) for region in revised.regions), default=0) + 1
        revised.primitives.append({
            "type": "point", "x": vertex_x, "y": vertex_y,
            "radius": 0, "height": 3, "region_id": region_id,
        })
        revised.regions.append({
            "id": region_id, "name": "顶点", "description": "局部视图中心的函数顶点", "speech": "这里是放大后的函数顶点。"
        })
        revised.title = "顶点附近放大"
        revised.summary = "视口已缩小到顶点附近，便于触摸曲线的最低点和左右对称关系。"
        return revised

    if any(word in text for word in ("隐藏坐标轴", "去掉坐标轴", "只显示曲线")):
        revised.primitives = [item for item in revised.primitives if item.get("type") != "axes"]
        revised.summary = "坐标轴已隐藏，只保留需要比较的函数曲线。"
        return revised

    if any(word in text for word in ("突出", "更高", "强调")) and quadratics:
        quadratics[0]["height"] = 3
        revised.summary = "当前主曲线已提升到 H3，其他结构保持较低高度。"
        return revised

    if any(word in text for word in ("简化", "只保留", "去掉")):
        revised.primitives = [
            item for item in revised.primitives if item.get("type") in {"axes", "quadratic", "polygon"}
        ]
        revised.summary = "已移除附加点和次要标记，只保留核心几何结构。"
        return revised
    raise ValueError("当前演示支持：加入 y=2x²、放大顶点、隐藏坐标轴、突出主曲线、进一步简化。")
