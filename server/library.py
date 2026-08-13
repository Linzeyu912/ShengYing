# -*- coding: utf-8 -*-
"""素材库数据访问层：读取 assets/ 下的元数据与音频文件清单。"""
from __future__ import annotations

import json
import time
from pathlib import Path

ASSETS_ROOT = Path(__file__).resolve().parent.parent / "assets"
EMOTIONS = ["开心", "悲伤", "愤怒", "惊讶", "平静", "紧张", "温柔", "严肃", "调皮", "疲惫"]

_cache: dict = {"loaded_at": 0.0, "voices": [], "sfx": [], "ambience": []}
_TTL = 5.0  # 秒；开发期短缓存，避免每次请求都扫盘


def _load_voices() -> list[dict]:
    voices = []
    voices_dir = ASSETS_ROOT / "voices"
    if not voices_dir.exists():
        return voices
    for vdir in sorted(voices_dir.iterdir()):
        meta_file = vdir / "voice.json"
        if not meta_file.exists():
            continue
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        samples = []
        for s in meta.get("samples", []):
            f = vdir / s["file"]
            if f.exists():
                samples.append({
                    **s,
                    "url": f"/api/voices/{meta['voice_id']}/audio/{f.name}",
                    "size_bytes": f.stat().st_size,
                })
        voices.append({
            "voice_id": meta["voice_id"],
            "name": meta["name"],
            "gender": meta.get("gender", "unknown"),
            "description": meta.get("description", ""),
            "language": meta.get("language", "zh"),
            "version": meta.get("version", ""),
            "license": meta.get("license", ""),
            "emotions": meta.get("emotions", []),
            "known_issues": meta.get("known_issues", []),
            "samples": samples,
        })
    return voices


def _scan_simple(kind: str) -> list[dict]:
    """扫描 sfx / ambience 目录（当前为占位，未来按类别子目录组织）。"""
    items = []
    root = ASSETS_ROOT / kind
    if not root.exists():
        return items
    for f in sorted(root.rglob("*.wav")):
        rel = f.relative_to(root)
        items.append({
            "id": str(rel.with_suffix("")),
            "name": f.stem,
            "category": rel.parts[0] if len(rel.parts) > 1 else "未分类",
            "url": f"/api/assets/{kind}/audio/{rel.as_posix()}",
            "size_bytes": f.stat().st_size,
        })
    return items


def get_library(force: bool = False) -> dict:
    if force or time.time() - _cache["loaded_at"] > _TTL:
        _cache["voices"] = _load_voices()
        _cache["sfx"] = _scan_simple("sfx")
        _cache["ambience"] = _scan_simple("ambience")
        _cache["loaded_at"] = time.time()
    return _cache


def get_voice(voice_id: str) -> dict | None:
    for v in get_library()["voices"]:
        if v["voice_id"] == voice_id:
            return v
    return None


def resolve_voice_sample(voice_id: str, filename: str) -> Path | None:
    """安全解析音色样本路径，防目录穿越。"""
    base = (ASSETS_ROOT / "voices" / voice_id / "samples").resolve()
    target = (base / filename).resolve()
    if target.is_file() and target.parent == base:
        return target
    return None


def resolve_asset_file(kind: str, relpath: str) -> Path | None:
    if kind not in ("sfx", "ambience"):
        return None
    base = (ASSETS_ROOT / kind).resolve()
    target = (base / relpath).resolve()
    if target.is_file() and str(target).startswith(str(base)):
        return target
    return None


def resolve_voice_reference(voice_id: str, emotion: str = "") -> tuple[str | None, dict | None]:
    """解析音色引用：实录音色返回克隆参考样本路径；固化音色返回其 generation_params。

    样本选择顺序：情绪匹配 > 首个样本。
    """
    if not voice_id:
        return None, None
    vdir = ASSETS_ROOT / "voices" / voice_id
    meta_file = vdir / "voice.json"
    if not meta_file.exists():
        return None, None
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    samples = [s for s in meta.get("samples", []) if (vdir / s["file"]).exists()]
    if not samples:
        return None, meta.get("generation_params")
    chosen = None
    if emotion:
        chosen = next((s for s in samples if s.get("emotion") == emotion), None)
    if chosen is None:
        chosen = samples[0]
    return str(vdir / chosen["file"]), None


def search_voices(q: str = "", gender: str = "", emotion: str = "") -> list[dict]:
    results = []
    for v in get_library()["voices"]:
        if gender and v["gender"] != gender:
            continue
        if q and q.lower() not in (v["name"] + v["voice_id"] + v["description"]).lower():
            continue
        if emotion:
            matched = [s for s in v["samples"] if s["emotion"] == emotion]
            if not matched:
                continue
            v = {**v, "samples": matched}
        results.append(v)
    return results
