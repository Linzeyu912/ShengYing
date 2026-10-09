# -*- coding: utf-8 -*-
"""声影 · 素材库服务（M2）

启动方式（项目根目录）：
    .venv/Scripts/python -m uvicorn server.main:app --reload --port 8317
浏览器打开 http://localhost:8317/ 进入素材浏览试听页。
"""
import json
import re
import uuid
import base64
import io
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool


from . import library, script_dub, tts

app = FastAPI(title="声影素材库服务", version="0.2.0")
SCRIPT_DUB_OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "exports" / "script_dubs"


@app.post("/internal/inference/voxcpm2")
def voxcpm2_inference(req: dict):
    """Model-only worker contract. Go owns voice resolution and record persistence."""
    import soundfile as sf
    wav, sample_rate, used_seed = tts.synthesize(
        text=str(req.get("text", "")),
        reference_wav_path=req.get("reference_wav_path"),
        prompt_wav_path=req.get("prompt_wav_path"),
        prompt_text=req.get("prompt_text"),
        seed=req.get("seed"),
        cfg_value=float(req.get("cfg_value", 2.0)),
        inference_timesteps=int(req.get("inference_timesteps", 10)),
        normalize=bool(req.get("normalize", False)),
        denoise=bool(req.get("denoise", False)),
    )
    buffer = io.BytesIO()
    sf.write(buffer, wav, sample_rate, format="WAV")
    return {
        "audio_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "sample_rate": sample_rate,
        "used_seed": used_seed,
        "frames": len(wav),
        "model": tts.model_identity(),
    }


@app.post("/internal/inference/qwen3-voice-design")
def qwen3_voice_design_inference(req: dict):
    """Model-only Qwen3 VoiceDesign worker; Go stores the resulting record."""
    import soundfile as sf
    from . import qwen_design
    audio, seed = qwen_design.infer(req)
    info = sf.info(io.BytesIO(audio))
    return {"audio_base64": base64.b64encode(audio).decode("ascii"),
            "sample_rate": info.samplerate, "frames": info.frames, "used_seed": seed}


@app.post("/internal/inference/nisqa")
def nisqa_inference(req: dict):
    """Naturalness-model inference only; Go runs signal checks and saves reports."""
    from .audio_review import listening_check
    return listening_check(str(req.get("path", "")))


async def _read_script_upload(file: UploadFile) -> tuple[bytes, str]:
    filename = file.filename or ""
    if Path(filename).suffix.lower() not in (".docx", ".txt", ".md", ".markdown"):
        raise HTTPException(400, "剧本仅支持 .docx / .txt / .md / .markdown 文件")
    data = await file.read()
    if not data:
        raise HTTPException(400, "剧本文件为空")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "剧本文件超过 20MB")
    return data, filename


@app.post("/api/script-dub/preview")
async def preview_script_dub(file: UploadFile):
    data, filename = await _read_script_upload(file)
    try:
        return script_dub.preview_script_bytes(data, filename)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/script-dub/generate")
async def generate_script_dub(
    file: UploadFile,
    cast_json: str = Form("{}"),
    scene_name: str = Form(""),
    episode_id: str = Form(""),
):
    data, filename = await _read_script_upload(file)
    if episode_id and projects.get_episode(episode_id) is None:
        raise HTTPException(404, "所选剧集不存在")
    try:
        cast = json.loads(cast_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "人物音色映射不是有效 JSON") from exc
    if not isinstance(cast, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in cast.items()):
        raise HTTPException(400, "人物音色映射格式不正确")
    invalid = [voice_id for voice_id in cast.values() if library.get_voice(voice_id) is None]
    if invalid:
        raise HTTPException(400, f"音色不存在: {invalid[0]}")

    try:
        result = await run_in_threadpool(
            script_dub.dub_script_bytes,
            data, filename, cast, None,
            scene_name.strip(), 500, episode_id, False,
        )
    except (ValueError, FileNotFoundError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"剧本配音失败: {exc}")
    result.pop("out_path", None)
    return result


@app.post("/api/script-dub/merge")
async def merge_script_dub(scene_id: str = Form(...), gap_ms: int = Form(500)):
    job_id = uuid.uuid4().hex
    output_path = SCRIPT_DUB_OUTPUT_ROOT / job_id / "dialogue.wav"
    try:
        result = await run_in_threadpool(
            script_dub.merge_scene, scene_id, output_path, gap_ms)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"音频合并失败: {exc}")
    result.pop("out_path", None)
    return {**result, "job_id": job_id,
            "audio_url": f"/api/script-dub/{job_id}/audio"}


@app.get("/api/script-dub/{job_id}/audio")
def script_dub_audio(job_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "配音结果不存在")
    path = SCRIPT_DUB_OUTPUT_ROOT / job_id / "dialogue.wav"
    if not path.is_file():
        raise HTTPException(404, "配音结果不存在")
    return FileResponse(path, media_type="audio/wav", filename=f"script-dub-{job_id[:8]}.wav")


@app.get("/api/system/tts-status")
def tts_status():
    return {**tts.runtime_status(), "voice_assets": library.voice_asset_status()}


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


@app.post("/api/voices/import")
async def import_voice(
    file: UploadFile,
    name: str = Form(...),
    transcript: str = Form(...),
    gender: str = Form("unknown"),
    emotion: str = Form("平静"),
    description: str = Form(""),
    language: str = Form("zh"),
    license_name: str = Form("authorized"),
    consent_confirmed: bool = Form(False),
):
    """导入一段已授权 WAV，并保存为可复用的极致克隆音色。"""
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "参考音频超过 50MB")
    try:
        return library.import_voice(
            audio_data=data,
            filename=file.filename or "reference.wav",
            name=name,
            transcript=transcript,
            gender=gender,
            emotion=emotion,
            description=description,
            language=language,
            license_name=license_name,
            consent_confirmed=consent_confirmed,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


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
    media_type = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.post("/api/library/refresh")
def refresh_library():
    library.get_library(force=True)
    return {"ok": True}


# ---------------- TTS 合成与生成记录（M3） ----------------

from pydantic import BaseModel

from . import characters, dialogue, records, synthesis


class GenerateRequest(BaseModel):
    text: str
    control_instruction: str = ""  # VoxCPM2 可控克隆/音色设计的风格描述
    voice_id: str = ""          # 库内音色：clone 模式取参考样本；固化音色复现其参数
    emotion: str = ""           # 指定用该情绪的样本作克隆参考
    seed: int | None = None     # 不传则随机生成并记录在案
    cfg_value: float = 2.0
    inference_timesteps: int = 10
    mode: str = "auto"
    prompt_text: str = ""       # 可覆盖音色样本内保存的精确转录
    normalize: bool = False
    denoise: bool = False


class VoiceReviewRequest(BaseModel):
    status: str
    notes: str = ""


class QwenDesignRequest(BaseModel):
    text: str
    instruction: str
    seed: int | None = None
    gender: str = "unknown"
    age_group: str = "未标注"
    tags: str = ""
    emotion: str = "参考"


@app.get('/api/qwen/design/status')
def qwen_design_status():
    from . import qwen_design
    return qwen_design.status()


@app.post('/api/qwen/design/generate')
def qwen_design_generate(req: QwenDesignRequest):
    from . import qwen_design
    if not 1 <= len(req.text.strip()) <= 200 or not 1 <= len(req.instruction.strip()) <= 1500:
        raise HTTPException(400, '台词需为1–200字，音色描述需为1–1500字')
    if req.gender not in ('male', 'female', 'unknown') or (req.seed is not None and not 0 <= req.seed < 2**31):
        raise HTTPException(400, '性别或随机种子无效')
    if any(len(value) > 200 for value in (req.age_group, req.tags, req.emotion)):
        raise HTTPException(400, '标签内容过长')
    try:
        return qwen_design.generate(req)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(500, f'Qwen3生成未完成：{exc}')


@app.post("/api/voices/{voice_id}/auto-review")
def auto_review_voice(voice_id: str):
    from . import audio_review
    try:
        return audio_review.review_voice(voice_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/voices/{voice_id}/review")
def review_voice(voice_id: str, req: VoiceReviewRequest):
    if len(req.notes) > 2000:
        raise HTTPException(400, "问题说明不能超过2000字")
    try:
        return library.save_voice_review(voice_id, req.status, req.notes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


from pydantic import Field
from . import auditions


class AuditionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    control: str = Field(default='自然清晰的普通话', min_length=1, max_length=1000)
    text: str = Field(min_length=1, max_length=1000)
    emotion: str = Field(default='平静', max_length=30)
    voice_id: str = Field(default='', max_length=100)


@app.post('/api/auditions/preview')
async def audition_preview(file: UploadFile):
    data, filename = await _read_script_upload(file)
    try:
        return await run_in_threadpool(auditions.preview, data, filename)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc))


@app.post('/api/auditions/visual-preview')
async def audition_visual_preview(file: UploadFile, images: list[UploadFile] = File(default=[]), bindings: str = Form('{}')):
    from . import visual_cast
    data, filename = await _read_script_upload(file)
    if len(images)>12: raise HTTPException(400, '一次最多12张图片')
    uploads=[]
    for image in images:
        content=await image.read(8*1024*1024+1)
        if len(content)>8*1024*1024: raise HTTPException(400, '每张图片不能超过8MB')
        uploads.append((image.filename or '',content))
    try:
        mapping=json.loads(bindings)
        return await run_in_threadpool(visual_cast.preview,data,filename,uploads,mapping)
    except (ValueError, UnicodeDecodeError) as exc: raise HTTPException(400,str(exc))
    except Exception as exc: raise HTTPException(500,str(exc))


@app.post('/api/auditions/generate')
def audition_generate(req: AuditionRequest):
    if not req.name.strip() or not req.text.strip() or not req.control.strip():
        raise HTTPException(400, '角色名、试音台词和声音描述不能为空')
    try:
        return auditions.generate(req)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f'角色试音失败：{exc}')


@app.post("/api/tts/generate")
def tts_generate(req: GenerateRequest):
    if req.voice_id and library.get_voice(req.voice_id) is None:
        raise HTTPException(404, f"音色不存在: {req.voice_id}")
    try:
        wav, sr, meta = synthesis.generate(
            text=req.text,
            control_instruction=req.control_instruction,
            voice_id=req.voice_id,
            emotion=req.emotion,
            requested_mode=req.mode,
            prompt_text_override=req.prompt_text,
            seed=req.seed,
            cfg_value=req.cfg_value,
            inference_timesteps=req.inference_timesteps,
            normalize=req.normalize,
            denoise=req.denoise,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"合成失败: {e}")
    return records.save_record(wav, sr, meta)


# ---------------- 角色管理（M3 后半） ----------------

class CharacterRequest(BaseModel):
    name: str
    voice_id: str = ""
    default_emotion: str = ""
    description: str = ""
    clone_mode: str = "auto"


@app.get("/api/characters")
def list_chars():
    return {"items": characters.list_characters()}


@app.post("/api/characters")
def create_char(req: CharacterRequest):
    if req.voice_id and library.get_voice(req.voice_id) is None:
        raise HTTPException(404, f"音色不存在: {req.voice_id}")
    if req.clone_mode not in synthesis.VALID_MODES - {"basic", "design"}:
        raise HTTPException(400, "角色克隆模式仅支持 auto / controllable_clone / ultimate_clone")
    return characters.create_character(
        req.name, req.voice_id, req.default_emotion, req.description,
        clone_mode=req.clone_mode)


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


class SceneOrderRequest(BaseModel):
    scene_ids: list[str]


@app.post("/api/episodes/{eid}/scene_order")
def set_scene_order(eid: str, req: SceneOrderRequest):
    ep = projects.get_episode(eid)
    if ep is None:
        raise HTTPException(404, f"剧集不存在: {eid}")
    mine = {s["scene_id"] for s in dialogue.list_scenes() if s.get("episode_id") == eid}
    bad = [sid for sid in req.scene_ids if sid not in mine]
    if bad:
        raise HTTPException(400, f"场次不属于该剧集: {bad}")
    return projects.set_scene_order(eid, req.scene_ids)


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
