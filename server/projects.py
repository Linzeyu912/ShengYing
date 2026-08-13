# -*- coding: utf-8 -*-
"""项目与剧集组织。

projects/
└── <project_id>/
    ├── project.json
    └── episodes/
        └── <episode_id>/episode.json

场次通过 scene.json 中的 project_id / episode_id 关联到剧集（存储保持扁平）。
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

PROJECTS_ROOT = Path(__file__).resolve().parent.parent / "projects"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 项目 ----------------

def create_project(name: str, description: str = "") -> dict:
    pid = "p_" + uuid.uuid4().hex[:8]
    pdir = PROJECTS_ROOT / pid
    (pdir / "episodes").mkdir(parents=True, exist_ok=True)
    project = {"project_id": pid, "name": name, "description": description,
               "created_at": _now()}
    (pdir / "project.json").write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    return project


def list_projects() -> list[dict]:
    if not PROJECTS_ROOT.exists():
        return []
    out = []
    for pdir in sorted(PROJECTS_ROOT.iterdir()):
        f = pdir / "project.json"
        if f.exists():
            p = json.loads(f.read_text(encoding="utf-8"))
            p["episode_count"] = len(list_episodes(p["project_id"]))
            out.append(p)
    return out


def get_project(pid: str) -> dict | None:
    f = PROJECTS_ROOT / pid / "project.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def delete_project(pid: str) -> bool:
    pdir = PROJECTS_ROOT / pid
    if pdir.exists():
        shutil.rmtree(pdir)
        return True
    return False


# ---------------- 剧集 ----------------

def create_episode(pid: str, name: str, number: int = 1, description: str = "") -> dict:
    if get_project(pid) is None:
        raise ValueError(f"项目不存在: {pid}")
    eid = "e_" + uuid.uuid4().hex[:8]
    edir = PROJECTS_ROOT / pid / "episodes" / eid
    edir.mkdir(parents=True, exist_ok=True)
    episode = {"episode_id": eid, "project_id": pid, "number": number,
               "name": name, "description": description, "created_at": _now()}
    (edir / "episode.json").write_text(
        json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
    return episode


def list_episodes(pid: str) -> list[dict]:
    edir = PROJECTS_ROOT / pid / "episodes"
    if not edir.exists():
        return []
    out = []
    for d in sorted(edir.iterdir()):
        f = d / "episode.json"
        if f.exists():
            out.append(json.loads(f.read_text(encoding="utf-8")))
    return sorted(out, key=lambda e: e.get("number", 0))


def get_episode(eid: str) -> dict | None:
    if not PROJECTS_ROOT.exists():
        return None
    for pdir in PROJECTS_ROOT.iterdir():
        f = pdir / "episodes" / eid / "episode.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    return None


def delete_episode(eid: str) -> bool:
    if not PROJECTS_ROOT.exists():
        return False
    for pdir in PROJECTS_ROOT.iterdir():
        edir = pdir / "episodes" / eid
        if edir.exists():
            shutil.rmtree(edir)
            return True
    return False
