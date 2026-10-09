package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

var recordIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,100}$`)

func registerRecordRoutes(mux *http.ServeMux, root string) {
	a := &recordAPI{root: root}
	mux.HandleFunc("GET /api/tts/records", a.list)
	mux.HandleFunc("GET /api/tts/records/{recordID}/audio", a.audio)
	mux.HandleFunc("POST /api/tts/records/{recordID}/promote", a.promote)
}

type recordAPI struct{ root string }

func (a *recordAPI) recordsRoot() string { return filepath.Join(a.root, "generations") }

func (a *recordAPI) record(id string) (map[string]any, string, error) {
	if !recordIDPattern.MatchString(id) {
		return nil, "", os.ErrNotExist
	}
	dir := filepath.Join(a.recordsRoot(), id)
	value, err := readObject(filepath.Join(dir, "record.json"))
	return value, dir, err
}

func (a *recordAPI) list(w http.ResponseWriter, _ *http.Request) {
	entries, err := os.ReadDir(a.recordsRoot())
	if err != nil && !os.IsNotExist(err) {
		apiError(w, 500, err.Error())
		return
	}
	items := make([]map[string]any, 0)
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		if item, _, err := a.record(entry.Name()); err == nil {
			items = append(items, item)
		}
	}
	sort.Slice(items, func(i, j int) bool { return fmt.Sprint(items[i]["record_id"]) > fmt.Sprint(items[j]["record_id"]) })
	writeJSON(w, 200, map[string]any{"items": items})
}

func (a *recordAPI) audio(w http.ResponseWriter, r *http.Request) {
	_, dir, err := a.record(r.PathValue("recordID"))
	path := filepath.Join(dir, "audio.wav")
	if err != nil || !safeUnder(a.recordsRoot(), path) {
		http.NotFound(w, r)
		return
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "audio/wav")
	http.ServeFile(w, r, path)
}

func (a *recordAPI) promote(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Name   string `json:"name"`
		Gender string `json:"gender"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil || strings.TrimSpace(req.Name) == "" {
		apiError(w, 400, "音色名称不能为空")
		return
	}
	if req.Gender == "" {
		req.Gender = "unknown"
	}
	if req.Gender != "male" && req.Gender != "female" && req.Gender != "unknown" {
		apiError(w, 400, "性别无效")
		return
	}
	rid := r.PathValue("recordID")
	record, dir, err := a.record(rid)
	if err != nil {
		apiError(w, 404, "生成记录不存在: "+rid)
		return
	}
	source := filepath.Join(dir, "audio.wav")
	info, err := os.Stat(source)
	if err != nil || !info.Mode().IsRegular() {
		apiError(w, 400, "生成音频不存在，无法固化")
		return
	}
	voiceID := "v_gen_" + strings.ReplaceAll(strings.TrimPrefix(rid, strings.SplitN(rid, "_", 2)[0]+"_"), "_", "")
	voiceDir := filepath.Join(a.root, "assets", "voices", voiceID)
	if _, err := os.Stat(voiceDir); err == nil {
		apiError(w, 409, "该记录已固化过: "+voiceID)
		return
	}
	samplesDir := filepath.Join(voiceDir, "samples")
	if err := os.MkdirAll(samplesDir, 0755); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	dst := filepath.Join(samplesDir, "01_参考.wav")
	in, err := os.Open(source)
	if err != nil {
		_ = os.RemoveAll(voiceDir)
		apiError(w, 500, err.Error())
		return
	}
	out, err := os.Create(dst)
	if err != nil {
		in.Close()
		_ = os.RemoveAll(voiceDir)
		apiError(w, 500, err.Error())
		return
	}
	hash := sha256.New()
	_, copyErr := io.Copy(io.MultiWriter(out, hash), in)
	closeIn, closeOut := in.Close(), out.Close()
	if copyErr == nil {
		copyErr = closeIn
	}
	if copyErr == nil {
		copyErr = closeOut
	}
	if copyErr != nil {
		_ = os.RemoveAll(voiceDir)
		apiError(w, 500, copyErr.Error())
		return
	}
	text, _ := record["text"].(string)
	emotion, _ := record["emotion"].(string)
	if emotion == "" {
		emotion = "参考"
	}
	sample := map[string]any{"sample_id": randomID("s_"), "file": "samples/01_参考.wav", "emotion": emotion, "transcript": text, "clone_mode": "ultimate_clone", "sha256": hex.EncodeToString(hash.Sum(nil)), "source_record": rid}
	voice := map[string]any{"voice_id": voiceID, "name": strings.TrimSpace(req.Name), "mode": "reference_samples", "default_clone_mode": "ultimate_clone", "gender": req.Gender, "description": clip(text, 50), "language": "zh", "source_generation": map[string]any{"source_record": rid, "tts_mode": record["mode"], "seed": record["seed"], "cfg_value": record["cfg_value"], "inference_timesteps": record["inference_timesteps"]}, "samples": []any{sample}, "bound_characters": []any{}, "license": "original", "consent_confirmed": true, "version": "v1.0", "created_at": time.Now().Format("2006-01-02")}
	if model, ok := record["model"].(map[string]any); ok && strings.HasPrefix(fmt.Sprint(model["name"]), "Qwen/") {
		name := req.Name
		if !strings.Contains(name, "Qwen3") {
			name += "（Qwen3）"
		}
		voice["collection"], voice["voice_source"], voice["name"] = "音色库新千问三", "design", name
		voice["gender"], voice["age_group"], voice["timbre_tags"] = record["gender"], record["age_group"], record["timbre_tags"]
		voice["review_status"] = "unreviewed"
		voice["description"] = record["control_instruction"]
		voice["source_generation"] = record
	}
	if err := writeObject(filepath.Join(voiceDir, "voice.json"), voice); err != nil {
		_ = os.RemoveAll(voiceDir)
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, voice)
}

func clip(value string, n int) string {
	runes := []rune(value)
	if len(runes) > n {
		return string(runes[:n])
	}
	return value
}
