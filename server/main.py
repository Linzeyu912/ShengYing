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


# 浏览试听页（静态页，最后挂载，避免覆盖 /api 路由）
app.mount("/", StaticFiles(directory="server/static", html=True), name="static")
