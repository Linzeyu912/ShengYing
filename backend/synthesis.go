package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type synthAPI struct {
	root   string
	worker *url.URL
}

type ttsRequest struct {
	Text       string  `json:"text"`
	Control    string  `json:"control_instruction"`
	VoiceID    string  `json:"voice_id"`
	Emotion    string  `json:"emotion"`
	Seed       *int64  `json:"seed"`
	CFG        float64 `json:"cfg_value"`
	Steps      int     `json:"inference_timesteps"`
	Mode       string  `json:"mode"`
	PromptText string  `json:"prompt_text"`
	Normalize  bool    `json:"normalize"`
	Denoise    bool    `json:"denoise"`
}

func registerSynthesisRoutes(mux *http.ServeMux, root string, worker *url.URL) {
	a := &synthAPI{root: root, worker: worker}
	mux.HandleFunc("POST /api/tts/generate", a.generate)
	mux.HandleFunc("GET /api/system/tts-status", a.status)
	mux.HandleFunc("POST /api/qwen/design/generate", a.qwenDesign)
	mux.HandleFunc("POST /api/dialogue/batch", a.dialogueBatch)
	mux.HandleFunc("POST /api/dialogue/scenes/{sceneID}/generate", a.generateSceneLines)
}

func (a *synthAPI) status(w http.ResponseWriter, r *http.Request) {
	worker := *a.worker
	worker.Path = strings.TrimSuffix(worker.Path, "/") + "/internal/status/voxcpm2"
	call, _ := http.NewRequestWithContext(r.Context(), http.MethodGet, worker.String(), nil)
	response, err := (&http.Client{Timeout: 10 * time.Second}).Do(call)
	if err != nil {
		apiError(w, 502, "模型状态服务不可用: "+err.Error())
		return
	}
	defer response.Body.Close()
	var status map[string]any
	if response.StatusCode < 200 || response.StatusCode >= 300 || json.NewDecoder(response.Body).Decode(&status) != nil {
		apiError(w, 502, "模型状态返回无效")
		return
	}
	entries, _ := os.ReadDir(filepath.Join(a.root, "assets", "voices"))
	declared, missing, ultimate := 0, 0, 0
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		voice, dir, err := (&libraryAPI{root: a.root}).loadVoice(entry.Name())
		if err != nil {
			continue
		}
		for _, sample := range listMap(voice["samples"]) {
			declared++
			path := filepath.Join(dir, filepath.FromSlash(mapText(sample, "file")))
			if _, err := os.Stat(path); err != nil {
				missing++
			} else if mapText(sample, "transcript") != "" {
				ultimate++
			}
		}
	}
	status["voice_assets"] = map[string]any{"declared_samples": declared, "missing_samples": missing, "ultimate_ready_samples": ultimate}
	writeJSON(w, 200, status)
}

func (a *synthAPI) qwenDesign(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Text        string `json:"text"`
		Instruction string `json:"instruction"`
		Seed        *int64 `json:"seed"`
		Gender      string `json:"gender"`
		AgeGroup    string `json:"age_group"`
		Tags        string `json:"tags"`
		Emotion     string `json:"emotion"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil {
		apiError(w, 400, "请求内容无效")
		return
	}
	req.Text = strings.TrimSpace(req.Text)
	req.Instruction = strings.TrimSpace(req.Instruction)
	if len([]rune(req.Text)) < 1 || len([]rune(req.Text)) > 200 || len([]rune(req.Instruction)) < 1 || len([]rune(req.Instruction)) > 1500 {
		apiError(w, 400, "台词需为1–200字，音色描述需为1–1500字")
		return
	}
	if req.Gender == "" {
		req.Gender = "unknown"
	}
	if req.Gender != "male" && req.Gender != "female" && req.Gender != "unknown" {
		apiError(w, 400, "性别无效")
		return
	}
	if req.Seed != nil && (*req.Seed < 0 || *req.Seed >= 1<<31) {
		apiError(w, 400, "随机种子无效")
		return
	}
	for _, v := range []string{req.AgeGroup, req.Tags, req.Emotion} {
		if len([]rune(v)) > 200 {
			apiError(w, 400, "标签内容过长")
			return
		}
	}
	workerURL := *a.worker
	workerURL.Path = strings.TrimSuffix(workerURL.Path, "/") + "/internal/inference/qwen3-voice-design"
	payload, _ := json.Marshal(map[string]any{"text": req.Text, "instruction": req.Instruction, "seed": req.Seed})
	call, err := http.NewRequestWithContext(r.Context(), http.MethodPost, workerURL.String(), bytes.NewReader(payload))
	if err != nil {
		apiError(w, 500, err.Error())
		return
	}
	call.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: 12 * time.Minute}).Do(call)
	if err != nil {
		apiError(w, 502, "Qwen3 推理服务不可用: "+err.Error())
		return
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		detail, _ := io.ReadAll(io.LimitReader(response.Body, 8192))
		apiError(w, 502, "Qwen3 生成失败: "+strings.TrimSpace(string(detail)))
		return
	}
	var output struct {
		Audio  string `json:"audio_base64"`
		Rate   int    `json:"sample_rate"`
		Frames int    `json:"frames"`
		Seed   int64  `json:"used_seed"`
	}
	if json.NewDecoder(response.Body).Decode(&output) != nil || output.Audio == "" || output.Rate < 1 {
		apiError(w, 502, "Qwen3 推理返回数据无效")
		return
	}
	audio, err := base64.StdEncoding.DecodeString(output.Audio)
	if err != nil {
		apiError(w, 502, "Qwen3 音频解码失败")
		return
	}
	rid := time.Now().Format("20060102_150405") + "_" + randomID("")[:6]
	dir := filepath.Join(a.root, "generations", rid)
	if err := os.MkdirAll(dir, 0755); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	if err := os.WriteFile(filepath.Join(dir, "audio.wav"), audio, 0644); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	tags := make([]string, 0)
	for _, tag := range strings.Split(strings.ReplaceAll(req.Tags, "，", ","), ",") {
		if strings.TrimSpace(tag) != "" {
			tags = append(tags, strings.TrimSpace(tag))
		}
	}
	frames := output.Frames
	if frames < 1 {
		frames = output.Rate * 2
	}
	record := map[string]any{"record_id": rid, "created_at": time.Now().Format("2006-01-02 15:04:05"), "sample_rate": output.Rate, "duration_sec": math.Round(float64(frames)/float64(output.Rate)*100) / 100, "review_status": "pending", "text": req.Text, "control_instruction": req.Instruction, "mode": "design", "seed": output.Seed, "model": map[string]any{"name": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"}, "emotion": req.Emotion, "gender": req.Gender, "age_group": req.AgeGroup, "timbre_tags": tags, "temperature": 0.8, "top_p": 0.9, "max_new_tokens": 800}
	if err := writeObject(filepath.Join(dir, "record.json"), record); err != nil {
		apiError(w, 500, "保存生成记录失败: "+err.Error())
		return
	}
	writeJSON(w, 200, record)
}

func (a *synthAPI) generateRecord(req ttsRequest, extra map[string]any) (map[string]any, error) {
	body, _ := json.Marshal(req)
	r := httptest.NewRequest(http.MethodPost, "/api/tts/generate", bytes.NewReader(body))
	w := httptest.NewRecorder()
	a.generate(w, r)
	var result map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		return nil, fmt.Errorf("TTS 返回内容无效: %s", w.Body.String())
	}
	if w.Code < 200 || w.Code >= 300 {
		return nil, fmt.Errorf("%s", mapText(result, "detail"))
	}
	for key, value := range extra {
		result[key] = value
	}
	if len(extra) > 0 {
		if err := writeObject(filepath.Join(a.root, "generations", mapText(result, "record_id"), "record.json"), result); err != nil {
			return nil, fmt.Errorf("保存生成记录失败: %w", err)
		}
	}
	return result, nil
}

func (a *synthAPI) dialogueBatch(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Name      string           `json:"scene_name"`
		Lines     []map[string]any `json:"lines"`
		EpisodeID string           `json:"episode_id"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil || len(req.Lines) == 0 {
		apiError(w, 400, "台词列表为空")
		return
	}
	projectID, episodeName := "", ""
	if req.EpisodeID != "" {
		ep, _, err := (&workflowAPI{root: a.root}).episode(req.EpisodeID)
		if err != nil {
			apiError(w, 400, "剧集不存在: "+req.EpisodeID)
			return
		}
		projectID = mapText(ep, "project_id")
		episodeName = mapText(ep, "name")
	}
	sceneID := time.Now().Format("20060102_150405") + "_" + randomID("")[:4]
	lines := make([]any, 0, len(req.Lines))
	for i, line := range req.Lines {
		charID := mapText(line, "character_id")
		if !workflowIDPattern.MatchString(charID) {
			apiError(w, 400, fmt.Sprintf("第 %d 行角色不存在: %s", i+1, charID))
			return
		}
		char, err := readObject(filepath.Join(a.root, "characters", charID, "character.json"))
		if err != nil {
			apiError(w, 400, "角色不存在: "+charID)
			return
		}
		text := mapText(line, "text")
		emotion := mapText(line, "emotion")
		if emotion == "" {
			emotion = mapText(char, "default_emotion")
		}
		voiceID := mapText(char, "voice_id")
		if voiceID == "" && !strings.HasPrefix(strings.TrimSpace(text), "(") && !strings.HasPrefix(strings.TrimSpace(text), "（") {
			apiError(w, 400, "角色「"+mapText(char, "name")+"」未绑定音色，无法合成")
			return
		}
		record, err := a.generateRecord(ttsRequest{Text: text, VoiceID: voiceID, Emotion: emotion, Mode: mapText(char, "clone_mode")}, map[string]any{"scene_id": sceneID, "character_id": charID, "line_index": i})
		if err != nil {
			apiError(w, 502, fmt.Sprintf("第 %d 行合成失败: %v", i+1, err))
			return
		}
		lines = append(lines, map[string]any{"index": i, "character_id": charID, "character_name": mapText(char, "name"), "text": mapText(record, "text"), "emotion": optionalValue(emotion), "record_id": record["record_id"], "duration_sec": record["duration_sec"], "seed": record["seed"], "mode": record["mode"]})
	}
	scene := map[string]any{"scene_id": sceneID, "name": req.Name, "project_id": optionalValue(projectID), "episode_id": optionalValue(req.EpisodeID), "episode_name": optionalValue(episodeName), "created_at": time.Now().Format("2006-01-02 15:04:05"), "line_count": len(lines), "lines": lines}
	if err := writeObject(filepath.Join(a.root, "scenes", sceneID, "scene.json"), scene); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, scene)
}

func (a *synthAPI) generateSceneLines(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("sceneID")
	scene, err := readObject(filepath.Join(a.root, "scenes", id, "scene.json"))
	if err != nil || !workflowIDPattern.MatchString(id) {
		apiError(w, 404, "场次不存在: "+id)
		return
	}
	lines := listMap(scene["lines"])
	for i, line := range lines {
		if mapText(line, "record_id") != "" {
			continue
		}
		charID := mapText(line, "character_id")
		char, err := readObject(filepath.Join(a.root, "characters", charID, "character.json"))
		if err != nil {
			apiError(w, 400, "角色不存在: "+charID)
			return
		}
		emotion := mapText(line, "emotion")
		if emotion == "" {
			emotion = mapText(char, "default_emotion")
		}
		record, err := a.generateRecord(ttsRequest{Text: mapText(line, "text"), VoiceID: mapText(char, "voice_id"), Emotion: emotion, Mode: mapText(char, "clone_mode")}, map[string]any{"scene_id": id, "character_id": charID, "line_index": mapNumber(line, "index", float64(i))})
		if err != nil {
			apiError(w, 502, fmt.Sprintf("第 %d 行合成失败: %v", i+1, err))
			return
		}
		line["emotion"] = optionalValue(emotion)
		line["record_id"] = record["record_id"]
		line["duration_sec"] = record["duration_sec"]
		line["seed"] = record["seed"]
		line["mode"] = record["mode"]
	}
	anyDraft := false
	for _, line := range lines {
		if mapText(line, "record_id") == "" {
			anyDraft = true
			break
		}
	}
	scene["lines"] = lines
	scene["draft"] = anyDraft
	if err := writeObject(filepath.Join(a.root, "scenes", id, "scene.json"), scene); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, scene)
}

func optionalValue(value string) any {
	if value == "" {
		return nil
	}
	return value
}

func (a *synthAPI) generate(w http.ResponseWriter, r *http.Request) {
	var req ttsRequest
	if json.NewDecoder(r.Body).Decode(&req) != nil || strings.TrimSpace(req.Text) == "" {
		apiError(w, 400, "合成文本不能为空")
		return
	}
	if req.Mode == "" {
		req.Mode = "auto"
	}
	validModes := map[string]bool{"auto": true, "basic": true, "design": true, "controllable_clone": true, "ultimate_clone": true}
	if !validModes[req.Mode] {
		apiError(w, 400, "不支持的合成模式: "+req.Mode)
		return
	}
	if req.CFG == 0 {
		req.CFG = 2
	}
	if req.Steps == 0 {
		req.Steps = 10
	}
	if req.CFG < 1 || req.CFG > 3 {
		apiError(w, 400, "cfg_value 应在 1.0–3.0 之间")
		return
	}
	if req.Steps < 4 || req.Steps > 30 {
		apiError(w, 400, "inference_timesteps 应在 4–30 之间")
		return
	}

	text := req.Text
	control := strings.NewReplacer("(", "", ")", "", "（", "", "）", "").Replace(strings.TrimSpace(req.Control))
	var refPath, transcript, sampleID, assetPath string
	var savedControl string
	var params map[string]any
	preferred := ""
	voiceSource := ""
	if req.VoiceID != "" {
		voice, voiceDir, err := (&libraryAPI{root: a.root}).loadVoice(req.VoiceID)
		if err != nil {
			apiError(w, 404, "音色不存在: "+req.VoiceID)
			return
		}
		voiceSource, _ = voice["voice_source"].(string)
		if voiceSource == "" {
			if _, ok := voice["lora"].(map[string]any); ok {
				voiceSource = "lora"
			} else if _, ok := voice["generation_params"].(map[string]any); ok {
				voiceSource = "design"
			} else {
				voiceSource = "reference_samples"
			}
		}
		if voiceSource == "lora" {
			apiError(w, 400, "该音色为 LoRA 微调来源，当前合成路径尚未接入；请改用参考样本或设计音色")
			return
		}
		if p, ok := voice["generation_params"].(map[string]any); ok {
			params = p
			if s, ok := p["text"].(string); ok && strings.HasPrefix(s, "(") && strings.Contains(s, ")") {
				savedControl = strings.TrimSpace(strings.SplitN(strings.TrimPrefix(s, "("), ")", 2)[0])
			}
		}
		if raw, ok := voice["samples"].([]any); ok {
			var chosen map[string]any
			for _, item := range raw {
				sample, ok := item.(map[string]any)
				if !ok {
					continue
				}
				f, _ := sample["file"].(string)
				candidate := filepath.Join(voiceDir, filepath.FromSlash(f))
				if safeUnder(voiceDir, candidate) {
					if _, err := os.Stat(candidate); err == nil {
						if chosen == nil {
							chosen = sample
						}
						if req.Emotion != "" && sample["emotion"] == req.Emotion {
							chosen = sample
							break
						}
					}
				}
			}
			if chosen != nil {
				file, _ := chosen["file"].(string)
				refPath = filepath.Join(voiceDir, filepath.FromSlash(file))
				transcript, _ = chosen["transcript"].(string)
				sampleID, _ = chosen["sample_id"].(string)
				assetPath = filepath.ToSlash(filepath.Join("voices", req.VoiceID, file))
				preferred, _ = chosen["clone_mode"].(string)
				if preferred == "" {
					preferred, _ = voice["default_clone_mode"].(string)
				}
			}
		}
		if refPath == "" && params != nil {
			stored, _ := params["reference_asset"].(string)
			if stored == "" {
				stored, _ = params["reference_wav_path"].(string)
			}
			refPath = a.resolveStoredAsset(stored)
			if refPath != "" {
				assetPath = filepath.ToSlash(strings.TrimPrefix(strings.TrimPrefix(refPath, a.root+string(filepath.Separator)), "assets"+string(filepath.Separator)))
			}
			transcript, _ = params["prompt_text"].(string)
			preferred, _ = params["tts_mode"].(string)
		}
	}
	if req.Seed == nil && params != nil {
		if n, ok := params["seed"].(float64); ok {
			seed := int64(n)
			req.Seed = &seed
		}
	}
	if params != nil {
		if n, ok := params["cfg_value"].(float64); ok {
			req.CFG = n
		}
		if n, ok := params["inference_timesteps"].(float64); ok {
			req.Steps = int(n)
		}
	}
	if req.PromptText != "" {
		transcript = req.PromptText
	}
	mode := req.Mode
	if mode == "auto" {
		if refPath != "" {
			mode = "controllable_clone"
			if preferred == "ultimate_clone" && transcript != "" {
				mode = "ultimate_clone"
			}
		} else if control != "" || savedControl != "" || strings.HasPrefix(strings.TrimSpace(text), "(") || strings.HasPrefix(strings.TrimSpace(text), "（") {
			mode = "design"
		} else {
			mode = "basic"
		}
	}
	if (mode == "controllable_clone" || mode == "ultimate_clone") && refPath == "" {
		apiError(w, 400, "克隆模式需要绑定一个包含参考音频的音色")
		return
	}
	if mode == "ultimate_clone" && transcript == "" {
		apiError(w, 400, "极致克隆需要参考音频的精确转录文本")
		return
	}
	effectiveControl := control
	if effectiveControl == "" {
		effectiveControl = savedControl
	}
	if effectiveControl != "" && (mode == "controllable_clone" || mode == "design") {
		text = "(" + effectiveControl + ")" + text
	}
	workerReq := map[string]any{"text": text, "reference_wav_path": nil, "prompt_wav_path": nil, "prompt_text": nil, "seed": req.Seed, "cfg_value": req.CFG, "inference_timesteps": req.Steps, "normalize": req.Normalize, "denoise": req.Denoise}
	if mode == "controllable_clone" {
		workerReq["reference_wav_path"] = refPath
	}
	if mode == "ultimate_clone" {
		workerReq["reference_wav_path"] = refPath
		workerReq["prompt_wav_path"] = refPath
		workerReq["prompt_text"] = transcript
	}
	workerURL := *a.worker
	workerURL.Path = strings.TrimSuffix(workerURL.Path, "/") + "/internal/inference/voxcpm2"
	body, _ := json.Marshal(workerReq)
	call, err := http.NewRequestWithContext(r.Context(), http.MethodPost, workerURL.String(), bytes.NewReader(body))
	if err != nil {
		apiError(w, 500, err.Error())
		return
	}
	call.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 30 * time.Minute}
	response, err := client.Do(call)
	if err != nil {
		apiError(w, 502, "模型推理服务不可用: "+err.Error())
		return
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		detail, _ := io.ReadAll(io.LimitReader(response.Body, 8192))
		apiError(w, 502, "模型推理失败: "+strings.TrimSpace(string(detail)))
		return
	}
	var result struct {
		Audio      string `json:"audio_base64"`
		SampleRate int    `json:"sample_rate"`
		UsedSeed   int64  `json:"used_seed"`
		Model      any    `json:"model"`
		Frames     int    `json:"frames"`
	}
	if json.NewDecoder(response.Body).Decode(&result) != nil || result.Audio == "" || result.SampleRate <= 0 {
		apiError(w, 502, "模型推理返回数据无效")
		return
	}
	audio, err := base64.StdEncoding.DecodeString(result.Audio)
	if err != nil {
		apiError(w, 502, "模型音频解码失败")
		return
	}
	rid := time.Now().Format("20060102_150405") + "_" + randomID("")[:6]
	dir := filepath.Join(a.root, "generations", rid)
	if err := os.MkdirAll(dir, 0755); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	if err := os.WriteFile(filepath.Join(dir, "audio.wav"), audio, 0644); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	actualMode := mode
	if mode == "ultimate_clone" {
		actualMode = "ultimate_clone"
	} else if mode == "controllable_clone" {
		actualMode = "controllable_clone"
	} else if strings.HasPrefix(strings.TrimSpace(text), "(") || strings.HasPrefix(strings.TrimSpace(text), "（") {
		actualMode = "design"
	} else {
		actualMode = "basic"
	}
	meta := map[string]any{"text": req.Text, "control_instruction": nil, "mode": actualMode, "voice_id": nil, "emotion": nil, "seed": result.UsedSeed, "cfg_value": req.CFG, "inference_timesteps": req.Steps, "normalize": req.Normalize, "denoise": req.Denoise, "source_sample_id": nil, "reference_asset": nil, "prompt_text": nil, "model": result.Model}
	if effectiveControl != "" && (actualMode == "design" || actualMode == "controllable_clone") {
		meta["control_instruction"] = effectiveControl
	}
	if req.VoiceID != "" {
		meta["voice_id"] = req.VoiceID
	}
	if req.Emotion != "" {
		meta["emotion"] = req.Emotion
	}
	if sampleID != "" {
		meta["source_sample_id"] = sampleID
	}
	if assetPath != "" {
		meta["reference_asset"] = assetPath
	}
	if actualMode == "ultimate_clone" {
		meta["prompt_text"] = transcript
	}
	frames := result.Frames
	if frames <= 0 {
		frames = result.SampleRate * 2
	}
	for k, v := range meta {
		if v == nil {
			delete(meta, k)
		}
	}
	meta["record_id"] = rid
	meta["created_at"] = time.Now().Format("2006-01-02 15:04:05")
	meta["sample_rate"] = result.SampleRate
	meta["duration_sec"] = float64(frames) / float64(result.SampleRate)
	meta["review_status"] = "pending"
	if err := writeObject(filepath.Join(dir, "record.json"), meta); err != nil {
		apiError(w, 500, fmt.Sprintf("保存生成记录失败: %v", err))
		return
	}
	writeJSON(w, 200, meta)
}

func (a *synthAPI) resolveStoredAsset(value string) string {
	if value == "" {
		return ""
	}
	candidate := value
	if !filepath.IsAbs(candidate) {
		normalized := strings.ReplaceAll(value, "\\", "/")
		if i := strings.Index(normalized, "assets/"); i >= 0 {
			normalized = normalized[i+len("assets/"):]
		}
		candidate = filepath.Join(a.root, "assets", filepath.FromSlash(normalized))
	}
	if !safeUnder(filepath.Join(a.root, "assets"), candidate) {
		return ""
	}
	if info, err := os.Stat(candidate); err == nil && info.Mode().IsRegular() {
		return candidate
	}
	return ""
}
