# VoxCPM 功能拆分报告

> v1.1 · 2026-08-14 · 极致克隆已接入声影
> 上游：OpenBMB/VoxCPM（Apache-2.0）；本项目运行时固定使用 PyPI `voxcpm==2.0.3`。
> 目的：把 VoxCPM 拆成可取舍的功能单元，讨论确定**保留哪些功能**，再谈如何适配声影。

---

## 1. 一句话理解 VoxCPM

VoxCPM 是一个**无音频分词器（Tokenizer-Free）的 TTS 系统**：文本 → 连续语音表征 → 扩散解码 → 音频波形。当前主力版本 **VoxCPM2**（2026.04 发布）：MiniCPM-4 基座、2B 参数、200 万小时多语料训练、30 种语言 + 9 种中文方言、原生 48kHz 输出。

历史版本线：VoxCPM-0.5B（2025.09）→ VoxCPM1.5（2025.12，引入微调）→ **VoxCPM2（当前）**。

## 2. 功能面拆分（讨论取舍的主清单）

下表是把 VoxCPM 全部能力拆成的 **12 个功能单元**，每个单元相对独立，可单独决定保留 / 放弃 / 缓议：

| # | 功能单元 | 是什么 | 代码/入口 | 对声影的价值 | 依赖成本 |
| --- | --- | --- | --- | --- | --- |
| A | **基础 TTS** | 纯文本 → 语音，语境感知自动韵律 | `VoxCPM.generate(text=...)` | ★★★ 对白生成底座 | 仅主模型 |
| B | **音色设计** | 文本前加 `(音色描述)` 凭空造音色，无需参考音频 | `generate(text="(描述)台词")` | ★★★ 快速扩充音色库、零版权风险 | 仅主模型 |
| C | **可控克隆** | 参考音频克隆音色 + 括号指令控情绪/语速 | `generate(text="(风格)台词", reference_wav_path=...)` | ★★★ 复用现有音色库样本 | 仅主模型 |
| D | **极致克隆** | 参考音频 + 精确文本，续写式高保真还原 | `generate(prompt_wav_path=..., prompt_text=..., reference_wav_path=...)` | ★★★ 重要角色默认方案，要求逐字转录 | 仅主模型 |
| E | **流式合成** | 边生成边返回音频块，实时试听 | `generate_streaming()` | ★★☆ 试听体验好，但工程坑多 | 仅主模型 |
| F | **参考音频降噪** | ZipEnhancer 先净化参考音频再克隆 | `load_denoiser=True` | ★★☆ 组员录音质量参差时有用 | 额外模型权重 |
| G | **文本规范化** | 数字/符号读法归一（如"2026"→"二零二六"） | `utils/text_normalize.py` | ★★★ 剧本数字、时间常见 | 无 |
| H | **多语言 + 方言** | 30 语言 + 9 方言直接混用，无需标签 | 模型内建 | ★☆☆ 出海短剧预留 | 仅主模型 |
| I | **微调（SFT / LoRA）** | 5–10 分钟音频定制专属音色 | `training/` + `conf/` + `scripts/train_voxcpm_finetune.py` | ★☆☆ 后期音色深度定制再议 | 训练算力 + 数据 |
| J | **时间戳对齐** | 生成语音的词级时间戳 | `timestamps/`（stable-ts） | ★★★ 台词挂载时间线、自动字幕 | 额外模型 |
| K | **Web Demo + ASR 转录** | Gradio 界面；ASR 自动转录参考音频文本 | `app.py` | ★☆☆ 参考其交互设计即可，不内置 | gradio + ASR 模型 |
| L | **生产部署加速** | Nano-vLLM / vLLM-Omni / llama.cpp-omni 端侧 | 外部生态 | ★★☆ 并发/流式阶段再引入 | 额外服务 |

### 初步建议（待讨论确认）

- **第一批保留**：A（基础 TTS）、B（音色设计）、C（可控克隆）、G（文本规范化）、J（时间戳对齐）——正好覆盖声影"对白生成 + 音色库 + 时间线"三条主干。
- **已经接入**：D（极致克隆）；音色样本保存精确转录，自动模式有转录时优先极致克隆。
- **建议缓议**：E（流式，试听阶段再加）、F（降噪，看实际录音质量）、L（部署加速）。
- **建议放弃（本期内置范围外）**：K（Demo 只作参考）、I（微调属训练侧，非软件内置功能）。

## 3. 代码结构拆分（适配时的裁剪地图）

```text
src/voxcpm/
├── core.py                 # VoxCPM 主入口：from_pretrained / generate / generate_streaming / LoRA 管理
├── cli.py                  # 命令行入口
├── model/
│   ├── voxcpm.py           # VoxCPM 1.x 模型实现
│   └── voxcpm2.py          # VoxCPM2 模型实现（我们只用这个）
├── modules/
│   ├── minicpm4/           # LLM 主干（语言理解与语音表征生成）
│   ├── locenc/             # 局部编码器
│   ├── locdit/             # 局部 DiT 扩散解码器（v1 / v2 / unified_cfm）
│   ├── audiovae/           # 声码器（V2 支持 16kHz 参考 → 48kHz 输出超分）
│   └── layers/             # LoRA 层、FSQ 标量量化层
├── utils/text_normalize.py # 文本规范化（功能 G）
├── zipenhancer.py          # 参考音频降噪（功能 F，可选）
├── timestamps/             # 时间戳对齐（功能 J，stable-ts）
└── training/               # SFT / LoRA 微调（功能 I）
app.py                      # Gradio Web Demo（功能 K）
lora_ft_webui.py            # 微调 WebUI（功能 I 的界面）
conf/                       # 各版本微调配置
scripts/                    # 训练与推理测试脚本
examples/                   # 示例音频与训练数据样例
```

**裁剪思路**（若讨论后决定深度内置而非依赖 pip 包）：保留 `core.py` + `model/voxcpm2.py` + `modules/`（去掉 training 相关层可再议）+ `utils/text_normalize.py` + `timestamps/`；剥离 `training/`、`app.py`、`lora_ft_webui.py`、`cli.py`。**更稳妥的默认方案是直接依赖官方 `pip install voxcpm`**，只做服务化封装，升级省心；裁剪 fork 是备选。

## 4. 生成参数一览（API 适配层要暴露的旋钮）

| 参数 | 作用 | 备注 |
| --- | --- | --- |
| `text` | 台词文本；开头 `(描述)` 即音色设计模式 | 功能 B 的入口 |
| `reference_wav_path` | 参考音频（可控克隆） | 对应音色库样本路径 |
| `voice_instruction` | 风格指令（情绪/语速/表现力） | 对应我们的情绪标签 |
| `prompt_wav_path` + `prompt_text` | 极致克隆（续写模式），两者必须同时给 | 需精确转录；同一音频也传给 `reference_wav_path` 可提高相似度 |
| `cfg_value` | 引导尺度（默认 2.0） | 影响表现力/稳定性 |
| `inference_timesteps` | 扩散步数（默认 10） | 质量 vs 速度 |
| 随机种子 | 2.0.3 的 `generate()` 不直接接收 `seed` | 适配层在推理前统一设置 Python、NumPy、PyTorch RNG 并记录 seed |
| `max_len` / `retry_threshold` | 长文本控制与重试 | 长台词兜底 |

## 5. 资源与环境要求

- **Python** ≥ 3.10 且 < 3.13（本机为 3.11.15）；PyTorch/torchaudio 2.9.1 CPU 已验证
- **模型权重**：`openbmb/VoxCPM2`（HuggingFace / ModelScope，数 GB）；ZipEnhancer 与时间戳模型另计
- **显存**：官方未在 README 标明，社区经验 FP16 约 6–8 GB；RTX 4090 上 RTF ≈ 0.3
- **本机设备结论**：Intel Arc Graphics，无 NVIDIA CUDA；当前 VoxCPM 设备选项无 Intel XPU，自动回退 CPU
- 权重不入 git，建议下载到 `models/`（已在 .gitignore）

## 6. 待与组员讨论的决策点

1. 功能单元 A–L 的**保留 / 缓议 / 放弃**结论（第 2 节表格）
2. 集成方式：`pip install voxcpm` 依赖封装（推荐）vs 裁剪源码内置
3. 权重分发：组员各自本地下载 vs 团队共享模型目录
4. 是否需要 VoxCPM1.5 的 LoRA 微调能力（做专属音色资产）
5. 是否增配 NVIDIA CUDA 推理机，以改善批量合成速度
6. 时间戳功能（J）是否纳入——它直接决定台词自动对齐时间线的实现路径
