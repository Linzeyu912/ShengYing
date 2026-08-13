# -*- coding: utf-8 -*-
"""对白批量合成与场次管理。

scenes/
└── <scene_id>/scene.json   # 场次清单：每行台词关联一条生成记录
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from . import characters, library, records, tts

SCENES_ROOT = Path(__file__).resolve().parent.parent / "scenes"


def run_batch(scene_name: str, lines: list[dict]) -> dict:
    """按行批量合成对白。每行: {character_id, text, emotion?}

    角色绑定的音色决定克隆参考（或固化音色参数）；行内情绪覆盖角色默认情绪。
    """
    scene_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    scene_lines = []
    for idx, line in enumerate(lines):
        char = characters.get_character(line["character_id"])
        if char is None:
            raise ValueError(f"第 {idx + 1} 行角色不存在: {line['character_id']}")
        emotion = line.get("emotion") or char.get("default_emotion") or ""
        reference, gen_params = library.resolve_voice_reference(
            char.get("voice_id") or "", emotion)

        text = line["text"]
        seed, cfg, steps = None, 2.0, 10
        if gen_params:  # 固化音色：复现描述前缀与参数
            src = gen_params.get("text") or ""
            if src.startswith("(") and ")" in src:
                text = src[: src.index(")") + 1] + text
            seed = gen_params.get("seed")
            cfg = gen_params.get("cfg_value", cfg)
            steps = gen_params.get("inference_timesteps", steps)
            reference = gen_params.get("reference_wav_path") or reference

        wav, sr, used_seed = tts.synthesize(
            text=text, reference_wav_path=reference,
            seed=seed, cfg_value=cfg, inference_timesteps=steps)
        record = records.save_record(wav, sr, {
            "text": line["text"], "mode": tts.detect_mode(text, reference),
            "voice_id": char.get("voice_id") or None, "emotion": emotion or None,
            "seed": used_seed, "cfg_value": cfg, "inference_timesteps": steps,
            "reference_wav_path": reference,
            "scene_id": scene_id, "character_id": char["char_id"], "line_index": idx,
        })
        scene_lines.append({
            "index": idx, "character_id": char["char_id"], "character_name": char["name"],
            "text": line["text"], "emotion": emotion or None,
            "record_id": record["record_id"], "duration_sec": record["duration_sec"],
            "seed": used_seed,
        })
    scene = {
        "scene_id": scene_id, "name": scene_name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "line_count": len(scene_lines), "lines": scene_lines,
    }
    sdir = SCENES_ROOT / scene_id
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "scene.json").write_text(
        json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    return scene


def list_scenes() -> list[dict]:
    if not SCENES_ROOT.exists():
        return []
    out = []
    for sdir in SCENES_ROOT.iterdir():
        f = sdir / "scene.json"
        if f.exists():
            s = json.loads(f.read_text(encoding="utf-8"))
            out.append({k: s[k] for k in ("scene_id", "name", "created_at", "line_count")})
    return sorted(out, key=lambda s: s["scene_id"], reverse=True)


def get_scene(scene_id: str) -> dict | None:
    f = SCENES_ROOT / scene_id / "scene.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
