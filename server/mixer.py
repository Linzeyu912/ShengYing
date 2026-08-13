# -*- coding: utf-8 -*-
"""混音引擎：对白轨 + 环境音轨 + 音效轨 → 整场混音（纯 numpy，无 ffmpeg 依赖）。

输出：
scenes/<scene_id>/mix.wav       整场混音
scenes/<scene_id>/dialogue.wav  纯对白轨（分轨导出）
混音配置写回 scene.json 的 mix 字段（配置入 git，音频不入）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from . import dialogue, library, records

TARGET_SR = 48000
SCENES_ROOT = Path(__file__).resolve().parent.parent / "scenes"


def _load_wav(path: str | Path) -> np.ndarray:
    wav, sr = sf.read(str(path), dtype="float32")
    if wav.ndim > 1:
        wav = wav.mean(axis=1)  # 立体声转单声道
    if sr != TARGET_SR:  # 线性插值重采样
        n = int(len(wav) * TARGET_SR / sr)
        wav = np.interp(np.linspace(0, len(wav), n, endpoint=False),
                        np.arange(len(wav)), wav).astype(np.float32)
    return wav


def _fade(wav: np.ndarray, ms: int = 800) -> np.ndarray:
    n = min(len(wav) // 2, int(TARGET_SR * ms / 1000))
    if n > 0:
        wav = wav.copy()
        wav[:n] *= np.linspace(0, 1, n)
        wav[-n:] *= np.linspace(1, 0, n)
    return wav


def _loop_to(wav: np.ndarray, length: int) -> np.ndarray:
    if len(wav) == 0:
        return np.zeros(length, dtype=np.float32)
    reps = int(np.ceil(length / len(wav)))
    return np.tile(wav, reps)[:length]


def render_scene_mix(scene_id: str, config: dict) -> dict:
    """渲染场次混音。

    config: {
      gap_ms: 对白行间隔（默认 500）
      dialogue_volume: 对白轨音量（默认 1.0）
      ambience: {id: 环境音素材 id, volume: 0.4} 或 None
      sfx: [{id, at_line: 行号, volume: 0.8}]（本期按行定位）
    }
    """
    scene = dialogue.get_scene(scene_id)
    if scene is None:
        raise ValueError(f"场次不存在: {scene_id}")

    gap_ms = int(config.get("gap_ms", 500))
    d_vol = float(config.get("dialogue_volume", 1.0))
    gap = np.zeros(int(TARGET_SR * gap_ms / 1000), dtype=np.float32)

    # 1) 对白轨：按行拼接
    parts = []
    line_offsets = []  # 每行起始采样点（供音效定位）
    cursor = 0
    for i, line in enumerate(scene["lines"]):
        audio_path = records.resolve_audio(line["record_id"])
        if audio_path is None:
            raise ValueError(f"第 {i + 1} 行音频缺失: {line['record_id']}")
        wav = _load_wav(audio_path) * d_vol
        if i > 0:
            parts.append(gap)
            cursor += len(gap)
        line_offsets.append(cursor)
        parts.append(wav)
        cursor += len(wav)
    dialogue_track = np.concatenate(parts) if parts else np.zeros(TARGET_SR, dtype=np.float32)
    total = len(dialogue_track)

    # 2) 环境音轨：循环铺满 + 淡入淡出
    mix = dialogue_track.copy()
    amb_cfg = config.get("ambience")
    amb_used = None
    if amb_cfg and amb_cfg.get("id"):
        amb_item = next((a for a in library.get_library()["ambience"] if a["id"] == amb_cfg["id"]), None)
        if amb_item is None:
            raise ValueError(f"环境音不存在: {amb_cfg['id']}")
        amb_path = library.ASSETS_ROOT / "ambience" / (amb_cfg["id"] + ".wav")
        amb = _fade(_loop_to(_load_wav(amb_path), total)) * float(amb_cfg.get("volume", 0.4))
        mix = mix + amb
        amb_used = {"id": amb_cfg["id"], "volume": float(amb_cfg.get("volume", 0.4))}

    # 3) 音效轨：按行定位插入
    sfx_used = []
    for item in config.get("sfx", []):
        sfx_item = next((s for s in library.get_library()["sfx"] if s["id"] == item["id"]), None)
        if sfx_item is None:
            raise ValueError(f"音效不存在: {item['id']}")
        sfx_path = library.ASSETS_ROOT / "sfx" / (item["id"] + ".wav")
        wav = _load_wav(sfx_path) * float(item.get("volume", 0.8))
        at_line = int(item.get("at_line", 0))
        start = line_offsets[min(at_line, len(line_offsets) - 1)] if line_offsets else 0
        end = min(start + len(wav), total)
        mix[start:end] += wav[: end - start]
        sfx_used.append({"id": item["id"], "at_line": at_line, "volume": float(item.get("volume", 0.8))})

    # 4) 防爆音归一 + 写盘
    peak = float(np.max(np.abs(mix))) if total else 0.0
    if peak > 0.99:
        mix = mix * (0.99 / peak)
    sdir = SCENES_ROOT / scene_id
    sf.write(str(sdir / "mix.wav"), mix, TARGET_SR)
    sf.write(str(sdir / "dialogue.wav"), dialogue_track, TARGET_SR)

    scene["mix"] = {
        "config": {"gap_ms": gap_ms, "dialogue_volume": d_vol,
                   "ambience": amb_used, "sfx": sfx_used},
        "duration_sec": round(total / TARGET_SR, 2),
        "rendered_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    }
    (sdir / "scene.json").write_text(
        json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    return scene["mix"]
