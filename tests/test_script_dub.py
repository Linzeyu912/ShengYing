# -*- coding: utf-8 -*-
import io
import unittest
import zipfile
from unittest.mock import patch

import numpy as np

from server import script_dub


SCRIPT_TEXT = """测试剧本
核心人物：周言（新任女老板）、王姐（九年老仓库内勤）
镜头 1
台词（周言，紧张）：先把门关上。（王姐，温柔）：好，你慢慢说。
镜头 2
字幕（居中白字）三天前
"""


def make_docx(text: str) -> bytes:
    paragraphs = "".join(
        f'<w:p><w:r><w:t>{line}</w:t></w:r></w:p>'
        for line in text.splitlines() if line
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document><w:body>{paragraphs}</w:body></w:document>")
    return stream.getvalue()


class ScriptUploadParsingTests(unittest.TestCase):
    def test_txt_upload_supports_utf8_bom(self):
        lines = script_dub.extract_script_text(
            ("\ufeff" + SCRIPT_TEXT).encode("utf-8"), "剧本.txt")
        self.assertEqual(lines[0], "测试剧本")

    def test_txt_upload_falls_back_to_gb18030(self):
        lines = script_dub.extract_script_bytes(
            SCRIPT_TEXT.encode("gb18030"), "剧本.txt")
        self.assertEqual(lines[0], "测试剧本")

    def test_docx_upload_is_parsed_without_writing_to_disk(self):
        lines = script_dub.extract_script_text(make_docx(SCRIPT_TEXT), "剧本.docx")
        parsed = script_dub.parse_shot_script(lines)
        self.assertEqual(parsed["shot_count"], 2)
        self.assertEqual([line["role"] for line in parsed["lines"]], ["周言", "王姐", "旁白"])
        self.assertEqual(parsed["lines"][0]["emotion"], "紧张")

    def test_preview_returns_editable_cast_plan(self):
        voices = [
            {"voice_id": "v_yujie", "name": "御姐", "samples": [{"file": "a.wav"}]},
            {"voice_id": "v_old_female", "name": "老人女", "samples": [{"file": "b.wav"}]},
            {"voice_id": "v_low_male", "name": "低沉男", "samples": [{"file": "c.wav"}]},
        ]
        with patch.object(script_dub.library, "get_library", return_value={"voices": voices}):
            plan = script_dub.preview_script_bytes(
                SCRIPT_TEXT.encode("utf-8"), "剧本.txt")
        self.assertEqual(plan["line_count"], 3)
        self.assertEqual(plan["cast"]["周言"], "v_yujie")
        self.assertEqual(plan["cast"]["王姐"], "v_old_female")
        self.assertEqual(plan["cast"]["旁白"], "v_low_male")
        self.assertTrue(all(item["ready"] for item in plan["cast_report"]))

class ScriptDubOrchestrationTests(unittest.TestCase):
    def test_concat_preserves_line_order_and_inserts_exact_gap(self):
        scene = {"lines": [
            {"record_id": "first"},
            {"record_id": "second"},
        ]}
        audio = {
            "first.wav": np.array([1.0, 1.0], dtype=np.float32),
            "second.wav": np.array([2.0], dtype=np.float32),
        }
        with patch.object(script_dub, "TARGET_SR", 10), \
             patch.object(script_dub.records, "resolve_audio",
                          side_effect=lambda record_id: record_id + ".wav"), \
             patch.object(script_dub.mixer, "_load_wav",
                          side_effect=lambda path: audio[path]):
            merged = script_dub._concat(scene, gap_ms=200)
        np.testing.assert_array_equal(
            merged, np.array([1.0, 1.0, 0.0, 0.0, 2.0], dtype=np.float32))

    def test_concat_rejects_missing_line_audio(self):
        scene = {"lines": [{"record_id": "first"}, {"record_id": "missing"}]}
        with patch.object(script_dub.records, "resolve_audio",
                          side_effect=["first.wav", None]), \
             patch.object(script_dub.mixer, "_load_wav",
                          return_value=np.array([1.0], dtype=np.float32)):
            with self.assertRaisesRegex(ValueError, "第 2 句音频缺失"):
                script_dub._concat(scene, gap_ms=500)

    def test_selected_episode_is_forwarded_to_created_scene(self):
        parsed = script_dub.parse_shot_script(
            script_dub.extract_script_text(SCRIPT_TEXT.encode("utf-8"), "剧本.txt"))
        voices = [
            {"voice_id": "v_yujie", "name": "御姐", "samples": [{"file": "a.wav"}]},
            {"voice_id": "v_old_female", "name": "老人女", "samples": [{"file": "b.wav"}]},
            {"voice_id": "v_low_male", "name": "低沉男", "samples": [{"file": "c.wav"}]},
        ]
        cast = {"周言": "v_yujie", "王姐": "v_old_female", "旁白": "v_low_male"}
        with patch.object(script_dub.library, "get_library", return_value={"voices": voices}), \
             patch.object(script_dub.characters, "list_characters", return_value=[]), \
             patch.object(script_dub.characters, "create_character",
                          side_effect=lambda name, *_: {"char_id": "char-" + name}), \
             patch.object(script_dub.dialogue, "run_batch",
                          return_value={"scene_id": "scene-1", "lines": []}) as run_batch, \
             patch.object(script_dub, "_concat", return_value=np.zeros(48000, dtype=np.float32)):
            result = script_dub.dub_parsed_script(
                parsed, cast, scene_name="第一场", episode_id="episode-1")
        self.assertEqual(run_batch.call_args.args[2], "episode-1")
        self.assertEqual(result["duration_sec"], 1.0)

    def test_existing_character_is_reused(self):
        roles = [{"name": "周言", "desc": "女老板"}]
        cast = {"周言": "v_yujie"}
        existing = [{"char_id": "char-existing", "name": "周言", "voice_id": "v_yujie"}]
        with patch.object(script_dub.characters, "list_characters", return_value=existing), \
             patch.object(script_dub.characters, "create_character") as create_character:
            char_ids, report = script_dub._resolve_characters(roles, cast)
        self.assertEqual(char_ids["周言"], "char-existing")
        self.assertEqual(report[0]["action"], "复用")
        create_character.assert_not_called()

    def test_voice_without_sample_is_rejected_before_creating_characters(self):
        parsed = script_dub.parse_shot_script(
            script_dub.extract_script_bytes(SCRIPT_TEXT.encode("utf-8"), "剧本.txt"))
        voices = [
            {"voice_id": "v_yujie", "name": "御姐", "samples": []},
            {"voice_id": "v_old_female", "name": "老人女", "samples": []},
            {"voice_id": "v_low_male", "name": "低沉男", "samples": []},
        ]
        cast = {"周言": "v_yujie", "王姐": "v_old_female", "旁白": "v_low_male"}
        with patch.object(script_dub.library, "get_library", return_value={"voices": voices}), \
             patch.object(script_dub.characters, "create_character") as create_character:
            with self.assertRaisesRegex(ValueError, "缺少参考音频"):
                script_dub.dub_parsed_script(parsed, cast)
        create_character.assert_not_called()


if __name__ == "__main__":
    unittest.main()
