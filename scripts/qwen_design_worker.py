"""Run with the isolated Qwen3 environment; input/output paths are server-owned."""
import os
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
import sys
import json
from pathlib import Path
import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
torch.set_num_threads(4)
torch.manual_seed(request['seed'])
torch.cuda.manual_seed_all(request['seed'])
model = Qwen3TTSModel.from_pretrained(sys.argv[3], device_map='cuda:0', dtype=torch.bfloat16, attn_implementation='sdpa')
with torch.inference_mode():
    wavs, sr = model.generate_voice_design(text=request['text'], language='Chinese',
        instruct=request['instruction'], max_new_tokens=800, temperature=.8, top_p=.9, do_sample=True)
wav = np.asarray(wavs[0])
if not wav.size or not np.isfinite(wav).all():
    raise RuntimeError('Invalid generated audio')
sf.write(sys.argv[2], wav, sr, subtype='PCM_16')
