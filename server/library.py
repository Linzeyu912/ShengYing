# -*- coding: utf-8 -*-
"""素材库数据访问层：读取 assets/ 下的元数据与音频文件清单。"""
from __future__ import annotations

import json
import time
import hashlib
import uuid
import os
from pathlib import Path

ASSETS_ROOT = Path(os.environ.get(
    "SHENGYING_ASSETS_DIR",
    str(Path(__file__).resolve().parent.parent / "assets"),
)).resolve()
EMOTIONS = ["开心", "悲伤", "愤怒", "惊讶", "平静", "紧张", "温柔", "严肃", "调皮", "疲惫"]

_cache: dict = {"loaded_at": 0.0, "voices": [], "sfx": [], "ambience": []}
_TTL = 5.0  # 秒；开发期短缓存，避免每次请求都扫盘

# 音色来源：统一标识"音色怎么来的"，是 voice.json 的权威字段。
# 缺失时按旧字段推断，保证 v1.1 及更早音色向后兼容。
VOICE_SOURCES = ("reference_samples", "design", "lora")


def _infer_voice_source(meta: dict) -> str:
    """从 voice.json 推断权威来源；优先显式字段，其次按内容特征回退。"""
    if meta.get("voice_source") in VOICE_SOURCES:
        return meta["voice_source"]
    if meta.get("lora"):
        return "lora"
    if meta.get("generation_params"):
        return "design"
    return "reference_samples"


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
        missing_samples = []
        for s in meta.get("samples", []):
            f = vdir / s["file"]
            if f.exists():
                samples.append({
                    **s,
                    "sample_id": s.get("sample_id") or f.stem,
                    "clone_mode": s.get("clone_mode") or (
                        "ultimate_clone" if s.get("transcript") else "controllable_clone"),
                    "url": f"/api/voices/{meta['voice_id']}/audio/{f.name}",
                    "size_bytes": f.stat().st_size,
                })
            else:
                missing_samples.append(s.get("file", ""))
        voices.append({
            "voice_id": meta["voice_id"],
            "name": meta["name"],
            "gender": meta.get("gender", "unknown"),
            "description": meta.get("description", ""),
            "language": meta.get("language", "zh"),
            "version": meta.get("version", ""),
            "license": meta.get("license", ""),
            "consent_confirmed": meta.get("consent_confirmed", False),
            "default_clone_mode": meta.get("default_clone_mode", "auto"),
            "voice_source": _infer_voice_source(meta),
            "lora": meta.get("lora"),
            "generation_params": meta.get("generation_params"),
            "emotions": meta.get("emotions", []),
            "known_issues": meta.get("known_issues", []),
            "missing_samples": missing_samples,
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
            "id": rel.with_suffix("").as_posix(),
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


def _resolve_stored_asset(asset_path: str | None) -> Path | None:
    """解析库内相对引用，并兼容旧记录里的绝对路径。"""
    if not asset_path:
        return None
    raw = Path(asset_path)
    base = ASSETS_ROOT.resolve()
    if raw.is_file():
        resolved = raw.resolve()
        if resolved == base or base in resolved.parents:
            return resolved
    normalized = str(asset_path).replace("\\", "/")
    marker = "assets/"
    if marker in normalized:
        normalized = normalized.split(marker, 1)[1]
    candidate = (ASSETS_ROOT / normalized).resolve()
    if candidate.is_file() and (candidate == base or base in candidate.parents):
        return candidate
    return None


def resolve_voice_reference(voice_id: str, emotion: str = "") -> dict:
    """解析音色引用，返回音频、转录、稳定资产引用和兼容参数。

    样本选择顺序：情绪匹配 > 首个样本。
    """
    empty = {
        "path": None, "asset_path": None, "sample_id": None,
        "transcript": "", "clone_mode": "", "generation_params": None,
        "voice_source": "",
    }
    if not voice_id:
        return empty
    vdir = ASSETS_ROOT / "voices" / voice_id
    meta_file = vdir / "voice.json"
    if not meta_file.exists():
        return empty
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    voice_source = _infer_voice_source(meta)

    # LoRA 来源：当前合成路径尚未接入，返回权重信息供上层识别并拦截，
    # 避免被误当作克隆 / 基础 TTS 静默降级。
    if voice_source == "lora":
        lora = meta.get("lora") or {}
        return {
            **empty,
            "voice_source": "lora",
            "clone_mode": "lora",
            "lora": lora,
            "weight_path": lora.get("weight_path"),
            "engine": lora.get("engine"),
            "base_model": lora.get("base_model"),
        }

    samples = [s for s in meta.get("samples", []) if (vdir / s["file"]).exists()]
    if not samples:
        params = meta.get("generation_params") or {}
        stored = params.get("reference_asset") or params.get("reference_wav_path")
        recovered = _resolve_stored_asset(stored)
        recovered_asset = (
            recovered.relative_to(ASSETS_ROOT.resolve()).as_posix() if recovered else None)
        return {
            **empty,
            "voice_source": voice_source,
            "path": str(recovered) if recovered else None,
            "asset_path": recovered_asset,
            "transcript": params.get("prompt_text") or "",
            "clone_mode": params.get("tts_mode") or "",
            "generation_params": params or None,
        }
    chosen = None
    if emotion:
        chosen = next((s for s in samples if s.get("emotion") == emotion), None)
    if chosen is None:
        chosen = samples[0]
    path = (vdir / chosen["file"]).resolve()
    rel = path.relative_to(ASSETS_ROOT.resolve()).as_posix()
    preferred = chosen.get("clone_mode") or meta.get("default_clone_mode") or "auto"
    if preferred == "auto":
        preferred = "ultimate_clone" if chosen.get("transcript") else "controllable_clone"
    return {
        "voice_source": voice_source,
        "path": str(path),
        "asset_path": rel,
        "sample_id": chosen.get("sample_id") or path.stem,
        "transcript": chosen.get("transcript") or "",
        "clone_mode": preferred,
        "generation_params": None,
    }


def import_voice(
    audio_data: bytes,
    filename: str,
    name: str,
    transcript: str,
    gender: str = "unknown",
    emotion: str = "平静",
    description: str = "",
    language: str = "zh",
    license_name: str = "authorized",
    consent_confirmed: bool = False,
) -> dict:
    """把上传的 WAV 与精确转录保存为可复用的极致克隆音色。"""
    if not name.strip():
        raise ValueError("音色名称不能为空")
    if not transcript.strip():
        raise ValueError("极致克隆需要参考音频的精确转录文本")
    if not consent_confirmed:
        raise ValueError("必须确认已获得声音使用授权")
    if gender not in ("male", "female", "unknown"):
        raise ValueError("gender 仅支持 male / female / unknown")
    if not filename.lower().endswith(".wav"):
        raise ValueError("当前音色导入仅支持 WAV 文件")
    if len(audio_data) < 44 or audio_data[:4] != b"RIFF" or audio_data[8:12] != b"WAVE":
        raise ValueError("文件不是有效的 WAV 音频")

    voice_id = "v_user_" + uuid.uuid4().hex[:8]
    sample_id = "s_" + uuid.uuid4().hex[:8]
    vdir = ASSETS_ROOT / "voices" / voice_id
    samples_dir = vdir / "samples"
    samples_dir.mkdir(parents=True)
    safe_emotion = "".join(c for c in emotion.strip() if c not in '\\/:*?"<>|') or "平静"
    sample_name = f"01_{safe_emotion}.wav"
    audio_path = samples_dir / sample_name
    audio_path.write_bytes(audio_data)
    digest = hashlib.sha256(audio_data).hexdigest()
    voice_json = {
        "voice_id": voice_id,
        "name": name.strip(),
        "mode": "reference_samples",
        "voice_source": "reference_samples",
        "default_clone_mode": "ultimate_clone",
        "gender": gender,
        "description": description.strip(),
        "language": language.strip() or "zh",
        "emotions": [safe_emotion],
        "samples": [{
            "sample_id": sample_id,
            "file": f"samples/{sample_name}",
            "emotion": safe_emotion,
            "transcript": transcript.strip(),
            "clone_mode": "ultimate_clone",
            "sha256": digest,
            "source_file": filename,
        }],
        "bound_characters": [],
        "license": license_name.strip() or "authorized",
        "consent_confirmed": True,
        "version": "v1.0",
        "created_at": time.strftime("%Y-%m-%d"),
    }
    (vdir / "voice.json").write_text(
        json.dumps(voice_json, ensure_ascii=False, indent=2), encoding="utf-8")
    get_library(force=True)
    return get_voice(voice_id) or voice_json


def voice_asset_status() -> dict:
    voices_dir = ASSETS_ROOT / "voices"
    declared = missing = ultimate_ready = 0
    if voices_dir.exists():
        for meta_file in voices_dir.glob("*/voice.json"):
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            for sample in meta.get("samples", []):
                declared += 1
                if not (meta_file.parent / sample.get("file", "")).is_file():
                    missing += 1
                elif sample.get("transcript"):
                    ultimate_ready += 1
    return {
        "declared_samples": declared,
        "missing_samples": missing,
        "ultimate_ready_samples": ultimate_ready,
    }


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
