"""Validated uploads and offline VLM inference; no external audio/image service."""
import gc
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageOps
from . import auditions, library, script_dub, tts

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(os.environ.get('QWEN_TTS_PYTHON', 'D:/why/Qwen3-TTS/.venv/Scripts/python.exe'))
MODEL = Path(os.environ.get('CAST_VISION_MODEL', 'D:/why/Qwen3-VL-2B-Instruct'))
FORMATS = {'.jpg','.jpeg','.png','.webp','.bmp'}

def normalize_image(data, name, target):
    if Path(name).suffix.lower() not in FORMATS: raise ValueError('建模图片支持 JPG、JPEG、PNG、WebP、BMP')
    if len(data)>8*1024*1024: raise ValueError('每张图片不能超过8MB')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height>20_000_000: raise ValueError('图片分辨率过大，最多2000万像素')
            if image.format not in {'JPEG','PNG','WEBP','BMP'}: raise ValueError('实际图片格式不支持')
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((768,768))
            image.save(target, format='PNG')
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('图片无法解码或损坏') from exc

def preview(data, filename, uploads, bindings):
    if len(uploads)>12: raise ValueError('一次最多12张建模图片')
    parsed = auditions.preview(data, filename)
    script = '\n'.join(script_dub.extract_script_bytes(data, filename))
    if len(script)>12000: raise ValueError('本地联合分析暂限12000字，请按场次拆分剧本')
    if len(parsed['items'])>20: raise ValueError('本地联合分析一次最多20个角色')
    names = {r['name'] for r in parsed['items']}
    if not isinstance(bindings,dict) or any(k not in {str(i) for i in range(len(uploads))} or (v and v not in names) for k,v in bindings.items()):
        raise ValueError('图片与角色对应关系无效，请重新分析')
    if not PYTHON.is_file() or not (MODEL/'config.json').is_file(): raise ValueError('本地视觉模型尚未安装完成')
    if not tts.engine_lock.acquire(blocking=False): raise ValueError('显卡正在生成语音，请稍后再分析')
    try:
        with tempfile.TemporaryDirectory(prefix='visual-cast-') as directory:
            folder=Path(directory); images=[]
            for i,(name,content) in enumerate(uploads):
                target=folder/f'image_{i}.png'; normalize_image(content,name,target)
                images.append(dict(index=i,name=Path(name).name,path=str(target)))
            voices=[dict(voice_id=v['voice_id'],name=v['name'],gender=v['gender'],age=v.get('age_group',''),
                tags=v.get('timbre_tags',[]),description=v['description'][:200],review=v.get('review_status',''))
                for v in library.get_library()['voices'] if v['samples'] and v['voice_source']!='lora']
            with tts._lock:
                tts._model=None; gc.collect()
                import torch
                if torch.cuda.is_available(): torch.cuda.empty_cache()
            (folder/'input.json').write_text(json.dumps(dict(rows=parsed['items'],script=script,images=images,bindings=bindings,voices=voices),ensure_ascii=False),encoding='utf-8')
            result=subprocess.run([str(PYTHON),str(ROOT/'scripts/visual_cast_worker.py'),str(folder/'input.json'),str(folder/'output.json'),str(MODEL)],
                cwd=ROOT,capture_output=True,timeout=1800,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or not (folder/'output.json').exists():
                (ROOT/'outputs/visual-cast-error.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
                raise RuntimeError('本地图片分析失败，详情见 outputs/visual-cast-error.log；未使用假分析结果')
            output=json.loads((folder/'output.json').read_text(encoding='utf-8'))
            return dict(output,title=parsed['title'],note='本地Qwen3-VL联合分析。无明确角色名的图片需确认归属后重新分析；推荐依据元数据，需试听确认。')
    finally: tts.engine_lock.release()
