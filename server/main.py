# -*- coding: utf-8 -*-
"""声影 · 素材库服务（M2）

启动方式（项目根目录）：
    .venv/Scripts/python -m uvicorn server.main:app --reload --port 8317
浏览器打开 http://localhost:8317/ 进入素材浏览试听页。
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import library

app = FastAPI(title="声影素材库服务", version="0.2.0")


@app.get("/api/library/summary")
def library_summary():
    lib = library.get_library()
    return {
        "voices": len(lib["voices"]),
        "voice_samples": sum(len(v["samples"]) for v in lib["voices"]),
        "sfx": len(lib["sfx"]),
        "ambience": len(lib["ambience"]),
        "emotions": library.EMOTIONS,
    }


@app.get("/api/voices")
def list_voices(
    q: str = Query("", description="关键词（名称 / id / 描述）"),
    gender: str = Query("", description="male / female / unknown"),
    emotion: str = Query("", description="按情绪过滤，且仅返回该情绪样本"),
):
    return {"items": library.search_voices(q=q, gender=gender, emotion=emotion)}


@app.get("/api/voices/{voice_id}")
def voice_detail(voice_id: str):
    voice = library.get_voice(voice_id)
    if voice is None:
        raise HTTPException(404, f"音色不存在: {voice_id}")
    return voice


@app.get("/api/voices/{voice_id}/audio/{filename}")
def voice_sample_audio(voice_id: str, filename: str):
    path = library.resolve_voice_sample(voice_id, filename)
    if path is None:
        raise HTTPException(404, "样本不存在")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.get("/api/assets/{kind}")
def list_simple_assets(kind: str):
    if kind not in ("sfx", "ambience"):
        raise HTTPException(404, "素材类型仅支持 sfx / ambience")
    return {"items": library.get_library()[kind]}


@app.get("/api/assets/{kind}/audio/{relpath:path}")
def simple_asset_audio(kind: str, relpath: str):
    path = library.resolve_asset_file(kind, relpath)
    if path is None:
        raise HTTPException(404, "素材不存在")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.post("/api/library/refresh")
def refresh_library():
    library.get_library(force=True)
    return {"ok": True}


# ---------------- TTS 合成与生成记录（M3） ----------------

from pydantic import BaseModel

from . import characters, dialogue, records, tts


class GenerateRequest(BaseModel):
    text: str
    voice_id: str = ""          # 库内音色：clone 模式取参考样本；固化音色复现其参数
    emotion: str = ""           # 指定用该情绪的样本作克隆参考
    seed: int | None = None     # 不传则随机生成并记录在案
    cfg_value: float = 2.0
    inference_timesteps: int = 10


@app.post("/api/tts/generate")
def tts_generate(req: GenerateRequest):
    reference, gen_params = library.resolve_voice_reference(req.voice_id, req.emotion)
    if req.voice_id and library.get_voice(req.voice_id) is None:
        raise HTTPException(404, f"音色不存在: {req.voice_id}")
    text = req.text
    seed, cfg, steps = req.seed, req.cfg_value, req.inference_timesteps
    if gen_params:  # 固化音色：复现描述前缀与参数（可被请求覆盖）
        src_text = gen_params.get("text") or ""
        if src_text.startswith("(") and ")" in src_text:
            text = src_text[: src_text.index(")") + 1] + text
        seed = seed if seed is not None else gen_params.get("seed")
        cfg = gen_params.get("cfg_value", cfg)
        steps = gen_params.get("inference_timesteps", steps)
        reference = gen_params.get("reference_wav_path") or reference
    try:
        wav, sr, used_seed = tts.synthesize(
            text=text, reference_wav_path=reference,
            seed=seed, cfg_value=cfg, inference_timesteps=steps)
    except Exception as e:
        raise HTTPException(500, f"合成失败: {e}")
    record = records.save_record(wav, sr, {
        "text": req.text, "mode": tts.detect_mode(text, reference),
        "voice_id": req.voice_id or None, "emotion": req.emotion or None,
        "seed": used_seed, "cfg_value": cfg, "inference_timesteps": steps,
        "reference_wav_path": reference,
    })
    return record


# ---------------- 角色管理（M3 后半） ----------------

class CharacterRequest(BaseModel):
    name: str
    voice_id: str = ""
    default_emotion: str = ""
    description: str = ""


@app.get("/api/characters")
def list_chars():
    return {"items": characters.list_characters()}


@app.post("/api/characters")
def create_char(req: CharacterRequest):
    if req.voice_id and library.get_voice(req.voice_id) is None:
        raise HTTPException(404, f"音色不存在: {req.voice_id}")
    return characters.create_character(req.name, req.voice_id, req.default_emotion, req.description)


@app.put("/api/characters/{char_id}")
def update_char(char_id: str, req: CharacterRequest):
    char = characters.update_character(char_id, **req.model_dump())
    if char is None:
        raise HTTPException(404, f"角色不存在: {char_id}")
    return char


@app.delete("/api/characters/{char_id}")
def delete_char(char_id: str):
    if not characters.delete_character(char_id):
        raise HTTPException(404, f"角色不存在: {char_id}")
    return {"ok": True}


# ---------------- 对白批量合成与场次 ----------------

class DialogueLine(BaseModel):
    character_id: str
    text: str
    emotion: str = ""


class BatchRequest(BaseModel):
    scene_name: str
    lines: list[DialogueLine]
    episode_id: str = ""


@app.post("/api/dialogue/batch")
def dialogue_batch(req: BatchRequest):
    if not req.lines:
        raise HTTPException(400, "台词列表为空")
    try:
        return dialogue.run_batch(req.scene_name, [l.model_dump() for l in req.lines], req.episode_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"批量合成失败: {e}")


@app.get("/api/dialogue/scenes")
def dialogue_scenes():
    return {"items": dialogue.list_scenes()}


@app.get("/api/dialogue/scenes/{scene_id}")
def dialogue_scene_detail(scene_id: str):
    scene = dialogue.get_scene(scene_id)
    if scene is None:
        raise HTTPException(404, f"场次不存在: {scene_id}")
    return scene


@app.get("/api/tts/records")
def tts_records():
    return {"items": records.list_records()}


@app.get("/api/tts/records/{rid}/audio")
def tts_record_audio(rid: str):
    path = records.resolve_audio(rid)
    if path is None:
        raise HTTPException(404, "记录不存在")
    return FileResponse(path, media_type="audio/wav", filename=f"{rid}.wav")


class PromoteRequest(BaseModel):
    name: str
    gender: str = "unknown"


@app.post("/api/tts/records/{rid}/promote")
def tts_promote(rid: str, req: PromoteRequest):
    try:
        return records.promote_to_voice(rid, req.name, req.gender)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- 项目与剧集（M5） ----------------

from . import projects


class ProjectRequest(BaseModel):
    name: str
    description: str = ""


@app.get("/api/projects")
def list_projs():
    return {"items": projects.list_projects()}


@app.post("/api/projects")
def create_proj(req: ProjectRequest):
    return projects.create_project(req.name, req.description)


@app.delete("/api/projects/{pid}")
def delete_proj(pid: str):
    if not projects.delete_project(pid):
        raise HTTPException(404, f"项目不存在: {pid}")
    return {"ok": True}


class EpisodeRequest(BaseModel):
    name: str
    number: int = 1
    description: str = ""


@app.get("/api/projects/{pid}/episodes")
def list_eps(pid: str):
    if projects.get_project(pid) is None:
        raise HTTPException(404, f"项目不存在: {pid}")
    return {"items": projects.list_episodes(pid)}


@app.post("/api/projects/{pid}/episodes")
def create_ep(pid: str, req: EpisodeRequest):
    try:
        return projects.create_episode(pid, req.name, req.number, req.description)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/episodes/{eid}")
def delete_ep(eid: str):
    if not projects.delete_episode(eid):
        raise HTTPException(404, f"剧集不存在: {eid}")
    return {"ok": True}


class AssignRequest(BaseModel):
    episode_id: str


@app.post("/api/dialogue/scenes/{scene_id}/assign")
def assign_scene(scene_id: str, req: AssignRequest):
    try:
        return dialogue.assign_scene(scene_id, req.episode_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- 整集导出 ----------------

from . import exporter


class ExportRequest(BaseModel):
    scene_gap_ms: int = 1000
    auto_render: bool = True


@app.post("/api/episodes/{eid}/export")
def export_ep(eid: str, req: ExportRequest):
    try:
        return exporter.export_episode(eid, req.scene_gap_ms, req.auto_render)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"导出失败: {e}")


@app.get("/api/episodes/{eid}/export/audio")
def export_audio(eid: str):
    path = exporter.resolve_export(eid)
    if path is None:
        raise HTTPException(404, "整集尚未导出")
    return FileResponse(path, media_type="audio/wav", filename=f"{eid}_export.wav")


# ---------------- 资产包导入（群像对接） ----------------

from . import importer


class ImportRequest(BaseModel):
    path: str


@app.post("/api/import/preview")
def import_preview(req: ImportRequest):
    try:
        return importer.preview(req.path)
    except importer.PackageError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, f"资产包文件缺失: {e}")


@app.post("/api/import/execute")
def import_execute(req: ImportRequest):
    try:
        return importer.execute(req.path)
    except importer.PackageError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, f"资产包文件缺失: {e}")


@app.post("/api/dialogue/scenes/{scene_id}/generate")
def generate_draft_lines(scene_id: str):
    try:
        return dialogue.generate_scene_lines(scene_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"生成失败: {e}")


# ---------------- 混音与素材导入（M4） ----------------

from fastapi import UploadFile

from . import mixer


class SfxCue(BaseModel):
    id: str
    at_line: int = 0
    volume: float = 0.8


class MixRequest(BaseModel):
    gap_ms: int = 500
    dialogue_volume: float = 1.0
    ambience: dict | None = None       # {id, volume}
    sfx: list[SfxCue] = []


@app.post("/api/dialogue/scenes/{scene_id}/mix")
def render_mix(scene_id: str, req: MixRequest):
    try:
        return mixer.render_scene_mix(scene_id, req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/dialogue/scenes/{scene_id}/mix/audio/{track}")
def mix_audio(scene_id: str, track: str):
    if track not in ("mix", "dialogue"):
        raise HTTPException(404, "track 仅支持 mix / dialogue")
    base = dialogue.SCENES_ROOT.resolve()
    path = (base / scene_id / f"{track}.wav").resolve()
    if not path.is_file() or path.parent.parent != base:
        raise HTTPException(404, "混音尚未渲染，请先调用 mix 接口")
    return FileResponse(path, media_type="audio/wav", filename=f"{scene_id}_{track}.wav")


@app.post("/api/assets/{kind}/upload")
async def upload_asset(kind: str, file: UploadFile, category: str = "未分类"):
    """上传 wav 到音效库 / 环境音库。"""
    if kind not in ("sfx", "ambience"):
        raise HTTPException(404, "素材类型仅支持 sfx / ambience")
    if not file.filename.lower().endswith(".wav"):
        raise HTTPException(400, "仅支持 .wav 文件")
    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(400, "文件超过 100MB")
    safe_name = "".join(c for c in file.filename if c not in '\\/:*?"<>|')
    safe_cat = "".join(c for c in category if c not in '\\/:*?"<>|') or "未分类"
    dest_dir = library.ASSETS_ROOT / kind / safe_cat
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    if dest.exists():
        raise HTTPException(409, f"同名素材已存在: {safe_cat}/{safe_name}")
    dest.write_bytes(data)
    library.get_library(force=True)
    return {"ok": True, "path": f"{kind}/{safe_cat}/{safe_name}", "size_bytes": len(data)}


# 浏览试听页（静态页，最后挂载，避免覆盖 /api 路由）
app.mount("/", StaticFiles(directory="server/static", html=True), name="static")
