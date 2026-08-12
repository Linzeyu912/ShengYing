# -*- coding: utf-8 -*-
"""生成记录存储：每次 TTS 合成自动归档 音频 + 元数据。

目录结构：
generations/
└── <record_id>/          # 20260812_094530_a1b2c3
    ├── audio.wav         # 生成音频（不入 git）
    └── record.json       # 元数据（入 git，可追溯）
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

RECORDS_ROOT = Path(__file__).resolve().parent.parent / "generations"


def save_record(wav: np.ndarray, sample_rate: int, meta: dict) -> dict:
    rid = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    rdir = RECORDS_ROOT / rid
    rdir.mkdir(parents=True, exist_ok=True)
    sf.write(str(rdir / "audio.wav"), wav, sample_rate)
    record = {
        "record_id": rid,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sample_rate": sample_rate,
        "duration_sec": round(len(wav) / sample_rate, 2),
        "review_status": "pending",   # pending | approved | rejected
        **meta,
    }
    (rdir / "record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def list_records() -> list[dict]:
    if not RECORDS_ROOT.exists():
        return []
    records = []
    for rdir in RECORDS_ROOT.iterdir():
        f = rdir / "record.json"
        if f.exists():
            records.append(json.loads(f.read_text(encoding="utf-8")))
    return sorted(records, key=lambda r: r["record_id"], reverse=True)


def get_record(rid: str) -> dict | None:
    f = RECORDS_ROOT / rid / "record.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def resolve_audio(rid: str) -> Path | None:
    base = RECORDS_ROOT.resolve()
    target = (base / rid / "audio.wav").resolve()
    if target.is_file() and target.parent.parent == base:
        return target
    return None


def promote_to_voice(rid: str, name: str, gender: str = "unknown") -> dict:
    """把一条生成记录固化为音色库音色（写入 generation_params）。"""
    from . import library  # 延迟导入避免循环

    record = get_record(rid)
    if record is None:
        raise ValueError(f"生成记录不存在: {rid}")
    voice_id = "v_gen_" + rid.split("_", 1)[-1].replace("_", "")
    vdir = library.ASSETS_ROOT / "voices" / voice_id
    if vdir.exists():
        raise ValueError(f"该记录已固化过: {voice_id}")
    vdir.mkdir(parents=True)
    voice_json = {
        "voice_id": voice_id,
        "name": name,
        "mode": "generation_params",       # 由生成记录固化，无参考音频
        "gender": gender,
        "description": record.get("text", "")[:50],
        "language": "zh",
        "generation_params": {
            "source_record": rid,
            "tts_mode": record.get("mode"),
            "text": record.get("text"),
            "seed": record.get("seed"),
            "cfg_value": record.get("cfg_value"),
            "inference_timesteps": record.get("inference_timesteps"),
            "reference_wav_path": record.get("reference_wav_path"),
        },
        "samples": [],
        "bound_characters": [],
        "license": "original",
        "version": "v1.0",
        "created_at": time.strftime("%Y-%m-%d"),
    }
    (vdir / "voice.json").write_text(
        json.dumps(voice_json, ensure_ascii=False, indent=2), encoding="utf-8")
    library.get_library(force=True)
    return voice_json
