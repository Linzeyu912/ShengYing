# -*- coding: utf-8 -*-
"""整集导出：把一集的所有场次混音按顺序合并为完整音频。

输出：
projects/<pid>/episodes/<eid>/export.wav   整集音频（不入 git）
projects/<pid>/episodes/<eid>/export.json  导出清单（入 git 可追溯）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import dialogue, mixer, projects


def export_episode(episode_id: str, scene_gap_ms: int = 1000,
                   auto_render: bool = True) -> dict:
    ep = projects.get_episode(episode_id)
    if ep is None:
        raise ValueError(f"剧集不存在: {episode_id}")
    pid = ep["project_id"]

    scenes = [s for s in dialogue.list_scenes() if s.get("episode_id") == episode_id]
    scenes.sort(key=lambda s: s["scene_id"])  # scene_id 含时间戳，即创作顺序
    if not scenes:
        raise ValueError("该剧集下没有场次")

    gap = np.zeros(int(mixer.TARGET_SR * scene_gap_ms / 1000), dtype=np.float32)
    parts, scene_entries = [], []
    for i, s in enumerate(scenes):
        sid = s["scene_id"]
        sdir = dialogue.SCENES_ROOT / sid
        source = "mix"
        if not (sdir / "mix.wav").exists():
            if s.get("draft"):
                raise ValueError(f"场次「{s['name']}」台词尚未生成，无法导出")
            if not auto_render:
                raise ValueError(f"场次「{s['name']}」尚未混音")
            mixer.render_scene_mix(sid, {})  # 默认配置补渲染（纯对白轨）
            source = "auto"
        wav = mixer._load_wav(sdir / "mix.wav")
        if i > 0:
            parts.append(gap)
        parts.append(wav)
        scene_entries.append({"scene_id": sid, "name": s["name"], "source": source,
                              "duration_sec": round(len(wav) / mixer.TARGET_SR, 2)})

    full = np.concatenate(parts)
    peak = float(np.max(np.abs(full)))
    if peak > 0.99:
        full = full * (0.99 / peak)

    edir = projects.PROJECTS_ROOT / pid / "episodes" / episode_id
    sf.write(str(edir / "export.wav"), full, mixer.TARGET_SR)
    manifest = {
        "episode_id": episode_id, "episode_name": ep["name"], "project_id": pid,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scene_gap_ms": scene_gap_ms,
        "total_duration_sec": round(len(full) / mixer.TARGET_SR, 2),
        "scenes": scene_entries,
    }
    (edir / "export.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def resolve_export(episode_id: str) -> Path | None:
    ep = projects.get_episode(episode_id)
    if ep is None:
        return None
    path = projects.PROJECTS_ROOT / ep["project_id"] / "episodes" / episode_id / "export.wav"
    return path if path.is_file() else None
