# 本地音频与听感初审

新音色库的“一键本地初审”逐条调用 `/api/voices/{voice_id}/auto-review`，也支持单条运行。音频不上传；初审结果保存在每个 `voice.json` 的 `auto_review`，不覆盖人工 `review_status` 或备注。

## 检查范围

- 信号：空音频、非有限数值、低音量、削波比例、首尾静音、句中长停顿。静音以20ms帧能量估算，并非语音活动识别；正常戏剧停顿也可能触发复核。
- 听感：官方 NISQA-TTS v1.0 的自然度预测。低于3.0标记复核；这是暂定筛选阈值，未经本项目中文动画数据校准，不是行业合格线。模型通常参考1–5分，回归值不做强行截断。
- 不能判断具体错读、定位怪腔、确认角色符合度或情绪。高分不等于人工通过。
- 模型未安装、超时或推理失败会保留错误并显示未完成，不能默认为通过。

## 安装与许可

本机在 `models/NISQA` 安装了官方仓库 https://github.com/gabrielmittag/NISQA ，版本 `fe84f0f252abec382b24367d5b22498a7ce34dbb`。安装到其他机器时需另行获取该版本及 `weights/nisqa_tts.tar`，模型目录不随工程自动提交。

代码采用MIT许可，但权重采用 **CC BY-NC-SA 4.0，仅限非商业使用**。当前为本地实验审核；商业流程须另获授权或替换模型。权重原许可位于 `models/NISQA/weights/LICENSE_model_weights`。

使用现有虚拟环境的 torch、librosa、numpy、pandas、scipy、matplotlib、soundfile、PyYAML、tqdm。CPU独立进程推理，单样本180秒超时，不占用VoxCPM2的GPU；只能加载固定的官方可信checkpoint。首次特征计算可能较慢。

批量本机检查：`.venv\Scripts\python.exe -m scripts.review_new_voices`。输出 `outputs/new-voices-local-review.json`。
