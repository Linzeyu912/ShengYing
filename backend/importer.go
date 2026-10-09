package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var allowedImportEmotions = map[string]bool{"开心": true, "悲伤": true, "愤怒": true, "惊讶": true, "平静": true, "紧张": true, "温柔": true, "严肃": true, "调皮": true, "疲惫": true}

func registerImportRoutes(mux *http.ServeMux, root string) {
	a := &importAPI{root: root}
	mux.HandleFunc("POST /api/import/preview", a.preview)
	mux.HandleFunc("POST /api/import/execute", a.execute)
}

type importAPI struct{ root string }
type importPackage struct {
	root       string
	manifest   map[string]any
	characters map[string]map[string]any
	scenes     map[string]map[string]any
	scripts    map[string]map[string]any
}

func loadImportPackage(path string) (*importPackage, error) {
	root, err := filepath.Abs(strings.TrimSpace(path))
	if err != nil {
		return nil, err
	}
	manifest, err := readObject(filepath.Join(root, "manifest.json"))
	if err != nil {
		return nil, fmt.Errorf("资产包缺少 manifest.json: %w", err)
	}
	if manifest["package_version"] != "0.1" {
		return nil, fmt.Errorf("不支持的包版本: %v", manifest["package_version"])
	}
	pkg := &importPackage{root: root, manifest: manifest, characters: map[string]map[string]any{}, scenes: map[string]map[string]any{}, scripts: map[string]map[string]any{}}
	readItems := func(raw any, target map[string]map[string]any, idField string) error {
		for _, rel := range anyStrings(raw) {
			file := filepath.Join(root, filepath.FromSlash(rel))
			if !safeUnder(root, file) {
				return fmt.Errorf("资产包包含越界路径: %s", rel)
			}
			obj, e := readObject(file)
			if e != nil {
				return e
			}
			id := mapText(obj, idField)
			if id == "" {
				return fmt.Errorf("文件缺少 %s: %s", idField, rel)
			}
			target[id] = obj
		}
		return nil
	}
	if err := readItems(manifest["characters"], pkg.characters, "id"); err != nil {
		return nil, err
	}
	if err := readItems(manifest["scenes"], pkg.scenes, "id"); err != nil {
		return nil, err
	}
	for _, ep := range listMap(manifest["episodes"]) {
		id, script := mapText(ep, "id"), mapText(ep, "script")
		file := filepath.Join(root, filepath.FromSlash(script))
		if id == "" || script == "" || !safeUnder(root, file) {
			return nil, fmt.Errorf("剧集剧本路径无效: %s", script)
		}
		s, e := readObject(file)
		if e != nil {
			return nil, e
		}
		pkg.scripts[id] = s
	}
	return pkg, nil
}
func anyStrings(v any) []string {
	raw, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(raw))
	for _, x := range raw {
		if s, ok := x.(string); ok {
			out = append(out, s)
		}
	}
	return out
}
func (a *importAPI) voiceSuggestion(hint map[string]any) (string, string) {
	style := mapText(hint, "reference_style")
	if style == "" {
		return "", "无 voice_hint，需人工选音色"
	}
	entries, _ := os.ReadDir(filepath.Join(a.root, "assets", "voices"))
	voices := make([]map[string]any, 0)
	for _, e := range entries {
		if e.IsDir() {
			v, _, err := (&libraryAPI{root: a.root}).loadVoice(e.Name())
			if err == nil {
				voices = append(voices, v)
			}
		}
	}
	for _, v := range voices {
		if mapText(v, "name") == style {
			return mapText(v, "voice_id"), "库内精确匹配「" + style + "」"
		}
	}
	for _, v := range voices {
		name := mapText(v, "name")
		if strings.Contains(style, name) || strings.Contains(name, style) {
			return mapText(v, "voice_id"), "库内模糊匹配「" + name + "」，建议人工确认"
		}
	}
	return "", "库内无匹配，可用 voice_hint.description 做音色设计"
}
func (a *importAPI) preview(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Path string `json:"path"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil {
		apiError(w, 400, "导入路径无效")
		return
	}
	pkg, err := loadImportPackage(req.Path)
	if err != nil {
		apiError(w, 400, err.Error())
		return
	}
	m := pkg.manifest
	project, _ := m["project"].(map[string]any)
	source, _ := m["source"].(map[string]any)
	issues := make([]string, 0)
	characters := make([]any, 0)
	for qid, c := range pkg.characters {
		hint, _ := c["voice_hint"].(map[string]any)
		vid, note := a.voiceSuggestion(hint)
		characters = append(characters, map[string]any{"qunxiang_id": qid, "name": c["name"], "suggested_voice_id": optionalValue(vid), "voice_note": note})
		if vid == "" {
			issues = append(issues, "角色「"+mapText(c, "name")+"」无库内音色匹配")
		}
	}
	lineCount := 0
	for _, ep := range listMap(m["episodes"]) {
		script := pkg.scripts[mapText(ep, "id")]
		if script == nil {
			issues = append(issues, "剧集 "+mapText(ep, "id")+" 缺剧本文件")
			continue
		}
		for _, sc := range listMap(script["scenes"]) {
			ref := mapText(sc, "scene_ref")
			if ref != "" && pkg.scenes[ref] == nil {
				issues = append(issues, "场次「"+mapText(sc, "name")+"」引用的场景卡 "+ref+" 不存在（跳过校验）")
			}
			for _, line := range listMap(sc["lines"]) {
				lineCount++
				if pkg.characters[mapText(line, "character_ref")] == nil {
					issues = append(issues, "台词引用未定义角色: "+mapText(line, "character_ref"))
				}
				emotion := mapText(line, "emotion")
				if emotion != "" && !allowedImportEmotions[emotion] {
					issues = append(issues, "未知情绪「"+emotion+"」（"+mapText(sc, "name")+"·"+clip(mapText(line, "text"), 10)+"…），将降级为平静")
				}
			}
		}
	}
	episodes := make([]any, 0)
	for _, ep := range listMap(m["episodes"]) {
		episodes = append(episodes, map[string]any{"id": ep["id"], "number": ep["number"], "title": ep["title"]})
	}
	sceneCards := make([]string, 0)
	for _, s := range pkg.scenes {
		sceneCards = append(sceneCards, mapText(s, "name"))
	}
	writeJSON(w, 200, map[string]any{"project_title": project["title"], "project_ref": source["project_ref"], "episodes": episodes, "characters": characters, "scene_cards": sceneCards, "line_count": lineCount, "issues": issues})
}

func (a *importAPI) execute(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Path string `json:"path"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil {
		apiError(w, 400, "导入路径无效")
		return
	}
	pkg, err := loadImportPackage(req.Path)
	if err != nil {
		apiError(w, 400, err.Error())
		return
	}
	m := pkg.manifest
	projectMeta, _ := m["project"].(map[string]any)
	source, _ := m["source"].(map[string]any)
	projectRef := mapText(source, "project_ref")
	projectsRoot := filepath.Join(a.root, "projects")
	var project map[string]any
	var im map[string]any
	entries, _ := os.ReadDir(projectsRoot)
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		candidate, er := readObject(filepath.Join(projectsRoot, e.Name(), "import_map.json"))
		if er == nil && projectRef != "" && candidate["project_ref"] == projectRef {
			project, er = readObject(filepath.Join(projectsRoot, e.Name(), "project.json"))
			if er == nil {
				im = candidate
				break
			}
		}
	}
	if project == nil {
		reqProject := struct {
			Name        string `json:"name"`
			Description string `json:"description"`
		}{Name: mapText(projectMeta, "title"), Description: mapText(projectMeta, "description")}
		b, _ := json.Marshal(reqProject)
		rr := httptest.NewRequest(http.MethodPost, "/api/projects", strings.NewReader(string(b)))
		ww := httptest.NewRecorder()
		(&workflowAPI{root: a.root}).createProject(ww, rr)
		if ww.Code >= 300 {
			apiError(w, 500, "创建项目失败")
			return
		}
		_ = json.Unmarshal(ww.Body.Bytes(), &project)
		im = map[string]any{"project_ref": projectRef, "imported_at": time.Now().Format("2006-01-02 15:04:05"), "characters": map[string]any{}, "episodes": map[string]any{}, "scenes": map[string]any{}}
	}
	pid := mapText(project, "project_id")
	episodesMap := mapMap(im["episodes"])
	charactersMap := mapMap(im["characters"])
	scenesMap := mapMap(im["scenes"])
	for _, ep := range listMap(m["episodes"]) {
		id := mapText(ep, "id")
		if _, ok := episodesMap[id]; !ok {
			number := int(mapNumber(ep, "number", 1))
			created, e := createEpisodeDirect(a.root, pid, mapText(ep, "title"), number)
			if e != nil {
				apiError(w, 500, e.Error())
				return
			}
			episodesMap[id] = created["episode_id"]
		}
	}
	bound := make([]any, 0)
	for qid, c := range pkg.characters {
		hint, _ := c["voice_hint"].(map[string]any)
		vid, note := a.voiceSuggestion(hint)
		autoBind := ""
		if strings.HasPrefix(note, "库内精确匹配") {
			autoBind = vid
		}
		personality := anyStrings(c["personality"])
		desc := strings.TrimSpace(strings.Join(personality, "/") + "；" + mapText(c, "appearance"))
		var char map[string]any
		if old := mapText(charactersMap, qid); old != "" {
			char, _ = readObject(filepath.Join(a.root, "characters", old, "character.json"))
			if char != nil {
				char["name"], char["description"] = mapText(c, "name"), desc
				_ = writeObject(filepath.Join(a.root, "characters", old, "character.json"), char)
			}
		}
		if char == nil {
			charID := randomID("c_")
			char = map[string]any{"char_id": charID, "name": mapText(c, "name"), "description": desc, "voice_id": autoBind, "default_emotion": "", "clone_mode": "auto", "created_at": time.Now().Format("2006-01-02 15:04:05"), "qunxiang_id": qid}
			if err := writeObject(filepath.Join(a.root, "characters", charID, "character.json"), char); err != nil {
				apiError(w, 500, err.Error())
				return
			}
			charactersMap[qid] = charID
		}
		bound = append(bound, map[string]any{"name": char["name"], "voice_id": optionalValue(mapText(char, "voice_id")), "note": note})
	}
	metaDir := filepath.Join(a.root, "projects", pid, "scenes_meta")
	_ = os.MkdirAll(metaDir, 0755)
	for qid, s := range pkg.scenes {
		_ = writeObject(filepath.Join(metaDir, qid+".json"), s)
	}
	createdScenes := make([]any, 0)
	skipped := 0
	for _, ep := range listMap(m["episodes"]) {
		script := pkg.scripts[mapText(ep, "id")]
		if script == nil {
			continue
		}
		eid := mapText(episodesMap, mapText(ep, "id"))
		for _, sc := range listMap(script["scenes"]) {
			key := mapText(ep, "id") + ":" + mapText(sc, "name")
			if _, ok := scenesMap[key]; ok {
				skipped++
				continue
			}
			lineRows := make([]any, 0)
			for i, line := range listMap(sc["lines"]) {
				charID := mapText(charactersMap, mapText(line, "character_ref"))
				if charID == "" {
					continue
				}
				char, _ := readObject(filepath.Join(a.root, "characters", charID, "character.json"))
				emotion := mapText(line, "emotion")
				if emotion != "" && !allowedImportEmotions[emotion] {
					emotion = "平静"
				}
				lineRows = append(lineRows, map[string]any{"index": i, "character_id": charID, "character_name": mapText(char, "name"), "text": mapText(line, "text"), "emotion": optionalValue(emotion), "record_id": nil})
			}
			sceneID := time.Now().Format("20060102_150405") + "_" + randomID("")[:4]
			scene := map[string]any{"scene_id": sceneID, "name": mapText(sc, "name"), "draft": true, "project_id": pid, "episode_id": eid, "episode_name": mapText(ep, "title"), "scene_card_ref": optionalValue(mapText(sc, "scene_ref")), "created_at": time.Now().Format("2006-01-02 15:04:05"), "line_count": len(lineRows), "lines": lineRows}
			if err := writeObject(filepath.Join(a.root, "scenes", sceneID, "scene.json"), scene); err != nil {
				apiError(w, 500, err.Error())
				return
			}
			scenesMap[key] = sceneID
			createdScenes = append(createdScenes, map[string]any{"scene_id": sceneID, "name": scene["name"], "lines": len(lineRows)})
		}
	}
	im["episodes"], im["characters"], im["scenes"] = episodesMap, charactersMap, scenesMap
	imPath := filepath.Join(a.root, "projects", pid, "import_map.json")
	if err := writeObject(imPath, im); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, map[string]any{"project_id": pid, "project_name": project["name"], "episodes": len(episodesMap), "characters": bound, "scenes_created": createdScenes, "scenes_skipped_existing": skipped})
}

func mapMap(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}
func createEpisodeDirect(root, pid, name string, number int) (map[string]any, error) {
	if _, err := (&workflowAPI{root: root}).project(pid); err != nil {
		return nil, err
	}
	id := randomID("e_")
	ep := map[string]any{"episode_id": id, "project_id": pid, "number": number, "name": name, "description": "", "created_at": time.Now().Format("2006-01-02 15:04:05")}
	return ep, writeObject(filepath.Join(root, "projects", pid, "episodes", id, "episode.json"), ep)
}
