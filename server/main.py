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


@app.post("/api/dialogue/batch")
def dialogue_batch(req: BatchRequest):
    if not req.lines:
        raise HTTPException(400, "台词列表为空")
    try:
        return dialogue.run_batch(req.scene_name, [l.model_dump() for l in req.lines])
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


# 浏览试听页（静态页，最后挂载，避免覆盖 /api 路由）
app.mount("/", StaticFiles(directory="server/static", html=True), name="static")
