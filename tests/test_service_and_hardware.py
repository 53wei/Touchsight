import base64
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from touchsight_edu.service import TouchSightService


def teaching_image(kind: str) -> str:
    image = Image.new("RGB", (120, 120), "white")
    draw = ImageDraw.Draw(image)
    if kind == "diagonal":
        draw.line((12, 108, 108, 12), fill="black", width=7)
    else:
        draw.ellipse((22, 22, 98, 98), outline="black", width=7)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


class ServiceAndHardwareTests(unittest.TestCase):
    def test_text_only_teaching_flow_revises_and_exports(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            service = TouchSightService(Path(directory))
            first = service.compile_teaching({
                "subject": "数学",
                "learning_goal": "比较 y=x² 与 y=2x² 的开口宽度",
                "lesson_content": "y=x² 与 y=2x²",
                "instruction": "先只显示坐标轴和 y=x²",
            })
            self.assertEqual(first["surface"]["mode"], "ai")
            self.assertEqual(first["surface"]["frame"]["frame_id"], 1)
            second = service.revise("加入 y=2x² 对比")
            self.assertEqual(second["surface"]["frame"]["frame_id"], 2)
            self.assertEqual(second["surface"]["title"], "二次函数对比")
            exported = service.publish_hardware()
            self.assertFalse(exported["physical"])
            self.assertEqual(exported["wire_bytes"], 520)
            self.assertTrue(Path(exported["json_path"]).is_file())
            self.assertTrue(Path(exported["wire_path"]).is_file())

    def test_ai_entry_direct_image_uses_new_upload_and_persists_history(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            service = TouchSightService(Path(directory))
            payload = {
                "subject": "数学",
                "learning_goal": "识别图形轮廓",
                "lesson_content": "观察上传图形",
                "instruction": "保留主要轮廓",
            }
            first = service.compile_teaching_image({
                **payload, "image_data_url": teaching_image("diagonal"),
            })["surface"]
            second = service.compile_teaching_image({
                **payload, "image_data_url": teaching_image("circle"),
            })["surface"]
            self.assertEqual(first["mode"], "ai-image")
            self.assertNotEqual(first["context"]["source_fingerprint"], second["context"]["source_fingerprint"])
            self.assertNotEqual(first["frame"]["pins"], second["frame"]["pins"])
            history = service.list_history()["items"]
            self.assertEqual(len(history), 2)
            older = history[1]
            restored = service.restore_history(older["history_id"])["surface"]
            self.assertEqual(restored["frame"]["pins"], first["frame"]["pins"])
            self.assertEqual(restored["frame"]["frame_id"], 3)

    def test_older_generation_cannot_replace_new_upload(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            service = TouchSightService(Path(directory))
            newer = service.compile_direct(teaching_image("circle"), {
                "generation_id": 200,
                "source_name": "new-circle.png",
            })
            stale = service.compile_direct(teaching_image("diagonal"), {
                "generation_id": 100,
                "source_name": "old-diagonal.png",
            })
            self.assertTrue(stale["discarded"])
            self.assertEqual(
                stale["surface"]["context"]["source_fingerprint"],
                newer["surface"]["context"]["source_fingerprint"],
            )
            self.assertEqual(stale["surface"]["context"]["source_name"], "new-circle.png")

    def test_vision_extraction_requires_a_configured_vision_model(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            service = TouchSightService(Path(directory))
            with self.assertRaisesRegex(ValueError, "视觉模型"):
                service.compile_teaching({
                    "subject": "数学",
                    "learning_goal": "只触摸抛物线",
                    "lesson_content": "从题图提取抛物线",
                    "instruction": "去掉坐标轴和公式",
                    "render_intent": "extract_subject",
                    "image_data_url": teaching_image("diagonal"),
                })


if __name__ == "__main__":
    unittest.main()
