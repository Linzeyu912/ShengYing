"""Local VoiceDesign subprocess bridge; shares GPU scheduling with VoxCPM2."""
import gc
import io
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
import soundfile as sf
from . import tts, records

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(os.environ.get('QWEN_TTS_PYTHON', 'D:/why/Qwen3-TTS/.venv/Scripts/python.exe'))
MODEL = Path(os.environ.get('QWEN_TTS_MODEL', 'D:/why/Qwen3-TTS-models/VoiceDesign'))


def status():
    return {'ready': PYTHON.is_file() and (MODEL/'model.safetensors').is_file(),
            'model': 'Qwen3-TTS-12Hz-1.7B-VoiceDesign', 'mode': 'design'}


def infer(request):
    if not status()['ready']:
        raise ValueError('本地 Qwen3 环境或模型不存在，请检查安装路径')
    if not tts.engine_lock.acquire(blocking=False):
        raise ValueError('已有语音生成任务运行，请完成后再试')
    try:
        # Free the resident VoxCPM2 model before using the 8GB GPU.
        with tts._lock:
            tts._model = None
            gc.collect()
            import torch
            if torch.cuda.is_available(): torch.cuda.empty_cache()
        get = request.get if isinstance(request, dict) else lambda key, default=None: getattr(request, key, default)
        seed = get('seed') if get('seed') is not None else secrets.randbelow(2**31)
        with tempfile.TemporaryDirectory(prefix='qwen-design-') as directory:
            folder = Path(directory)
            data = dict(text=str(get('text', '')).strip(), instruction=str(get('instruction', '')).strip(), seed=seed)
            (folder/'request.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([str(PYTHON), str(ROOT/'scripts/qwen_design_worker.py'),
                str(folder/'request.json'), str(folder/'audio.wav'), str(MODEL)],
                capture_output=True, cwd=ROOT, timeout=600, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not (folder/'audio.wav').is_file():
                (ROOT/'outputs').mkdir(exist_ok=True)
                (ROOT/'outputs/qwen-design-error.log').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
                raise RuntimeError('Qwen3 生成失败，详情见 outputs/qwen-design-error.log')
            return (folder/'audio.wav').read_bytes(), seed
    finally:
        tts.engine_lock.release()


def generate(request):
    """Compatibility wrapper; new Go API owns record persistence."""
    audio, seed = infer({"text": request.text, "instruction": request.instruction, "seed": request.seed})
    wav, sr = sf.read(io.BytesIO(audio))
    return records.save_record(wav, sr, dict(text=request.text.strip(),
        control_instruction=request.instruction.strip(), mode='design', seed=seed,
        model={'name':'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign'},
        emotion=request.emotion.strip(), gender=request.gender, age_group=request.age_group,
        timbre_tags=[s.strip() for s in request.tags.replace('，', ',').split(',') if s.strip()],
        temperature=.8, top_p=.9, max_new_tokens=800))
