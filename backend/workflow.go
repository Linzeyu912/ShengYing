package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

type workflowAPI struct{ root string }

var workflowIDPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}$`)

func registerWorkflowRoutes(mux *http.ServeMux, root string) {
	a := &workflowAPI{root: root}
	mux.HandleFunc("GET /api/projects", a.listProjects)
	mux.HandleFunc("POST /api/projects", a.createProject)
	mux.HandleFunc("DELETE /api/projects/{projectID}", a.deleteProject)
	mux.HandleFunc("GET /api/projects/{projectID}/episodes", a.listEpisodes)
	mux.HandleFunc("POST /api/projects/{projectID}/episodes", a.createEpisode)
	mux.HandleFunc("DELETE /api/episodes/{episodeID}", a.deleteEpisode)
	mux.HandleFunc("POST /api/episodes/{episodeID}/scene_order", a.setSceneOrder)
	mux.HandleFunc("GET /api/characters", a.listCharacters)
	mux.HandleFunc("POST /api/characters", a.createCharacter)
	mux.HandleFunc("PUT /api/characters/{characterID}", a.updateCharacter)
	mux.HandleFunc("DELETE /api/characters/{characterID}", a.deleteCharacter)
	mux.HandleFunc("GET /api/dialogue/scenes", a.listScenes)
	mux.HandleFunc("GET /api/dialogue/scenes/{sceneID}", a.sceneDetail)
	mux.HandleFunc("POST /api/dialogue/scenes/{sceneID}/assign", a.assignScene)
}

func readObject(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var value map[string]any
	err = json.Unmarshal(data, &value)
	return value, err
}

func writeObject(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".write-*.json")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer os.Remove(name)
	if _, err = tmp.Write(append(data, '\n')); err == nil {
		err = tmp.Sync()
	}
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(name, path)
	}
	return err
}

func randomID(prefix string) string {
	var data [4]byte
	if _, err := rand.Read(data[:]); err != nil {
		return prefix + fmt.Sprintf("%08x", time.Now().UnixNano())
	}
	return prefix + hex.EncodeToString(data[:])
}

func (a *workflowAPI) projectDir(id string) string   { return filepath.Join(a.root, "projects", id) }
func (a *workflowAPI) characterDir(id string) string { return filepath.Join(a.root, "characters", id) }
func (a *workflowAPI) sceneDir(id string) string     { return filepath.Join(a.root, "scenes", id) }

func (a *workflowAPI) project(id string) (map[string]any, error) {
	if !workflowIDPattern.MatchString(id) || !strings.HasPrefix(id, "p_") {
		return nil, os.ErrNotExist
	}
	return readObject(filepath.Join(a.projectDir(id), "project.json"))
}

func (a *workflowAPI) episode(id string) (map[string]any, string, error) {
	if !workflowIDPattern.MatchString(id) || !strings.HasPrefix(id, "e_") {
		return nil, "", os.ErrNotExist
	}
	projectsRoot := filepath.Join(a.root, "projects")
	entries, err := os.ReadDir(projectsRoot)
	if err != nil {
		return nil, "", err
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		path := filepath.Join(projectsRoot, entry.Name(), "episodes", id, "episode.json")
		value, err := readObject(path)
		if err == nil {
			return value, filepath.Dir(path), nil
		}
	}
	return nil, "", os.ErrNotExist
}

func (a *workflowAPI) listProjects(w http.ResponseWriter, _ *http.Request) {
	root := filepath.Join(a.root, "projects")
	entries, _ := os.ReadDir(root)
	items := make([]map[string]any, 0)
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		p, err := readObject(filepath.Join(root, entry.Name(), "project.json"))
		if err != nil {
			continue
		}
		episodes, _ := os.ReadDir(filepath.Join(root, entry.Name(), "episodes"))
		count := 0
		for _, ep := range episodes {
			if _, err := os.Stat(filepath.Join(root, entry.Name(), "episodes", ep.Name(), "episode.json")); err == nil {
				count++
			}
		}
		p["episode_count"] = count
		items = append(items, p)
	}
	writeJSON(w, 200, map[string]any{"items": items})
}

func (a *workflowAPI) createProject(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Name        string `json:"name"`
		Description string `json:"description"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil || strings.TrimSpace(req.Name) == "" {
		apiError(w, 400, "项目名称不能为空")
		return
	}
	id := randomID("p_")
	dir := a.projectDir(id)
	if err := os.MkdirAll(filepath.Join(dir, "episodes"), 0755); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	project := map[string]any{"project_id": id, "name": strings.TrimSpace(req.Name), "description": req.Description, "created_at": time.Now().Format("2006-01-02 15:04:05")}
	if err := writeObject(filepath.Join(dir, "project.json"), project); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, project)
}

func (a *workflowAPI) deleteProject(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("projectID")
	if _, err := a.project(id); err != nil {
		apiError(w, 404, "项目不存在: "+id)
		return
	}
	dir := a.projectDir(id)
	if !safeUnder(filepath.Join(a.root, "projects"), dir) {
		apiError(w, 400, "项目路径无效")
		return
	}
	if err := os.RemoveAll(dir); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (a *workflowAPI) listEpisodes(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("projectID")
	if _, err := a.project(id); err != nil {
		apiError(w, 404, "项目不存在: "+id)
		return
	}
	root := filepath.Join(a.projectDir(id), "episodes")
	entries, _ := os.ReadDir(root)
	items := make([]map[string]any, 0)
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		value, err := readObject(filepath.Join(root, entry.Name(), "episode.json"))
		if err == nil {
			items = append(items, value)
		}
	}
	sort.Slice(items, func(i, j int) bool { return number(items[i]["number"]) < number(items[j]["number"]) })
	writeJSON(w, 200, map[string]any{"items": items})
}

func (a *workflowAPI) createEpisode(w http.ResponseWriter, r *http.Request) {
	pid := r.PathValue("projectID")
	if _, err := a.project(pid); err != nil {
		apiError(w, 404, "项目不存在: "+pid)
		return
	}
	var req struct {
		Name        string `json:"name"`
		Number      int    `json:"number"`
		Description string `json:"description"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil || strings.TrimSpace(req.Name) == "" {
		apiError(w, 400, "剧集名称不能为空")
		return
	}
	if req.Number == 0 {
		req.Number = 1
	}
	id := randomID("e_")
	dir := filepath.Join(a.projectDir(pid), "episodes", id)
	if err := os.MkdirAll(dir, 0755); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	ep := map[string]any{"episode_id": id, "project_id": pid, "number": req.Number, "name": strings.TrimSpace(req.Name), "description": req.Description, "created_at": time.Now().Format("2006-01-02 15:04:05")}
	if err := writeObject(filepath.Join(dir, "episode.json"), ep); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, ep)
}

func (a *workflowAPI) deleteEpisode(w http.ResponseWriter, r *http.Request) {
	_, dir, err := a.episode(r.PathValue("episodeID"))
	if err != nil {
		apiError(w, 404, "剧集不存在")
		return
	}
	if !safeUnder(filepath.Join(a.root, "projects"), dir) {
		apiError(w, 400, "剧集路径无效")
		return
	}
	if err := os.RemoveAll(dir); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (a *workflowAPI) listCharacters(w http.ResponseWriter, _ *http.Request) {
	root := filepath.Join(a.root, "characters")
	entries, _ := os.ReadDir(root)
	items := make([]map[string]any, 0)
	for _, entry := range entries {
		if entry.IsDir() {
			value, err := readObject(filepath.Join(root, entry.Name(), "character.json"))
			if err == nil {
				items = append(items, value)
			}
		}
	}
	writeJSON(w, 200, map[string]any{"items": items})
}

type characterInput struct {
	Name           string `json:"name"`
	VoiceID        string `json:"voice_id"`
	DefaultEmotion string `json:"default_emotion"`
	Description    string `json:"description"`
	CloneMode      string `json:"clone_mode"`
	QunxiangID     string `json:"qunxiang_id"`
}

func (a *workflowAPI) decodeCharacter(w http.ResponseWriter, r *http.Request) (characterInput, bool) {
	var req characterInput
	if json.NewDecoder(r.Body).Decode(&req) != nil || strings.TrimSpace(req.Name) == "" {
		apiError(w, 400, "角色名称不能为空")
		return req, false
	}
	if req.CloneMode == "" {
		req.CloneMode = "auto"
	}
	if req.CloneMode != "auto" && req.CloneMode != "controllable_clone" && req.CloneMode != "ultimate_clone" {
		apiError(w, 400, "角色克隆模式无效")
		return req, false
	}
	if req.VoiceID != "" {
		if _, _, err := (&libraryAPI{root: a.root}).loadVoice(req.VoiceID); err != nil {
			apiError(w, 404, "音色不存在: "+req.VoiceID)
			return req, false
		}
	}
	return req, true
}

func (a *workflowAPI) createCharacter(w http.ResponseWriter, r *http.Request) {
	req, ok := a.decodeCharacter(w, r)
	if !ok {
		return
	}
	id := randomID("c_")
	value := map[string]any{"char_id": id, "name": strings.TrimSpace(req.Name), "description": req.Description, "voice_id": req.VoiceID, "default_emotion": req.DefaultEmotion, "clone_mode": req.CloneMode, "created_at": time.Now().Format("2006-01-02 15:04:05")}
	if req.QunxiangID != "" {
		value["qunxiang_id"] = req.QunxiangID
	}
	if err := writeObject(filepath.Join(a.characterDir(id), "character.json"), value); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, value)
}

func (a *workflowAPI) updateCharacter(w http.ResponseWriter, r *http.Request) {
	req, ok := a.decodeCharacter(w, r)
	if !ok {
		return
	}
	id := r.PathValue("characterID")
	if !workflowIDPattern.MatchString(id) || !strings.HasPrefix(id, "c_") {
		apiError(w, 404, "角色不存在: "+id)
		return
	}
	path := filepath.Join(a.characterDir(id), "character.json")
	value, err := readObject(path)
	if err != nil {
		apiError(w, 404, "角色不存在: "+id)
		return
	}
	value["name"], value["description"], value["voice_id"] = strings.TrimSpace(req.Name), req.Description, req.VoiceID
	value["default_emotion"], value["clone_mode"] = req.DefaultEmotion, req.CloneMode
	if req.QunxiangID != "" {
		value["qunxiang_id"] = req.QunxiangID
	}
	if err := writeObject(path, value); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, value)
}

func (a *workflowAPI) deleteCharacter(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("characterID")
	dir := a.characterDir(id)
	if !workflowIDPattern.MatchString(id) || !strings.HasPrefix(id, "c_") || !safeUnder(filepath.Join(a.root, "characters"), dir) {
		apiError(w, 404, "角色不存在")
		return
	}
	if _, err := os.Stat(filepath.Join(dir, "character.json")); err != nil {
		apiError(w, 404, "角色不存在")
		return
	}
	if err := os.RemoveAll(dir); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func (a *workflowAPI) listScenes(w http.ResponseWriter, _ *http.Request) {
	root := filepath.Join(a.root, "scenes")
	entries, _ := os.ReadDir(root)
	items := make([]map[string]any, 0)
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		scene, err := readObject(filepath.Join(root, entry.Name(), "scene.json"))
		if err != nil {
			continue
		}
		item := map[string]any{"scene_id": scene["scene_id"], "name": scene["name"], "created_at": scene["created_at"], "line_count": scene["line_count"], "project_id": scene["project_id"], "episode_id": scene["episode_id"], "episode_name": scene["episode_name"], "has_mix": scene["mix"] != nil, "draft": scene["draft"] == true}
		items = append(items, item)
	}
	sort.Slice(items, func(i, j int) bool { return fmt.Sprint(items[i]["scene_id"]) > fmt.Sprint(items[j]["scene_id"]) })
	writeJSON(w, 200, map[string]any{"items": items})
}

func (a *workflowAPI) sceneDetail(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("sceneID")
	if !workflowIDPattern.MatchString(id) {
		apiError(w, 404, "场次不存在")
		return
	}
	scene, err := readObject(filepath.Join(a.sceneDir(id), "scene.json"))
	if err != nil {
		apiError(w, 404, "场次不存在: "+id)
		return
	}
	writeJSON(w, 200, scene)
}

func (a *workflowAPI) assignScene(w http.ResponseWriter, r *http.Request) {
	var req struct {
		EpisodeID string `json:"episode_id"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil {
		apiError(w, 400, "剧集信息无效")
		return
	}
	scenePath := filepath.Join(a.sceneDir(r.PathValue("sceneID")), "scene.json")
	scene, err := readObject(scenePath)
	if err != nil {
		apiError(w, 404, "场次不存在")
		return
	}
	ep, _, err := a.episode(req.EpisodeID)
	if err != nil {
		apiError(w, 400, "剧集不存在: "+req.EpisodeID)
		return
	}
	scene["episode_id"], scene["project_id"], scene["episode_name"] = req.EpisodeID, ep["project_id"], ep["name"]
	if err := writeObject(scenePath, scene); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, scene)
}

func (a *workflowAPI) setSceneOrder(w http.ResponseWriter, r *http.Request) {
	var req struct {
		SceneIDs []string `json:"scene_ids"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil {
		apiError(w, 400, "场次顺序无效")
		return
	}
	id := r.PathValue("episodeID")
	ep, dir, err := a.episode(id)
	if err != nil {
		apiError(w, 404, "剧集不存在: "+id)
		return
	}
	allowed := map[string]bool{}
	entries, _ := os.ReadDir(filepath.Join(a.root, "scenes"))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		s, err := readObject(filepath.Join(a.sceneDir(entry.Name()), "scene.json"))
		if err == nil && s["episode_id"] == id {
			allowed[entry.Name()] = true
		}
	}
	for _, sceneID := range req.SceneIDs {
		if !allowed[sceneID] {
			apiError(w, 400, "场次不属于该剧集: "+sceneID)
			return
		}
	}
	ep["scene_order"] = req.SceneIDs
	if err := writeObject(filepath.Join(dir, "episode.json"), ep); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, ep)
}

func number(v any) float64 {
	if n, ok := v.(float64); ok {
		return n
	}
	return 0
}
