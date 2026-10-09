package main

import (
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const mixSampleRate = 48000

func registerAudioRoutes(mux *http.ServeMux, root string) {
	a := &audioAPI{root: root}
	mux.HandleFunc("POST /api/dialogue/scenes/{sceneID}/mix", a.renderMix)
	mux.HandleFunc("GET /api/dialogue/scenes/{sceneID}/mix/audio/{track}", a.mixAudio)
	mux.HandleFunc("POST /api/episodes/{episodeID}/export", a.exportEpisode)
	mux.HandleFunc("GET /api/episodes/{episodeID}/export/audio", a.exportAudio)
	mux.HandleFunc("POST /api/script-dub/merge", a.mergeScriptScene)
	mux.HandleFunc("GET /api/script-dub/{jobID}/audio", a.scriptDubAudio)
}

func (a *audioAPI) mergeScriptScene(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 2<<20)
	if err := r.ParseMultipartForm(2 << 20); err != nil {
		apiError(w, 400, "合并参数无效")
		return
	}
	id := r.FormValue("scene_id")
	gapMS := 500
	if value := r.FormValue("gap_ms"); value != "" {
		if _, err := fmt.Sscanf(value, "%d", &gapMS); err != nil {
			apiError(w, 400, "台词间隔无效")
			return
		}
	}
	if gapMS < 0 || gapMS > 10000 {
		apiError(w, 400, "台词间隔必须在0–10000毫秒之间")
		return
	}
	scene, err := a.loadScene(id)
	if err != nil {
		apiError(w, 400, "场次不存在")
		return
	}
	parts := make([][]float64, 0)
	lines := listMap(scene["lines"])
	rec := &recordAPI{root: a.root}
	gap := silence(mixSampleRate * gapMS / 1000)
	for i, line := range lines {
		rid := mapText(line, "record_id")
		_, dir, e := rec.record(rid)
		if rid == "" || e != nil {
			apiError(w, 400, fmt.Sprintf("第 %d 行音频缺失: %s", i+1, rid))
			return
		}
		wav, e := readWav(filepath.Join(dir, "audio.wav"))
		if e != nil {
			apiError(w, 400, e.Error())
			return
		}
		if i > 0 {
			parts = append(parts, gap)
		}
		parts = append(parts, wav.samples)
	}
	merged := concat(parts...)
	idBytes := make([]byte, 16)
	if _, err := rand.Read(idBytes); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	job := hex.EncodeToString(idBytes)
	outPath := filepath.Join(a.root, "exports", "script_dubs", job, "dialogue.wav")
	if err := writeWav(outPath, merged, mixSampleRate); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, map[string]any{"scene_id": id, "scene_name": scene["name"], "line_count": len(lines), "merge_mode": "script_order_strict", "gap_ms": gapMS, "duration_sec": math.Round(float64(len(merged))/mixSampleRate*100) / 100, "job_id": job, "audio_url": "/api/script-dub/" + job + "/audio"})
}

func (a *audioAPI) scriptDubAudio(w http.ResponseWriter, r *http.Request) {
	job := r.PathValue("jobID")
	if len(job) != 32 {
		http.NotFound(w, r)
		return
	}
	if _, err := hex.DecodeString(job); err != nil {
		http.NotFound(w, r)
		return
	}
	path := filepath.Join(a.root, "exports", "script_dubs", job, "dialogue.wav")
	if !safeUnder(filepath.Join(a.root, "exports", "script_dubs"), path) {
		http.NotFound(w, r)
		return
	}
	if _, err := os.Stat(path); err != nil {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "audio/wav")
	http.ServeFile(w, r, path)
}

type audioAPI struct{ root string }

type pcmWav struct {
	rate    int
	samples []float64
}

func readWav(path string) (pcmWav, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return pcmWav{}, err
	}
	if len(data) < 12 || string(data[:4]) != "RIFF" || string(data[8:12]) != "WAVE" {
		return pcmWav{}, fmt.Errorf("不是有效 WAV 文件: %s", filepath.Base(path))
	}
	format, channels, rate, bits := 0, 0, 0, 0
	var raw []byte
	for off := 12; off+8 <= len(data); {
		size := int(binary.LittleEndian.Uint32(data[off+4 : off+8]))
		start := off + 8
		end := start + size
		if end > len(data) {
			return pcmWav{}, fmt.Errorf("WAV chunk 长度无效")
		}
		switch string(data[off : off+4]) {
		case "fmt ":
			if size < 16 {
				return pcmWav{}, fmt.Errorf("WAV 格式头无效")
			}
			format = int(binary.LittleEndian.Uint16(data[start : start+2]))
			channels = int(binary.LittleEndian.Uint16(data[start+2 : start+4]))
			rate = int(binary.LittleEndian.Uint32(data[start+4 : start+8]))
			bits = int(binary.LittleEndian.Uint16(data[start+14 : start+16]))
		case "data":
			raw = data[start:end]
		}
		off = end + (size % 2)
	}
	if channels < 1 || rate < 1 || len(raw) == 0 {
		return pcmWav{}, fmt.Errorf("WAV 缺少音频数据")
	}
	bytesPer := (bits + 7) / 8
	if bytesPer < 1 || len(raw)%(channels*bytesPer) != 0 {
		return pcmWav{}, fmt.Errorf("WAV 位深或声道数无效")
	}
	frames := len(raw) / (channels * bytesPer)
	mono := make([]float64, frames)
	for i := 0; i < frames; i++ {
		sum := 0.0
		for c := 0; c < channels; c++ {
			p := raw[(i*channels+c)*bytesPer:]
			v := 0.0
			switch format {
			case 1:
				switch bits {
				case 8:
					v = (float64(p[0]) - 128) / 128
				case 16:
					v = float64(int16(binary.LittleEndian.Uint16(p))) / 32768
				case 24:
					x := int32(p[0]) | int32(p[1])<<8 | int32(p[2])<<16
					if x&0x800000 != 0 {
						x |= ^0xffffff
					}
					v = float64(x) / 8388608
				case 32:
					v = float64(int32(binary.LittleEndian.Uint32(p))) / 2147483648
				default:
					return pcmWav{}, fmt.Errorf("不支持的 PCM 位深 %d", bits)
				}
			case 3:
				if bits != 32 {
					return pcmWav{}, fmt.Errorf("不支持的浮点 WAV 位深 %d", bits)
				}
				v = float64(math.Float32frombits(binary.LittleEndian.Uint32(p)))
			default:
				return pcmWav{}, fmt.Errorf("不支持的 WAV 编码 %d", format)
			}
			sum += v
		}
		mono[i] = sum / float64(channels)
	}
	if rate != mixSampleRate && len(mono) > 0 {
		n := int(float64(len(mono)) * float64(mixSampleRate) / float64(rate))
		if n < 1 {
			n = 1
		}
		resampled := make([]float64, n)
		for i := range resampled {
			x := float64(i) * float64(rate) / float64(mixSampleRate)
			lo := int(x)
			hi := lo + 1
			if hi >= len(mono) {
				hi = len(mono) - 1
			}
			frac := x - float64(lo)
			resampled[i] = mono[lo]*(1-frac) + mono[hi]*frac
		}
		mono = resampled
	}
	return pcmWav{rate: mixSampleRate, samples: mono}, nil
}

func writeWav(path string, samples []float64, rate int) error {
	if rate < 1 {
		rate = mixSampleRate
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	dataSize := len(samples) * 2
	header := make([]byte, 44)
	copy(header[:4], "RIFF")
	binary.LittleEndian.PutUint32(header[4:8], uint32(36+dataSize))
	copy(header[8:12], "WAVEfmt ")
	binary.LittleEndian.PutUint32(header[16:20], 16)
	binary.LittleEndian.PutUint16(header[20:22], 1)
	binary.LittleEndian.PutUint16(header[22:24], 1)
	binary.LittleEndian.PutUint32(header[24:28], uint32(rate))
	binary.LittleEndian.PutUint32(header[28:32], uint32(rate*2))
	binary.LittleEndian.PutUint16(header[32:34], 2)
	binary.LittleEndian.PutUint16(header[34:36], 16)
	copy(header[36:40], "data")
	binary.LittleEndian.PutUint32(header[40:44], uint32(dataSize))
	if _, err = f.Write(header); err != nil {
		return err
	}
	buf := make([]byte, 2*min(len(samples), 32768))
	for start := 0; start < len(samples); {
		count := min(len(samples)-start, len(buf)/2)
		for i := 0; i < count; i++ {
			v := samples[start+i]
			if v > 1 {
				v = 1
			}
			if v < -1 {
				v = -1
			}
			binary.LittleEndian.PutUint16(buf[i*2:i*2+2], uint16(int16(math.Round(v*32767))))
		}
		if _, err = f.Write(buf[:count*2]); err != nil {
			return err
		}
		start += count
	}
	return f.Close()
}

func silence(n int) []float64 {
	if n < 0 {
		n = 0
	}
	return make([]float64, n)
}
func concat(parts ...[]float64) []float64 {
	n := 0
	for _, p := range parts {
		n += len(p)
	}
	out := make([]float64, 0, n)
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}
func normalizePeak(samples []float64) {
	peak := 0.0
	for _, v := range samples {
		if math.Abs(v) > peak {
			peak = math.Abs(v)
		}
	}
	if peak > 0.99 {
		scale := 0.99 / peak
		for i := range samples {
			samples[i] *= scale
		}
	}
}
func scale(samples []float64, volume float64) []float64 {
	out := make([]float64, len(samples))
	for i, v := range samples {
		out[i] = v * volume
	}
	return out
}
func (a *audioAPI) scenePath(id string) string {
	return filepath.Join(a.root, "scenes", id, "scene.json")
}
func (a *audioAPI) loadScene(id string) (map[string]any, error) {
	if !workflowIDPattern.MatchString(id) {
		return nil, os.ErrNotExist
	}
	return readObject(a.scenePath(id))
}
func listMap(value any) []map[string]any {
	raw, ok := value.([]any)
	if !ok {
		return nil
	}
	out := make([]map[string]any, 0, len(raw))
	for _, v := range raw {
		if m, ok := v.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}
func mapText(m map[string]any, key string) string { s, _ := m[key].(string); return s }
func mapNumber(m map[string]any, key string, def float64) float64 {
	if n, ok := m[key].(float64); ok {
		return n
	}
	return def
}
func (a *audioAPI) assetPath(kind, id string) (string, error) {
	base := filepath.Join(a.root, "assets", kind)
	items := make([]map[string]any, 0)
	_ = filepath.WalkDir(base, func(path string, d os.DirEntry, e error) error {
		if e != nil || d.IsDir() {
			return nil
		}
		ext := strings.ToLower(filepath.Ext(path))
		if ext != ".wav" && ext != ".mp3" {
			return nil
		}
		rel, _ := filepath.Rel(base, path)
		key := strings.TrimSuffix(filepath.ToSlash(rel), ext)
		if key == id {
			items = append(items, map[string]any{"path": path})
		}
		return nil
	})
	if len(items) == 0 {
		return "", fmt.Errorf("素材不存在: %s", id)
	}
	return mapText(items[0], "path"), nil
}

func (a *audioAPI) render(sceneID string, cfg map[string]any) (map[string]any, error) {
	scene, err := a.loadScene(sceneID)
	if err != nil {
		return nil, fmt.Errorf("场次不存在: %s", sceneID)
	}
	gapMS := int(mapNumber(cfg, "gap_ms", 500))
	if gapMS < 0 || gapMS > 60000 {
		return nil, fmt.Errorf("行间隔必须在0–60000毫秒")
	}
	dialogueVol := mapNumber(cfg, "dialogue_volume", 1)
	if dialogueVol < 0 || dialogueVol > 4 {
		return nil, fmt.Errorf("对白音量必须在0–4之间")
	}
	lines := listMap(scene["lines"])
	parts := make([][]float64, 0, len(lines)*2)
	offsets := make([]int, 0, len(lines))
	cursor := 0
	gap := silence(mixSampleRate * gapMS / 1000)
	rec := &recordAPI{root: a.root}
	for i, line := range lines {
		rid := mapText(line, "record_id")
		if rid == "" {
			return nil, fmt.Errorf("第 %d 行音频缺失", i+1)
		}
		_, dir, e := rec.record(rid)
		if e != nil {
			return nil, fmt.Errorf("第 %d 行音频缺失: %s", i+1, rid)
		}
		wav, e := readWav(filepath.Join(dir, "audio.wav"))
		if e != nil {
			return nil, e
		}
		if i > 0 {
			parts = append(parts, gap)
			cursor += len(gap)
		}
		offsets = append(offsets, cursor)
		scaled := scale(wav.samples, dialogueVol)
		parts = append(parts, scaled)
		cursor += len(scaled)
	}
	dialogueTrack := concat(parts...)
	if len(dialogueTrack) == 0 {
		dialogueTrack = silence(mixSampleRate)
	}
	mix := append([]float64(nil), dialogueTrack...)
	ambUsed := any(nil)
	if amb, ok := cfg["ambience"].(map[string]any); ok && mapText(amb, "id") != "" {
		id := mapText(amb, "id")
		path, e := a.assetPath("ambience", id)
		if e != nil {
			return nil, e
		}
		wav, e := readWav(path)
		if e != nil {
			return nil, e
		}
		vol := mapNumber(amb, "volume", 0.4)
		loop := make([]float64, len(mix))
		for i := range loop {
			loop[i] = wav.samples[i%len(wav.samples)]
		}
		fade := min(len(loop)/2, mixSampleRate*800/1000)
		for i := 0; i < fade; i++ {
			loop[i] *= float64(i) / float64(fade)
			loop[len(loop)-1-i] *= float64(i) / float64(fade)
		}
		for i := range mix {
			mix[i] += loop[i] * vol
		}
		ambUsed = map[string]any{"id": id, "volume": vol}
	}
	sfxUsed := make([]any, 0)
	for _, item := range listMap(cfg["sfx"]) {
		id := mapText(item, "id")
		if id == "" {
			continue
		}
		path, e := a.assetPath("sfx", id)
		if e != nil {
			return nil, e
		}
		wav, e := readWav(path)
		if e != nil {
			return nil, e
		}
		vol := mapNumber(item, "volume", 0.8)
		at := int(mapNumber(item, "at_line", 0))
		if at < 0 {
			at = 0
		}
		if at >= len(offsets) {
			at = len(offsets) - 1
		}
		start := 0
		if at >= 0 {
			start = offsets[at]
		}
		for i, v := range wav.samples {
			if start+i >= len(mix) {
				break
			}
			mix[start+i] += v * vol
		}
		sfxUsed = append(sfxUsed, map[string]any{"id": id, "at_line": at, "volume": vol})
	}
	normalizePeak(mix)
	sceneDir := filepath.Join(a.root, "scenes", sceneID)
	if err := writeWav(filepath.Join(sceneDir, "mix.wav"), mix, mixSampleRate); err != nil {
		return nil, err
	}
	if err := writeWav(filepath.Join(sceneDir, "dialogue.wav"), dialogueTrack, mixSampleRate); err != nil {
		return nil, err
	}
	result := map[string]any{"config": map[string]any{"gap_ms": gapMS, "dialogue_volume": dialogueVol, "ambience": ambUsed, "sfx": sfxUsed}, "duration_sec": math.Round(float64(len(dialogueTrack))/mixSampleRate*100) / 100, "rendered_at": time.Now().Format("2006-01-02 15:04:05")}
	scene["mix"] = result
	if err := writeObject(a.scenePath(sceneID), scene); err != nil {
		return nil, err
	}
	return result, nil
}

func (a *audioAPI) renderMix(w http.ResponseWriter, r *http.Request) {
	var cfg map[string]any
	if json.NewDecoder(r.Body).Decode(&cfg) != nil {
		apiError(w, 400, "混音配置无效")
		return
	}
	result, err := a.render(r.PathValue("sceneID"), cfg)
	if err != nil {
		apiError(w, 400, err.Error())
		return
	}
	writeJSON(w, 200, result)
}
func (a *audioAPI) mixAudio(w http.ResponseWriter, r *http.Request) {
	track := r.PathValue("track")
	if track != "mix" && track != "dialogue" {
		http.NotFound(w, r)
		return
	}
	id := r.PathValue("sceneID")
	if _, err := a.loadScene(id); err != nil {
		http.NotFound(w, r)
		return
	}
	path := filepath.Join(a.root, "scenes", id, track+".wav")
	if !safeUnder(filepath.Join(a.root, "scenes"), path) {
		http.NotFound(w, r)
		return
	}
	if _, err := os.Stat(path); err != nil {
		apiError(w, 404, "混音尚未渲染，请先调用 mix 接口")
		return
	}
	w.Header().Set("Content-Type", "audio/wav")
	http.ServeFile(w, r, path)
}

func (a *audioAPI) exportEpisode(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Gap  int   `json:"scene_gap_ms"`
		Auto *bool `json:"auto_render"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil { req.Auto = nil }
	autoRender := req.Auto == nil || *req.Auto
	if req.Gap == 0 {
		req.Gap = 1000
	}
	if req.Gap < 0 || req.Gap > 60000 {
		apiError(w, 400, "场次间隔必须在0–60000毫秒")
		return
	}
	ep, epDir, err := (&workflowAPI{root: a.root}).episode(r.PathValue("episodeID"))
	if err != nil {
		apiError(w, 404, "剧集不存在")
		return
	}
	scenes := make([]map[string]any, 0)
	entries, _ := os.ReadDir(filepath.Join(a.root, "scenes"))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		s, e := a.loadScene(entry.Name())
		if e == nil && s["episode_id"] == r.PathValue("episodeID") {
			scenes = append(scenes, s)
		}
	}
	if len(scenes) == 0 {
		apiError(w, 400, "该剧集下没有场次")
		return
	}
	order := make([]string, 0)
	if raw, ok := ep["scene_order"].([]any); ok {
		for _, v := range raw {
			order = append(order, fmt.Sprint(v))
		}
	}
	orderIndex := map[string]int{}
	for i, id := range order {
		orderIndex[id] = i
	}
	sort.Slice(scenes, func(i, j int) bool {
		ai, oki := orderIndex[mapText(scenes[i], "scene_id")]
		aj, okj := orderIndex[mapText(scenes[j], "scene_id")]
		if !oki {
			ai = 9999
		}
		if !okj {
			aj = 9999
		}
		if ai != aj {
			return ai < aj
		}
		return mapText(scenes[i], "scene_id") < mapText(scenes[j], "scene_id")
	})
	parts := make([][]float64, 0, len(scenes)*2)
	manifestScenes := make([]any, 0, len(scenes))
	gap := silence(mixSampleRate * req.Gap / 1000)
	for i, s := range scenes {
		id := mapText(s, "scene_id")
		path := filepath.Join(a.root, "scenes", id, "mix.wav")
		source := "mix"
		if _, e := os.Stat(path); e != nil {
			if s["draft"] == true {
				apiError(w, 400, "场次「"+mapText(s, "name")+"」台词尚未生成，无法导出")
				return
			}
			if !autoRender {
				apiError(w, 400, "场次「"+mapText(s, "name")+"」尚未混音")
				return
			}
			if _, e = a.render(id, map[string]any{}); e != nil {
				apiError(w, 400, e.Error())
				return
			}
			source = "auto"
		}
		wav, e := readWav(path)
		if e != nil {
			apiError(w, 500, e.Error())
			return
		}
		if i > 0 {
			parts = append(parts, gap)
		}
		parts = append(parts, wav.samples)
		manifestScenes = append(manifestScenes, map[string]any{"scene_id": id, "name": s["name"], "source": source, "duration_sec": math.Round(float64(len(wav.samples))/mixSampleRate*100) / 100})
	}
	full := concat(parts...)
	normalizePeak(full)
	wavPath := filepath.Join(epDir, "export.wav")
	if err := writeWav(wavPath, full, mixSampleRate); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	manifest := map[string]any{"episode_id": r.PathValue("episodeID"), "episode_name": ep["name"], "project_id": ep["project_id"], "exported_at": time.Now().Format("2006-01-02 15:04:05"), "scene_gap_ms": req.Gap, "total_duration_sec": math.Round(float64(len(full))/mixSampleRate*100) / 100, "scenes": manifestScenes}
	if err := writeObject(filepath.Join(epDir, "export.json"), manifest); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, manifest)
}
func (a *audioAPI) exportAudio(w http.ResponseWriter, r *http.Request) {
	_, dir, err := (&workflowAPI{root: a.root}).episode(r.PathValue("episodeID"))
	if err != nil {
		apiError(w, 404, "整集尚未导出")
		return
	}
	path := filepath.Join(dir, "export.wav")
	if _, err := os.Stat(path); err != nil {
		apiError(w, 404, "整集尚未导出")
		return
	}
	w.Header().Set("Content-Type", "audio/wav")
	http.ServeFile(w, r, path)
}
