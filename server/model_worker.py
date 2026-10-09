"""Local model worker. Business APIs and workbench pages are served by Go."""
import base64
import io

import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from . import audio_review, qwen_design, tts, visual_cast

app = FastAPI(title="声影本地模型 Worker", version="1.0.0")


@app.get("/healthz")
def health():
    return {"ok": True, "role": "model-worker"}


@app.get("/internal/status/voxcpm2")
def voxcpm2_status():
    return tts.runtime_status()


@app.get("/api/qwen/design/status")
def qwen3_status():
    return qwen_design.status()


@app.post("/internal/inference/voxcpm2")
def voxcpm2_inference(req: dict):
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
    return {"audio_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "sample_rate": sample_rate, "frames": len(wav), "used_seed": used_seed,
            "model": tts.model_identity()}


@app.post("/internal/inference/qwen3-voice-design")
def qwen3_voice_design_inference(req: dict):
    audio, seed = qwen_design.infer(req)
    info = sf.info(io.BytesIO(audio))
    return {"audio_base64": base64.b64encode(audio).decode("ascii"),
            "sample_rate": info.samplerate, "frames": info.frames, "used_seed": seed}


@app.post("/internal/inference/nisqa")
def nisqa_inference(req: dict):
    return audio_review.listening_check(str(req.get("path", "")))


@app.post("/api/auditions/visual-preview")
async def visual_preview(file: UploadFile, images: list[UploadFile] = File(default=[]),
                        bindings: str = Form("{}")):
    suffix = (file.filename or "").lower().rsplit(".", 1)[-1]
    if suffix not in {"docx", "txt", "md", "markdown"}:
        raise HTTPException(400, "剧本仅支持 .docx / .txt / .md / .markdown 文件")
    script = await file.read(20 * 1024 * 1024 + 1)
    if not script or len(script) > 20 * 1024 * 1024:
        raise HTTPException(400, "剧本为空或超过20MB")
    if len(images) > 12:
        raise HTTPException(400, "一次最多12张图片")
    uploads = []
    for image in images:
        data = await image.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(400, "每张图片不能超过8MB")
        uploads.append((image.filename or "", data))
    try:
        result = visual_cast.preview(script, file.filename or "script.txt", uploads,
                                     __import__("json").loads(bindings))
        return result
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
