# -*- coding: utf-8 -*-
"""群像资产包导入（协议 v0.1，见 docs/qunxiang-资产包协议.md）。

流程：load_package → preview（导入预览报告）→ execute（建项目/剧集/角色/场次骨架）。
幂等：projects/<pid>/import_map.json 记录 qunxiang_id 映射，重导执行更新。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import characters, dialogue, library, projects

ALLOWED_EMOTIONS = {"开心", "悲伤", "愤怒", "惊讶", "平静", "紧张", "温柔", "严肃", "调皮", "疲惫"}


class PackageError(ValueError):
    pass


def load_package(pkg_dir: str | Path) -> dict:
    root = Path(pkg_dir)
    manifest_file = root / "manifest.json"
    if not manifest_file.exists():
        raise PackageError(f"资产包缺少 manifest.json: {root}")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("package_version") != "0.1":
        raise PackageError(f"不支持的包版本: {manifest.get('package_version')}")
    pkg = {"root": root, "manifest": manifest, "characters": {}, "scenes": {}, "scripts": {}}
    for rel in manifest.get("characters", []):
        c = json.loads((root / rel).read_text(encoding="utf-8"))
        pkg["characters"][c["id"]] = c
    for rel in manifest.get("scenes", []):
        s = json.loads((root / rel).read_text(encoding="utf-8"))
        pkg["scenes"][s["id"]] = s
    for ep in manifest.get("episodes", []):
        pkg["scripts"][ep["id"]] = json.loads((root / ep["script"]).read_text(encoding="utf-8"))
    return pkg


def _voice_suggestion(hint: dict) -> tuple[str | None, str]:
    """按 voice_hint.reference_style 在音色库模糊匹配，返回 (voice_id, 说明)。"""
    style = (hint or {}).get("reference_style", "")
    if not style:
        return None, "无 voice_hint，需人工选音色"
    voices = library.get_library()["voices"]
    exact = [v for v in voices if v["name"] == style]
    if exact:
        return exact[0]["voice_id"], f"库内精确匹配「{style}」"
    fuzzy = [v for v in voices if style in v["name"] or v["name"] in style]
    if fuzzy:
        return fuzzy[0]["voice_id"], f"库内模糊匹配「{fuzzy[0]['name']}」，建议人工确认"
    return None, f"库内无匹配，可用 voice_hint.description 做音色设计"


def preview(pkg_dir: str | Path) -> dict:
    pkg = load_package(pkg_dir)
    m = pkg["manifest"]
    issues = []
    char_reports = []
    for qid, c in pkg["characters"].items():
        vid, note = _voice_suggestion(c.get("voice_hint") or {})
        char_reports.append({"qunxiang_id": qid, "name": c["name"],
                             "suggested_voice_id": vid, "voice_note": note})
        if vid is None:
            issues.append(f"角色「{c['name']}」无库内音色匹配")
    line_count = 0
    for ep in m.get("episodes", []):
        script = pkg["scripts"].get(ep["id"])
        if not script:
            issues.append(f"剧集 {ep['id']} 缺剧本文件")
            continue
        for sc in script.get("scenes", []):
            if sc.get("scene_ref") not in pkg["scenes"]:
                issues.append(f"场次「{sc['name']}」引用的场景卡 {sc.get('scene_ref')} 不存在（跳过校验）")
            for line in sc.get("lines", []):
                line_count += 1
                if line.get("character_ref") not in pkg["characters"]:
                    issues.append(f"台词引用未定义角色: {line.get('character_ref')}")
                if line.get("emotion") and line["emotion"] not in ALLOWED_EMOTIONS:
                    issues.append(f"未知情绪「{line['emotion']}」（{sc['name']}·{line['text'][:10]}…），将降级为平静")
    return {
        "project_title": m["project"]["title"],
        "project_ref": m["source"].get("project_ref", ""),
        "episodes": [{"id": e["id"], "number": e["number"], "title": e["title"]} for e in m.get("episodes", [])],
        "characters": char_reports,
        "scene_cards": [s["name"] for s in pkg["scenes"].values()],
        "line_count": line_count,
        "issues": issues,
    }


def _load_import_map(pid: str) -> dict:
    f = projects.PROJECTS_ROOT / pid / "import_map.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _save_import_map(pid: str, m: dict) -> None:
    (projects.PROJECTS_ROOT / pid / "import_map.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def execute(pkg_dir: str | Path) -> dict:
    pkg = load_package(pkg_dir)
    m = pkg["manifest"]
    project_ref = m["source"].get("project_ref", "")

    # 1) 项目：同 project_ref 重导则复用
    project, import_map = None, {}
    for p in projects.list_projects():
        im = _load_import_map(p["project_id"])
        if im.get("project_ref") == project_ref and project_ref:
            project, import_map = p, im
            break
    if project is None:
        project = projects.create_project(m["project"]["title"], m["project"].get("description", ""))
        import_map = {"project_ref": project_ref, "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "characters": {}, "episodes": {}, "scenes": {}}
    pid = project["project_id"]

    # 2) 剧集
    for ep in m.get("episodes", []):
        if ep["id"] not in import_map["episodes"]:
            created = projects.create_episode(pid, ep["title"], ep.get("number", 1))
            import_map["episodes"][ep["id"]] = created["episode_id"]

    # 3) 角色（记录 qunxiang_id；精确匹配的音色自动绑定，其余留待人工）
    bound_notes = []
    for qid, c in pkg["characters"].items():
        vid, note = _voice_suggestion(c.get("voice_hint") or {})
        auto_bind = vid if note.startswith("库内精确匹配") else ""
        desc = f"{'/'.join(c.get('personality', []))}；{c.get('appearance', '')}".strip("；")
        if qid in import_map["characters"]:
            char = characters.update_character(import_map["characters"][qid],
                                               name=c["name"], description=desc)
        else:
            char = characters.create_character(c["name"], auto_bind, "", desc, qunxiang_id=qid)
            import_map["characters"][qid] = char["char_id"]
        bound_notes.append({"name": c["name"], "voice_id": char.get("voice_id") or None, "note": note})

    # 4) 场景卡 → 项目参考文件
    meta_dir = projects.PROJECTS_ROOT / pid / "scenes_meta"
    meta_dir.mkdir(exist_ok=True)
    for qid, s in pkg["scenes"].items():
        (meta_dir / f"{qid}.json").write_text(
            json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5) 剧本 → 场次草稿骨架（台词行待生成）
    created_scenes, skipped = [], 0
    for ep in m.get("episodes", []):
        script = pkg["scripts"].get(ep["id"])
        if not script:
            continue
        eid = import_map["episodes"][ep["id"]]
        for sc in script.get("scenes", []):
            map_key = f"{ep['id']}:{sc['name']}"
            if map_key in import_map["scenes"]:
                skipped += 1
                continue
            lines = []
            for i, line in enumerate(sc.get("lines", [])):
                char_id = import_map["characters"].get(line.get("character_ref"))
                if char_id is None:
                    continue
                emotion = line.get("emotion") or ""
                if emotion and emotion not in ALLOWED_EMOTIONS:
                    emotion = "平静"
                char = characters.get_character(char_id)
                lines.append({"index": i, "character_id": char_id,
                              "character_name": char["name"], "text": line["text"],
                              "emotion": emotion or None, "record_id": None})
            scene = dialogue.write_skeleton(sc["name"], eid, lines,
                                            scene_card_ref=sc.get("scene_ref"))
            import_map["scenes"][map_key] = scene["scene_id"]
            created_scenes.append({"scene_id": scene["scene_id"], "name": scene["name"],
                                   "lines": len(lines)})
    _save_import_map(pid, import_map)
    return {
        "project_id": pid, "project_name": project["name"],
        "episodes": len(import_map["episodes"]),
        "characters": bound_notes,
        "scenes_created": created_scenes, "scenes_skipped_existing": skipped,
    }
