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

from . import characters, projects, records, synthesis

SCENES_ROOT = Path(__file__).resolve().parent.parent / "scenes"


def _synth_line(scene_id: str, idx: int, char: dict, text: str, emotion: str) -> dict:
    """合成单行台词并归档生成记录，返回场次行数据。"""
    voice_id = char.get("voice_id") or ""
    if not voice_id and not text.lstrip().startswith(("(", "（")):
        raise ValueError(f"角色「{char['name']}」未绑定音色，无法合成")
    wav, sr, meta = synthesis.generate(
        text=text,
        voice_id=voice_id,
        emotion=emotion,
        requested_mode=char.get("clone_mode") or "auto",
    )
    record = records.save_record(wav, sr, {
        **meta,
        "scene_id": scene_id, "character_id": char["char_id"], "line_index": idx,
    })
    return {
        "index": idx, "character_id": char["char_id"], "character_name": char["name"],
        "text": record["text"], "emotion": emotion or None,
        "record_id": record["record_id"], "duration_sec": record["duration_sec"],
        "seed": meta["seed"], "mode": meta["mode"],
    }


def run_batch(scene_name: str, lines: list[dict], episode_id: str = "") -> dict:
    """按行批量合成对白。每行: {character_id, text, emotion?}

    角色绑定的音色决定克隆参考（或固化音色参数）；行内情绪覆盖角色默认情绪。
    提供 episode_id 时场次归属对应剧集与项目。
    """
    project_id, ep_name = "", ""
    if episode_id:
        ep = projects.get_episode(episode_id)
        if ep is None:
            raise ValueError(f"剧集不存在: {episode_id}")
        project_id, ep_name = ep["project_id"], ep["name"]
    scene_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    scene_lines = []
    for idx, line in enumerate(lines):
        char = characters.get_character(line["character_id"])
        if char is None:
            raise ValueError(f"第 {idx + 1} 行角色不存在: {line['character_id']}")
        emotion = line.get("emotion") or char.get("default_emotion") or ""
        scene_lines.append(_synth_line(scene_id, idx, char, line["text"], emotion))
    scene = {
        "scene_id": scene_id, "name": scene_name,
        "project_id": project_id or None, "episode_id": episode_id or None,
        "episode_name": ep_name or None,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "line_count": len(scene_lines), "lines": scene_lines,
    }
    _save_scene(scene)
    return scene


def _save_scene(scene: dict) -> None:
    sdir = SCENES_ROOT / scene["scene_id"]
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "scene.json").write_text(
        json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")


def write_skeleton(scene_name: str, episode_id: str, lines: list[dict],
                   scene_card_ref: str = "") -> dict:
    """写入场次草稿骨架（资产包导入用）：台词行不含音频，record_id 为 None。"""
    project_id, ep_name = "", ""
    ep = projects.get_episode(episode_id)
    if ep:
        project_id, ep_name = ep["project_id"], ep["name"]
    scene = {
        "scene_id": time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4],
        "name": scene_name, "draft": True,
        "project_id": project_id or None, "episode_id": episode_id or None,
        "episode_name": ep_name or None, "scene_card_ref": scene_card_ref or None,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "line_count": len(lines), "lines": lines,
    }
    _save_scene(scene)
    return scene


def generate_scene_lines(scene_id: str) -> dict:
    """为草稿场次的全部待生成行合成音频。"""
    scene = get_scene(scene_id)
    if scene is None:
        raise ValueError(f"场次不存在: {scene_id}")
    for line in scene["lines"]:
        if line.get("record_id"):
            continue
        char = characters.get_character(line["character_id"])
        if char is None:
            raise ValueError(f"角色不存在: {line['character_id']}")
        emotion = line.get("emotion") or char.get("default_emotion") or ""
        done = _synth_line(scene_id, line["index"], char, line["text"], emotion)
        line.update(done)
    scene["draft"] = any(not l.get("record_id") for l in scene["lines"])
    _save_scene(scene)
    return scene


def assign_scene(scene_id: str, episode_id: str) -> dict:
    """把已有场次分配到剧集（老场次补归属用）。"""
    scene = get_scene(scene_id)
    if scene is None:
        raise ValueError(f"场次不存在: {scene_id}")
    ep = projects.get_episode(episode_id)
    if ep is None:
        raise ValueError(f"剧集不存在: {episode_id}")
    scene["episode_id"] = episode_id
    scene["project_id"] = ep["project_id"]
    scene["episode_name"] = ep["name"]
    (SCENES_ROOT / scene_id / "scene.json").write_text(
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
            out.append({
                "scene_id": s["scene_id"], "name": s["name"],
                "created_at": s["created_at"], "line_count": s["line_count"],
                "project_id": s.get("project_id"), "episode_id": s.get("episode_id"),
                "episode_name": s.get("episode_name"),
                "has_mix": "mix" in s, "draft": s.get("draft", False),
            })
    return sorted(out, key=lambda s: s["scene_id"], reverse=True)


def get_scene(scene_id: str) -> dict | None:
    f = SCENES_ROOT / scene_id / "scene.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
