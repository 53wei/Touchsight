from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import threading

from .ai_planner import TeachingPlanner, revise_scene
from .contract import COLS, ROWS, CompiledSurface, TactileFrame, TactileScene
from .direct_compiler import compile_direct
from .hardware import HardwareManager, SimulatorAdapter
from .history import HistoryStore
from .scene_compiler import SceneCompiler


class TouchSightService:
    def __init__(self, output_dir: Path) -> None:
        self._lock = threading.RLock()
        self._frame_id = 0
        self._revision = 0
        self.planner = TeachingPlanner()
        self.scene_compiler = SceneCompiler()
        self.simulator = SimulatorAdapter()
        self.hardware = HardwareManager(output_dir / "hardware")
        self.history = HistoryStore(output_dir / "history")
        self.surface = CompiledSurface(
            frame=TactileFrame(pins=tuple([0] * (COLS * ROWS))),
            mode="idle",
            title="等待生成",
            summary="入口一把已有图片直接转换成点阵；入口二根据学科和学习目标生成可动态调整的教学触觉图。",
        )

    def _next_frame_id(self) -> int:
        with self._lock:
            self._frame_id += 1
            return self._frame_id

    def _publish_surface(self, surface: CompiledSurface, *, save_history: bool = True) -> dict:
        self.simulator.publish(surface.frame)
        if save_history:
            try:
                self.history.save(surface)
            except OSError as exc:
                surface.warnings.append(f"历史记录保存失败：{exc}")
        with self._lock:
            self.surface = surface
            self._revision += 1
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return {"revision": self._revision, "surface": self.surface.to_dict()}

    def status(self) -> dict:
        return {
            "ok": True,
            "product": "TouchSight EDU",
            "stage": f"software simulator; physical {COLS}x{ROWS} array not claimed complete",
            "contract": {
                "cols": COLS,
                "rows": ROWS,
                "pin_count": COLS * ROWS,
                "levels": 4,
                "height_mm": [0, 2, 4, 6],
            },
            "ai": self.planner.status(),
            "adapters": {
                "simulator": self.simulator.status(),
                **self.hardware.status(),
            },
        }

    def compile_direct(self, image_data_url: str) -> dict:
        return self._publish_surface(
            compile_direct(image_data_url, frame_id=self._next_frame_id())
        )

    def compile_teaching(self, payload: dict) -> dict:
        image_data_url = str(payload.get("image_data_url") or "").strip() or None
        render_intent = str(payload.get("render_intent") or "").strip()
        if render_intent == "extract_subject":
            if not image_data_url:
                raise ValueError("请先上传需要提取主体的题目图片")
            if not self.planner.status().get("vision_ready"):
                raise ValueError("当前本地千问是文字模型；请加载视觉模型后再使用 AI 看图提取主体")
        scene, warnings = self.planner.plan(
            subject=str(payload.get("subject") or ""),
            learning_goal=str(payload.get("learning_goal") or ""),
            lesson_content=str(payload.get("lesson_content") or ""),
            instruction=str(payload.get("instruction") or ""),
            image_data_url=image_data_url,
            render_intent=render_intent,
        )
        surface = self.scene_compiler.compile(
            scene,
            frame_id=self._next_frame_id(),
            warnings=warnings,
        )
        return self._publish_surface(surface)

    def compile_teaching_image(self, payload: dict) -> dict:
        image_data_url = str(payload.get("image_data_url") or "").strip()
        if not image_data_url:
            raise ValueError("请先上传要直接转换的教学图片")
        direct = compile_direct(image_data_url, frame_id=self._next_frame_id())
        supports_vision = bool(self.planner.status().get("supports_vision"))
        planner_context = {
            "subject": str(payload.get("subject") or "").strip(),
            "learning_goal": str(payload.get("learning_goal") or "").strip(),
            "lesson_content": str(payload.get("lesson_content") or "").strip(),
            "instruction": str(payload.get("instruction") or "").strip(),
        }
        try:
            scene, planner_warnings = self.planner.plan(
                **planner_context,
                image_data_url=image_data_url if supports_vision else None,
            )
        except ValueError as exc:
            if self.planner.status().get("configured"):
                raise
            goal = planner_context["learning_goal"] or planner_context["lesson_content"]
            scene = TactileScene(
                title="上传图片教学点阵",
                summary=(
                    f"本节课围绕“{goal}”触摸本次上传图片的主要结构。"
                    if goal else "触摸本次上传图片转换得到的主要结构。"
                ),
                primitives=[],
                regions=[],
                context={
                    **planner_context,
                    "generation_source": "offline-compiler",
                    "generation_provider": "local-rules",
                    "generation_model": "none",
                    "generation_input": "text",
                },
                semantic_kind="image",
            )
            planner_warnings = [f"未配置模型，文字教学说明使用本地模板：{exc}"]
        context = {**scene.context, **direct.context}
        surface = CompiledSurface(
            frame=direct.frame,
            mode="ai-image",
            title=f"{scene.title} · 上传图片点阵",
            summary=(
                "上传图片已经直接转换为高密度点阵。"
                f"文字教学计划：{scene.summary}"
            ),
            regions=[
                {
                    "id": 1,
                    "name": "上传图片点阵",
                    "description": "由本次上传图片直接转换，不复用旧图片",
                    "speech": "这是本次上传图片直接转换成的触觉点阵。",
                },
                {
                    "id": 2,
                    "name": "文字教学计划",
                    "description": scene.summary,
                    "speech": scene.summary,
                },
            ],
            region_map=direct.region_map,
            context=context,
            scene=scene,
            warnings=[
                (
                    "图片点阵由本次上传文件直接转换；模型读取图片只用于生成教学计划。"
                    if supports_vision else
                    "图片点阵由本次上传文件直接转换；本地千问只根据文字生成教学计划，没有读取图片内容。"
                ),
                *planner_warnings,
            ],
        )
        return self._publish_surface(surface)

    def list_history(self) -> dict:
        return {"ok": True, "items": self.history.list()}

    def restore_history(self, history_id: str) -> dict:
        surface = self.history.restore(history_id, frame_id=self._next_frame_id())
        return self._publish_surface(surface, save_history=False)

    def revise(self, command: str) -> dict:
        with self._lock:
            current = deepcopy(self.surface)
        if current.mode != "ai" or current.scene is None:
            raise ValueError("请先从 AI 教学入口生成一张触觉图")
        scene = revise_scene(current.scene, command)
        surface = self.scene_compiler.compile(
            scene,
            frame_id=self._next_frame_id(),
            warnings=current.warnings,
        )
        return self._publish_surface(surface)

    def publish_hardware(self) -> dict:
        with self._lock:
            current = self.surface
        if current.mode == "idle":
            raise ValueError("请先生成一个触觉表面")
        return self.hardware.publish(current.frame)
