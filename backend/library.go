package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

type libraryAPI struct {
	root string
	mu   sync.Mutex
}

var voiceIDPattern = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,100}$`)
var reviewStatuses = map[string]bool{"unreviewed": true, "needs_review": true, "approved": true}

func registerLibraryRoutes(mux *http.ServeMux, root string) {
	api := &libraryAPI{root: root}
	mux.HandleFunc("GET /api/voices", api.listVoices)
	mux.HandleFunc("GET /api/voices/{voiceID}", api.voiceDetail)
	mux.HandleFunc("GET /api/voices/{voiceID}/audio/{filename}", api.voiceAudio)
	mux.HandleFunc("POST /api/voices/{voiceID}/review", api.saveReview)
	mux.HandleFunc("POST /api/voices/import", api.importVoice)
	mux.HandleFunc("GET /api/library/summary", api.summary)
	mux.HandleFunc("POST /api/library/refresh", api.refresh)
	mux.HandleFunc("GET /api/assets/{kind}", api.listAssets)
	mux.HandleFunc("GET /api/assets/{kind}/audio/{assetPath...}", api.assetAudio)
	mux.HandleFunc("POST /api/assets/{kind}/upload", api.uploadAsset)
}

func (a *libraryAPI) voicesRoot() string { return filepath.Join(a.root, "assets", "voices") }

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func apiError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"detail": message})
}

func (a *libraryAPI) loadVoice(id string) (map[string]any, string, error) {
	if !voiceIDPattern.MatchString(id) {
		return nil, "", os.ErrNotExist
	}
	voiceDir := filepath.Join(a.voicesRoot(), id)
	metaPath := filepath.Join(voiceDir, "voice.json")
	data, err := os.ReadFile(metaPath)
	if err != nil {
		return nil, "", err
	}
	var voice map[string]any
	if err := json.Unmarshal(data, &voice); err != nil {
		return nil, "", err
	}
	return voice, voiceDir, nil
}

func (a *libraryAPI) listVoices(w http.ResponseWriter, r *http.Request) {
	entries, err := os.ReadDir(a.voicesRoot())
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		apiError(w, 500, err.Error())
		return
	}
	query := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("q")))
	gender, emotion := r.URL.Query().Get("gender"), r.URL.Query().Get("emotion")
	items := make([]map[string]any, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		voice, voiceDir, err := a.loadVoice(entry.Name())
		if err != nil {
			continue
		}
		voiceID, _ := voice["voice_id"].(string)
		if voiceID == "" {
			voiceID = entry.Name()
			voice["voice_id"] = voiceID
		}
		if gender != "" && voice["gender"] != gender {
			continue
		}
		if query != "" {
			encoded, _ := json.Marshal(voice)
			if !strings.Contains(strings.ToLower(string(encoded)), query) {
				continue
			}
		}
		if _, ok := voice["gender"]; !ok {
			voice["gender"] = "unknown"
		}
		if _, ok := voice["age_group"]; !ok {
			voice["age_group"] = "未标注"
		}
		if _, ok := voice["review_status"]; !ok {
			voice["review_status"] = "unreviewed"
		}
		samples, _ := voice["samples"].([]any)
		available := make([]any, 0, len(samples))
		missing := make([]string, 0)
		for _, raw := range samples {
			sample, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			file, _ := sample["file"].(string)
			candidate := filepath.Join(voiceDir, filepath.FromSlash(file))
			if !safeUnder(voiceDir, candidate) {
				missing = append(missing, file)
				continue
			}
			if info, err := os.Stat(candidate); err == nil && info.Mode().IsRegular() {
				copySample := make(map[string]any, len(sample)+2)
				for key, value := range sample {
					copySample[key] = value
				}
				copySample["url"] = "/api/voices/" + url.PathEscape(voiceID) + "/audio/" + url.PathEscape(filepath.Base(candidate))
				copySample["size_bytes"] = info.Size()
				available = append(available, copySample)
			} else {
				missing = append(missing, file)
			}
		}
		if emotion != "" {
			filtered := make([]any, 0)
			for _, raw := range available {
				if sample, ok := raw.(map[string]any); ok && sample["emotion"] == emotion {
					filtered = append(filtered, sample)
				}
			}
			available = filtered
			if len(filtered) == 0 {
				continue
			}
		}
		voice["samples"], voice["missing_samples"] = available, missing
		items = append(items, voice)
	}
	sort.Slice(items, func(i, j int) bool { return fmt.Sprint(items[i]["voice_id"]) < fmt.Sprint(items[j]["voice_id"]) })
	writeJSON(w, 200, map[string]any{"items": items})
}

func (a *libraryAPI) voiceDetail(w http.ResponseWriter, r *http.Request) {
	voice, _, err := a.loadVoice(r.PathValue("voiceID"))
	if err != nil {
		apiError(w, http.StatusNotFound, "音色不存在")
		return
	}
	writeJSON(w, 200, voice)
}

func (a *libraryAPI) voiceAudio(w http.ResponseWriter, r *http.Request) {
	id, filename := r.PathValue("voiceID"), r.PathValue("filename")
	voiceDir := filepath.Join(a.voicesRoot(), id)
	path := filepath.Join(voiceDir, "samples", filepath.Base(filename))
	if !voiceIDPattern.MatchString(id) || !safeUnder(filepath.Join(voiceDir, "samples"), path) {
		http.NotFound(w, r)
		return
	}
	voice, _, err := a.loadVoice(id)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	listed := false
	if samples, ok := voice["samples"].([]any); ok {
		for _, raw := range samples {
			if sample, ok := raw.(map[string]any); ok && filepath.Base(fmt.Sprint(sample["file"])) == filepath.Base(filename) {
				listed = true
				break
			}
		}
	}
	if !listed {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, path)
}

func (a *libraryAPI) saveReview(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Status string `json:"status"`
		Notes  string `json:"notes"`
	}
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		apiError(w, 400, "审核信息格式无效")
		return
	}
	if !reviewStatuses[request.Status] {
		apiError(w, 400, "审核状态无效")
		return
	}
	request.Notes = strings.TrimSpace(request.Notes)
	if request.Status == "needs_review" && request.Notes == "" {
		apiError(w, 400, "待复核时请填写问题说明")
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	voice, _, err := a.loadVoice(r.PathValue("voiceID"))
	if err != nil {
		apiError(w, 404, "音色不存在")
		return
	}
	voice["review_status"], voice["review_notes"] = request.Status, request.Notes
	path := filepath.Join(a.voicesRoot(), r.PathValue("voiceID"), "voice.json")
	data, err := json.MarshalIndent(voice, "", "  ")
	if err != nil {
		apiError(w, 500, "无法编码音色资料")
		return
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".voice-*.json")
	if err != nil {
		apiError(w, 500, err.Error())
		return
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err = tmp.Write(append(data, '\n')); err == nil {
		err = tmp.Sync()
	}
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(tmpName, path)
	}
	if err != nil {
		apiError(w, 500, "保存审核失败: "+err.Error())
		return
	}
	writeJSON(w, 200, voice)
}

func (a *libraryAPI) importVoice(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 51<<20)
	if err := r.ParseMultipartForm(51 << 20); err != nil {
		apiError(w, 400, "上传内容无效或超过50MB")
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		apiError(w, 400, "请选择 WAV 文件")
		return
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, (50<<20)+1))
	if err != nil || len(data) > 50<<20 {
		apiError(w, 400, "参考音频超过50MB")
		return
	}
	if !strings.EqualFold(filepath.Ext(header.Filename), ".wav") || len(data) < 44 || string(data[:4]) != "RIFF" || string(data[8:12]) != "WAVE" {
		apiError(w, 400, "文件不是有效的 WAV 音频")
		return
	}
	name := strings.TrimSpace(r.FormValue("name"))
	transcript := strings.TrimSpace(r.FormValue("transcript"))
	gender := r.FormValue("gender")
	if gender == "" {
		gender = "unknown"
	}
	emotion := cleanUploadPart(r.FormValue("emotion"))
	if emotion == "" {
		emotion = "平静"
	}
	description := strings.TrimSpace(r.FormValue("description"))
	language := strings.TrimSpace(r.FormValue("language"))
	if language == "" {
		language = "zh"
	}
	license := strings.TrimSpace(r.FormValue("license_name"))
	if license == "" {
		license = "authorized"
	}
	consent := r.FormValue("consent_confirmed") == "true" || r.FormValue("consent_confirmed") == "1"
	if name == "" {
		apiError(w, 400, "音色名称不能为空")
		return
	}
	if transcript == "" {
		apiError(w, 400, "极致克隆需要参考音频的精确转录文本")
		return
	}
	if !consent {
		apiError(w, 400, "必须确认已获得声音使用授权")
		return
	}
	if gender != "male" && gender != "female" && gender != "unknown" {
		apiError(w, 400, "gender 仅支持 male / female / unknown")
		return
	}
	voiceID, sampleID := randomID("v_user_"), randomID("s_")
	safeName := cleanUploadPart(header.Filename)
	sampleName := "01_" + emotion + ".wav"
	dir := filepath.Join(a.voicesRoot(), voiceID, "samples")
	if err := os.MkdirAll(dir, 0755); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	if err := os.WriteFile(filepath.Join(dir, sampleName), data, 0644); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	digest := sha256.Sum256(data)
	voice := map[string]any{"voice_id": voiceID, "name": name, "mode": "reference_samples", "voice_source": "reference_samples", "default_clone_mode": "ultimate_clone", "gender": gender, "description": description, "language": language, "emotions": []string{emotion}, "samples": []any{map[string]any{"sample_id": sampleID, "file": "samples/" + sampleName, "emotion": emotion, "transcript": transcript, "clone_mode": "ultimate_clone", "sha256": hex.EncodeToString(digest[:]), "source_file": safeName}}, "bound_characters": []any{}, "license": license, "consent_confirmed": true, "version": "v1.0", "created_at": time.Now().Format("2006-01-02")}
	if err := writeObject(filepath.Join(a.voicesRoot(), voiceID, "voice.json"), voice); err != nil {
		_ = os.RemoveAll(filepath.Join(a.voicesRoot(), voiceID))
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, voice)
}

func (a *libraryAPI) summary(w http.ResponseWriter, _ *http.Request) {
	voices := 0
	entries, _ := os.ReadDir(a.voicesRoot())
	for _, entry := range entries {
		if entry.IsDir() {
			if _, err := os.Stat(filepath.Join(a.voicesRoot(), entry.Name(), "voice.json")); err == nil {
				voices++
			}
		}
	}
	sfx, amb := countMedia(filepath.Join(a.root, "assets", "sfx")), countMedia(filepath.Join(a.root, "assets", "ambience"))
	writeJSON(w, 200, map[string]any{"voices": voices, "voice_samples": countMedia(a.voicesRoot()), "sfx": sfx, "ambience": amb,
		"emotions": []string{"开心", "悲伤", "愤怒", "惊讶", "平静", "紧张", "温柔", "严肃", "调皮", "疲惫"}})
}

func (a *libraryAPI) refresh(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (a *libraryAPI) listAssets(w http.ResponseWriter, r *http.Request) {
	kind := r.PathValue("kind")
	if kind != "sfx" && kind != "ambience" {
		apiError(w, 404, "素材类型仅支持 sfx / ambience")
		return
	}
	root := filepath.Join(a.root, "assets", kind)
	items := make([]map[string]any, 0)
	_ = filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return nil
		}
		ext := strings.ToLower(filepath.Ext(path))
		if ext != ".wav" && ext != ".mp3" {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return nil
		}
		category := filepath.ToSlash(filepath.Dir(rel))
		if category == "." {
			category = "未分类"
		}
		items = append(items, map[string]any{"id": strings.TrimSuffix(filepath.ToSlash(rel), ext), "name": strings.TrimSuffix(entry.Name(), ext), "category": category, "path": filepath.ToSlash(rel), "format": strings.TrimPrefix(ext, "."), "url": "/api/assets/" + kind + "/audio/" + url.PathEscape(filepath.ToSlash(rel)), "size_bytes": info.Size()})
		return nil
	})
	sort.Slice(items, func(i, j int) bool { return fmt.Sprint(items[i]["path"]) < fmt.Sprint(items[j]["path"]) })
	writeJSON(w, 200, map[string]any{"items": items})
}

func (a *libraryAPI) assetAudio(w http.ResponseWriter, r *http.Request) {
	kind := r.PathValue("kind")
	if kind != "sfx" && kind != "ambience" {
		http.NotFound(w, r)
		return
	}
	root := filepath.Join(a.root, "assets", kind)
	path := filepath.Join(root, filepath.FromSlash(r.PathValue("assetPath")))
	if !safeUnder(root, path) {
		http.NotFound(w, r)
		return
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, path)
}

func (a *libraryAPI) uploadAsset(w http.ResponseWriter, r *http.Request) {
	kind := r.PathValue("kind")
	if kind != "sfx" && kind != "ambience" {
		apiError(w, 404, "素材类型仅支持 sfx / ambience")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 101<<20)
	if err := r.ParseMultipartForm(101 << 20); err != nil {
		apiError(w, 400, "上传内容无效或超过100MB")
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		apiError(w, 400, "请选择 WAV 文件")
		return
	}
	defer file.Close()
	if strings.ToLower(filepath.Ext(header.Filename)) != ".wav" {
		apiError(w, 400, "仅支持 .wav 文件")
		return
	}
	name := cleanUploadPart(filepath.Base(header.Filename))
	if name == "" {
		apiError(w, 400, "文件名无效")
		return
	}
	category := cleanUploadPart(r.URL.Query().Get("category"))
	if category == "" {
		category = "未分类"
	}
	base := filepath.Join(a.root, "assets", kind)
	dir := filepath.Join(base, category)
	if !safeUnder(base, filepath.Join(dir, "placeholder")) {
		apiError(w, 400, "分类路径无效")
		return
	}
	if err := os.MkdirAll(dir, 0755); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	dest := filepath.Join(dir, name)
	if _, err := os.Stat(dest); err == nil {
		apiError(w, 409, "同名素材已存在: "+category+"/"+name)
		return
	}
	out, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		apiError(w, 500, err.Error())
		return
	}
	written, copyErr := io.Copy(out, io.LimitReader(file, (100<<20)+1))
	closeErr := out.Close()
	if copyErr == nil {
		copyErr = closeErr
	}
	if copyErr != nil || written > 100<<20 {
		_ = os.Remove(dest)
		apiError(w, 400, "上传失败或文件超过100MB")
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true, "path": filepath.ToSlash(filepath.Join(kind, category, name)), "size_bytes": written})
}

func cleanUploadPart(value string) string {
	var b strings.Builder
	for _, r := range value {
		if !strings.ContainsRune(`\\/:*?"<>|`, r) && r != 0 {
			b.WriteRune(r)
		}
	}
	return strings.TrimSpace(b.String())
}

func countMedia(root string) int {
	count := 0
	_ = filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err == nil && !entry.IsDir() {
			ext := strings.ToLower(filepath.Ext(path))
			if ext == ".wav" || ext == ".mp3" {
				count++
			}
		}
		return nil
	})
	return count
}

func safeUnder(root, candidate string) bool {
	base, err := filepath.Abs(root)
	if err != nil {
		return false
	}
	target, err := filepath.Abs(candidate)
	if err != nil {
		return false
	}
	if resolvedBase, resolveErr := filepath.EvalSymlinks(base); resolveErr == nil {
		base = resolvedBase
	}
	if resolvedTarget, resolveErr := filepath.EvalSymlinks(target); resolveErr == nil {
		target = resolvedTarget
	}
	rel, err := filepath.Rel(base, target)
	return err == nil && rel != "." && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}
