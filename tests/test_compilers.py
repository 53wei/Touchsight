import base64
import io
import unittest

from PIL import Image, ImageDraw

from touchsight_edu.ai_planner import TeachingPlanner, fallback_scene, revise_scene, validate_scene_payload
from touchsight_edu.contract import COLS, ROWS
from touchsight_edu.direct_compiler import compile_direct
from touchsight_edu.scene_compiler import SceneCompiler


def test_image_data_url(width=200, height=100):
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, width - 9, height - 9), outline="black", width=6)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


class CompilerTests(unittest.TestCase):
    def test_configured_model_failure_is_not_silently_replaced(self):
        class FailingProvider:
            model = "test-model"

            def status(self):
                return {"configured": True, "supports_vision": False, "vision_ready": False, "model": self.model}

            def plan(self, context, image_data_url):
                raise RuntimeError("model unavailable")

        planner = TeachingPlanner(provider=FailingProvider(), vision_provider=FailingProvider())
        with self.assertRaisesRegex(RuntimeError, "没有使用离线模板替代"):
            planner.plan(
                subject="数学",
                learning_goal="理解抛物线",
                lesson_content="y=x²",
                instruction="只保留曲线",
                image_data_url=None,
            )

    def test_extract_subject_removes_axes_from_ai_scene(self):
        scene = validate_scene_payload({
            "title": "抛物线主体",
            "summary": "只保留抛物线",
            "semantic_kind": "graph",
            "viewport": {"x_min": -3, "x_max": 3, "y_min": -1, "y_max": 8},
            "primitives": [
                {"type": "axes", "height": 1, "region_id": 1},
                {"type": "polyline", "points": [[-3, 0], [3, 0]], "height": 1, "region_id": 3},
                {"type": "quadratic", "a": 1, "b": 0, "c": 0, "x_min": -3, "x_max": 3, "height": 3, "region_id": 2},
            ],
            "regions": [
                {"id": 1, "name": "坐标轴"},
                {"id": 2, "name": "抛物线"},
                {"id": 3, "name": "X轴刻度与数字"},
            ],
        }, {"render_intent": "extract_subject"})
        self.assertEqual([item["type"] for item in scene.primitives], ["quadratic"])
        self.assertEqual([item["name"] for item in scene.regions], ["抛物线"])

    def test_direct_compiler_contains_whole_image_without_stretch(self):
        surface = compile_direct(test_image_data_url(), frame_id=1)
        self.assertEqual(surface.frame.content_box, {"x": 0, "y": 15, "width": 40, "height": 20})
        self.assertEqual(len(surface.frame.pins), 2000)
        self.assertTrue(any(surface.frame.pins))

    def test_teaching_scene_separates_ai_planning_from_matrix_compile(self):
        context = {
            "subject": "数学",
            "learning_goal": "比较 y=x² 与 y=2x²",
            "lesson_content": "y=x² 与 y=2x²",
            "instruction": "先只显示坐标轴和 y=x²",
        }
        scene = fallback_scene(context)
        self.assertEqual(sum(p["type"] == "quadratic" for p in scene.primitives), 1)
        self.assertEqual(scene.viewport["y_max"], 6.0)
        surface = SceneCompiler().compile(scene, frame_id=2)
        self.assertIn(3, surface.frame.pins)
        self.assertIn(1, surface.frame.pins)
        curve = [
            [surface.region_map[row * COLS + col] == 2 for col in range(COLS)]
            for row in range(ROWS)
        ]
        for row in range(ROWS - 1):
            for col in range(COLS - 1):
                self.assertFalse(
                    curve[row][col]
                    and curve[row][col + 1]
                    and curve[row + 1][col]
                    and curve[row + 1][col + 1],
                    "quadratic should not contain a 2x2 block",
                )

    def test_dynamic_command_adds_second_curve_and_focuses_vertex(self):
        scene = fallback_scene({
            "subject": "数学", "learning_goal": "二次函数", "lesson_content": "y=x²",
            "instruction": "只显示 y=x²",
        })
        compared = revise_scene(scene, "加入 y=2x² 对比")
        self.assertEqual(sum(p["type"] == "quadratic" for p in compared.primitives), 2)
        focused = revise_scene(compared, "放大顶点")
        self.assertEqual(focused.title, "顶点附近放大")
        self.assertLess(focused.viewport["x_max"] - focused.viewport["x_min"], 4)


if __name__ == "__main__":
    unittest.main()
