# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from server import library, records, synthesis, tts


class DummyModel:
    class TTS:
        sample_rate = 48000

    tts_model = TTS()

    def __init__(self):
        self.kwargs = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return np.zeros(480, dtype=np.float32)


class VoxCPMAdapterTests(unittest.TestCase):
    def test_ultimate_clone_forwards_prompt_and_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            wav = Path(temp) / "voice.wav"
            wav.write_bytes(b"RIFF" + b"\0" * 40)
            model = DummyModel()
            old_model = tts._model
            tts._model = model
            try:
                out, sr, seed = tts.synthesize(
                    text="新台词",
                    reference_wav_path=str(wav),
                    prompt_wav_path=str(wav),
                    prompt_text="参考音频原文",
                    seed=42,
                    normalize=True,
                )
            finally:
                tts._model = old_model
            self.assertEqual(sr, 48000)
            self.assertEqual(seed, 42)
            self.assertEqual(len(out), 480)
            self.assertEqual(model.kwargs["prompt_wav_path"], str(wav))
            self.assertEqual(model.kwargs["reference_wav_path"], str(wav))
            self.assertEqual(model.kwargs["prompt_text"], "参考音频原文")
            self.assertTrue(model.kwargs["normalize"])

    def test_prompt_audio_and_text_must_be_paired(self):
        with self.assertRaisesRegex(ValueError, "必须同时提供"):
            tts.synthesize(text="新台词", prompt_wav_path="missing.wav")


class SynthesisRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_assets = library.ASSETS_ROOT
        library.ASSETS_ROOT = Path(self.temp.name) / "assets"
        library._cache["loaded_at"] = 0

    def tearDown(self):
        library.ASSETS_ROOT = self.old_assets
        library._cache["loaded_at"] = 0
        self.temp.cleanup()

    def _voice(self, transcript="参考原文", clone_mode="ultimate_clone"):
        vdir = library.ASSETS_ROOT / "voices" / "v_test"
        (vdir / "samples").mkdir(parents=True)
        (vdir / "samples" / "ref.wav").write_bytes(b"RIFF" + b"\0" * 40)
        (vdir / "voice.json").write_text(json.dumps({
            "voice_id": "v_test",
            "name": "测试音色",
            "default_clone_mode": clone_mode,
            "samples": [{
                "sample_id": "s_test",
                "file": "samples/ref.wav",
                "emotion": "平静",
                "transcript": transcript,
                "clone_mode": clone_mode,
            }],
        }, ensure_ascii=False), encoding="utf-8")

    def test_auto_routes_transcribed_sample_to_ultimate_clone(self):
        self._voice()
        captured = {}

        def fake_synthesize(**kwargs):
            captured.update(kwargs)
            return np.zeros(10), 48000, kwargs.get("seed") or 7

        with patch.object(tts, "synthesize", side_effect=fake_synthesize):
            _, _, meta = synthesis.generate("新台词", voice_id="v_test")
        self.assertEqual(meta["mode"], "ultimate_clone")
        self.assertEqual(meta["source_sample_id"], "s_test")
        self.assertEqual(captured["prompt_text"], "参考原文")
        self.assertEqual(captured["prompt_wav_path"], captured["reference_wav_path"])

    def test_auto_falls_back_to_controllable_without_transcript(self):
        self._voice(transcript="", clone_mode="auto")
        with patch.object(tts, "synthesize", return_value=(np.zeros(10), 48000, 8)) as synth:
            _, _, meta = synthesis.generate("新台词", voice_id="v_test")
        self.assertEqual(meta["mode"], "controllable_clone")
        self.assertIsNone(synth.call_args.kwargs["prompt_wav_path"])

    def test_explicit_ultimate_requires_transcript(self):
        self._voice(transcript="", clone_mode="auto")
        with self.assertRaisesRegex(ValueError, "精确转录"):
            synthesis.generate(
                "新台词", voice_id="v_test", requested_mode="ultimate_clone")

    def test_import_voice_requires_consent_and_creates_ultimate_asset(self):
        fake_wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"\0" * 32
        with self.assertRaisesRegex(ValueError, "授权"):
            library.import_voice(fake_wav, "ref.wav", "测试", "原文")
        voice = library.import_voice(
            fake_wav, "ref.wav", "测试", "原文", consent_confirmed=True)
        self.assertEqual(voice["default_clone_mode"], "ultimate_clone")
        self.assertEqual(voice["samples"][0]["transcript"], "原文")
        self.assertTrue(voice["samples"][0]["url"].startswith("/api/voices/"))


class RecordPromotionTests(unittest.TestCase):
    def test_promoted_generation_becomes_portable_ultimate_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            old_records, old_assets = records.RECORDS_ROOT, library.ASSETS_ROOT
            records.RECORDS_ROOT = Path(temp) / "generations"
            library.ASSETS_ROOT = Path(temp) / "assets"
            library._cache["loaded_at"] = 0
            try:
                record = records.save_record(np.zeros(4800), 48000, {
                    "text": "这是生成音频的原文。",
                    "mode": "controllable_clone",
                    "seed": 12,
                    "cfg_value": 2.0,
                    "inference_timesteps": 10,
                    "emotion": "平静",
                })
                voice = records.promote_to_voice(record["record_id"], "固化测试")
                sample = voice["samples"][0]
                self.assertEqual(voice["default_clone_mode"], "ultimate_clone")
                self.assertEqual(sample["transcript"], "这是生成音频的原文。")
                self.assertTrue((library.ASSETS_ROOT / "voices" / voice["voice_id"] / sample["file"]).is_file())
                resolved = library.resolve_voice_reference(voice["voice_id"])
                self.assertEqual(resolved["clone_mode"], "ultimate_clone")
            finally:
                records.RECORDS_ROOT, library.ASSETS_ROOT = old_records, old_assets
                library._cache["loaded_at"] = 0


if __name__ == "__main__":
    unittest.main()
