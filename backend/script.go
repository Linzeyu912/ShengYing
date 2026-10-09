package main

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"encoding/xml"
	"fmt"
	"html"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

var shotPattern = regexp.MustCompile(`^镜头\s*(\d+)`)
var roleHintPattern = regexp.MustCompile(`([^、（）()]+?)[（(]([^）)]*)[）)]`)
var contentTags = []string{"画面", "音效", "台词", "字幕", "运动", "动作", "情绪"}
var toneEmotions = []struct{ word, emotion string }{{"紧张", "紧张"}, {"轻蔑", "严肃"}, {"嘲讽", "严肃"}, {"严肃", "严肃"}, {"愤怒", "愤怒"}, {"生气", "愤怒"}, {"笑", "开心"}, {"开心", "开心"}, {"高兴", "开心"}, {"错愕", "惊讶"}, {"惊讶", "惊讶"}, {"哭", "悲伤"}, {"鼻音", "悲伤"}, {"哽咽", "悲伤"}, {"难过", "悲伤"}, {"温柔", "温柔"}, {"放缓", "温柔"}, {"温和", "温柔"}, {"疲惫", "疲惫"}, {"调皮", "调皮"}, {"低声", "平静"}, {"平淡", "平静"}, {"平静", "平静"}, {"自语", "平静"}, {"头也不抬", "平静"}, {"公事公办", "平静"}}
var dialogueMarker = regexp.MustCompile(`[（(]([^）)]+)[）)][：:]?|([^：:（）()。，、！？!?·\s]{1,6})(?:[（(]([^）)]*)[）)])?[：:]`)

func scriptParagraphs(data []byte, filename string) ([]string, error) {
	ext := strings.ToLower(filepath.Ext(filename))
	var text string
	if ext == ".txt" || ext == ".md" || ext == ".markdown" {
		text = strings.TrimPrefix(string(data), "\ufeff")
		if !utf8.Valid(data) {
			return nil, fmt.Errorf("剧本编码无效")
		}
		if ext == ".md" || ext == ".markdown" {
			text = regexp.MustCompile(`(?m)^\s{0,3}#{1,6}\s+`).ReplaceAllString(text, "")
			text = regexp.MustCompile(`(?m)^\s*(?:>\s*|[-+*]\s+)`).ReplaceAllString(text, "")
			text = regexp.MustCompile(`\*\*(.+?)\*\*|__(.+?)__`).ReplaceAllString(text, "$1$2")
		}
	} else if ext == ".docx" {
		z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if err != nil {
			return nil, fmt.Errorf("DOCX 文件损坏或不是有效的 Word 文档")
		}
		var doc *zip.File
		for _, f := range z.File {
			if f.Name == "word/document.xml" {
				doc = f
				break
			}
		}
		if doc == nil {
			return nil, fmt.Errorf("DOCX 文件损坏或不是有效的 Word 文档")
		}
		rc, err := doc.Open()
		if err != nil {
			return nil, err
		}
		defer rc.Close()
		decoder := xml.NewDecoder(rc)
		var out []string
		inPara := false
		var para strings.Builder
		for {
			tok, e := decoder.Token()
			if e == io.EOF {
				break
			}
			if e != nil {
				return nil, fmt.Errorf("DOCX 文件损坏或不是有效的 Word 文档")
			}
			switch t := tok.(type) {
			case xml.StartElement:
				if t.Name.Local == "p" {
					inPara = true
					para.Reset()
				}
			case xml.CharData:
				if inPara {
					para.Write([]byte(t))
				}
			case xml.EndElement:
				if t.Name.Local == "p" && inPara {
					line := strings.TrimSpace(html.UnescapeString(para.String()))
					if line != "" {
						out = append(out, line)
					}
					inPara = false
				}
			}
		}
		return out, nil
	} else {
		return nil, fmt.Errorf("仅支持 .docx / .txt / .md / .markdown 文件: %s", filename)
	}
	lines := strings.Split(text, "\n")
	out := make([]string, 0, len(lines))
	for _, line := range lines {
		if line = strings.TrimSpace(line); line != "" {
			out = append(out, line)
		}
	}
	return out, nil
}

func toneEmotion(tone string) string {
	if tone == "" {
		return "平静"
	}
	for _, p := range toneEmotions {
		if strings.Contains(tone, p.word) {
			return p.emotion
		}
	}
	return "平静"
}
func splitTagContent(content string) map[string]string {
	type marker struct {
		pos int
		tag string
	}
	markers := make([]marker, 0)
	for _, tag := range contentTags {
		start := 0
		for {
			idx := strings.Index(content[start:], tag)
			if idx < 0 {
				break
			}
			idx += start
			markers = append(markers, marker{idx, tag})
			start = idx + len(tag)
		}
	}
	sort.Slice(markers, func(i, j int) bool { return markers[i].pos < markers[j].pos })
	out := map[string]string{}
	for i, m := range markers {
		end := len(content)
		if i+1 < len(markers) {
			end = markers[i+1].pos
		}
		out[m.tag] = strings.TrimSpace(content[m.pos+len(m.tag) : end])
	}
	return out
}
func splitRoleTone(s string, known []string) (string, string) {
	s = strings.TrimSpace(s)
	for _, sep := range []string{"，", ",", "、"} {
		if i := strings.Index(s, sep); i >= 0 {
			return strings.TrimSpace(s[:i]), strings.TrimSpace(s[i+len(sep):])
		}
	}
	sort.Slice(known, func(i, j int) bool { return len(known[i]) > len(known[j]) })
	for _, role := range known {
		if strings.HasPrefix(s, role) {
			return role, strings.TrimSpace(strings.TrimPrefix(s, role))
		}
	}
	words := make([]string, 0)
	for _, p := range toneEmotions {
		words = append(words, p.word)
	}
	sort.Slice(words, func(i, j int) bool { return len(words[i]) > len(words[j]) })
	for _, word := range words {
		if strings.HasSuffix(s, word) {
			return strings.TrimSpace(strings.TrimSuffix(s, word)), word
		}
	}
	return s, ""
}
func parseDialogueBlock(text string, known []string) [][3]string {
	text = strings.TrimLeft(strings.TrimSpace(text), "：:")
	if text == "" || strings.HasPrefix(text, "无") {
		return nil
	}
	out := make([][3]string, 0)
	lastRole, lastTone, lastEnd := "", "", 0
	for _, m := range dialogueMarker.FindAllStringSubmatchIndex(text, -1) {
		start, end := m[0], m[1]
		seg := strings.TrimSpace(text[lastEnd:start])
		if lastRole != "" && seg != "" {
			out = append(out, [3]string{lastRole, lastTone, seg})
		}
		if m[2] >= 0 {
			lastRole, lastTone = splitRoleTone(text[m[2]:m[3]], known)
		} else {
			lastRole = strings.TrimSpace(text[m[4]:m[5]])
			lastTone = ""
			if m[6] >= 0 {
				lastTone = strings.TrimSpace(text[m[6]:m[7]])
			}
		}
		lastEnd = end
	}
	seg := strings.TrimSpace(text[lastEnd:])
	if lastRole != "" && seg != "" {
		out = append(out, [3]string{lastRole, lastTone, seg})
	}
	return out
}
func extractShotLines(content string, known []string) [][3]string {
	if content == "" {
		return nil
	}
	segments := splitTagContent(content)
	lines := make([][3]string, 0)
	if v := segments["台词"]; v != "" {
		lines = append(lines, parseDialogueBlock(v, known)...)
	}
	if v := segments["字幕"]; v != "" {
		v = regexp.MustCompile(`^[（(][^）)]*[）)]`).ReplaceAllString(strings.TrimSpace(v), "")
		if v != "" {
			lines = append(lines, [3]string{"旁白", "", v})
		}
	}
	return lines
}
func parseShotScript(paragraphs []string) map[string]any {
	title := ""
	if len(paragraphs) > 0 {
		title = paragraphs[0]
	}
	hints := make([]map[string]any, 0)
	for _, p := range paragraphs {
		loc := strings.Index(p, "核心人物")
		if loc >= 0 {
			rest := p[loc+len("核心人物"):]
			if i := strings.IndexAny(rest, "：:"); i >= 0 {
				for _, m := range roleHintPattern.FindAllStringSubmatch(rest[i+1:], -1) {
					name := strings.TrimSpace(m[1])
					if name != "" {
						hints = append(hints, map[string]any{"name": name, "desc": strings.TrimSpace(m[2])})
					}
				}
			}
			break
		}
	}
	known := make([]string, 0, len(hints)+1)
	for _, h := range hints {
		known = append(known, mapText(h, "name"))
	}
	known = append(known, "旁白")
	lines := make([]any, 0)
	shots := 0
	for i := 0; i < len(paragraphs); i++ {
		match := shotPattern.FindStringSubmatch(paragraphs[i])
		if match == nil {
			continue
		}
		shots++
		var content string
		if i+1 < len(paragraphs) && shotPattern.FindStringSubmatch(paragraphs[i+1]) == nil {
			content = paragraphs[i+1]
			i++
		}
		shotNo := 0
		fmt.Sscanf(match[1], "%d", &shotNo)
		for _, line := range extractShotLines(content, append([]string(nil), known...)) {
			role := line[0]
			if !containsString(known, role) && len([]rune(role)) >= 3 {
				for _, r := range known {
					if strings.Contains(role, r) || strings.Contains(r, role) {
						role = r
						break
					}
				}
			}
			lines = append(lines, map[string]any{"shot": shotNo, "role": role, "emotion": toneEmotion(line[1]), "text": line[2]})
		}
	}
	return map[string]any{"title": title, "cast_hints": hints, "lines": lines, "shot_count": shots}
}
func containsString(values []string, want string) bool {
	for _, v := range values {
		if v == want {
			return true
		}
	}
	return false
}

type scriptAPI struct {
	root   string
	synth  *synthAPI
	worker *url.URL
}

func registerScriptRoutes(mux *http.ServeMux, root string, worker *url.URL) {
	a := &scriptAPI{root: root, synth: &synthAPI{root: root, worker: worker}, worker: worker}
	mux.HandleFunc("POST /api/script-dub/preview", func(w http.ResponseWriter, r *http.Request) {
		data, name, err := readUploadedScript(w, r)
		if err != nil {
			apiError(w, 400, err.Error())
			return
		}
		paras, err := scriptParagraphs(data, name)
		if err != nil {
			apiError(w, 400, err.Error())
			return
		}
		writeJSON(w, 200, parseShotScript(paras))
	})
	mux.HandleFunc("POST /api/script-dub/generate", a.generate)
}

func (a *scriptAPI) generate(w http.ResponseWriter, r *http.Request) {
	data, filename, err := readUploadedScript(w, r)
	if err != nil {
		apiError(w, 400, err.Error())
		return
	}
	paragraphs, err := scriptParagraphs(data, filename)
	if err != nil {
		apiError(w, 400, err.Error())
		return
	}
	script := parseShotScript(paragraphs)
	lines := listMap(script["lines"])
	if len(lines) == 0 {
		apiError(w, 400, "剧本未解析出任何台词，请确认是分镜头剧本格式")
		return
	}
	gap := 500
	if value := r.FormValue("gap_ms"); value != "" {
		if _, err := fmt.Sscanf(value, "%d", &gap); err != nil {
			apiError(w, 400, "台词间隔无效")
			return
		}
	}
	if gap < 0 || gap > 10000 {
		apiError(w, 400, "台词间隔必须在0–10000毫秒之间")
		return
	}
	merge := r.FormValue("merge_audio") != "false" && r.FormValue("merge_audio") != "0"
	sceneName := strings.TrimSpace(r.FormValue("scene_name"))
	if sceneName == "" {
		sceneName = mapText(script, "title")
	}
	episodeID := strings.TrimSpace(r.FormValue("episode_id"))
	castOverride := map[string]string{}
	if raw := r.FormValue("cast_json"); raw != "" {
		if err := json.Unmarshal([]byte(raw), &castOverride); err != nil {
			apiError(w, 400, "人物音色映射不是有效 JSON")
			return
		}
	}
	hints := map[string]string{}
	for _, hint := range listMap(script["cast_hints"]) {
		hints[mapText(hint, "name")] = mapText(hint, "desc")
	}
	roles := make([]map[string]any, 0)
	roleNames := make([]string, 0)
	for _, line := range lines {
		name := mapText(line, "role")
		if !containsString(roleNames, name) {
			roleNames = append(roleNames, name)
			roles = append(roles, map[string]any{"name": name, "desc": hints[name]})
		}
	}
	voiceSvc := &auditionAPI{root: a.root}
	voices := voiceSvc.voices()
	cast := map[string]string{}
	castReport := make([]any, 0)
	for _, role := range roles {
		name, desc := mapText(role, "name"), mapText(role, "desc")
		vid := castOverride[name]
		note := "手动指定"
		if vid != "" {
			if _, _, e := (&libraryAPI{root: a.root}).loadVoice(vid); e != nil {
				note = "指定音色不存在：" + vid
				vid = ""
			}
		}
		if vid == "" {
			vid, note = voiceSvc.matchVoice(name+" "+desc, voices)
		}
		var voice map[string]any
		if vid != "" {
			voice, _, _ = (&libraryAPI{root: a.root}).loadVoice(vid)
		}
		ready := voice != nil && len(listMap(voice["samples"])) > 0
		cast[name] = vid
		castReport = append(castReport, map[string]any{"role": name, "desc": desc, "voice_id": optionalValue(vid), "note": note, "voice_name": mapText(voice, "name"), "ready": ready})
	}
	for _, role := range roles {
		name := mapText(role, "name")
		if cast[name] == "" {
			apiError(w, 400, "以下角色未能自动匹配音色，请手动选择: "+name)
			return
		}
		voice, _, _ := (&libraryAPI{root: a.root}).loadVoice(cast[name])
		if len(listMap(voice["samples"])) == 0 {
			apiError(w, 400, "角色「"+name+"」所选音色缺少参考音频")
			return
		}
	}
	charactersRoot := filepath.Join(a.root, "characters")
	entries, _ := os.ReadDir(charactersRoot)
	existing := make([]map[string]any, 0)
	for _, entry := range entries {
		if entry.IsDir() {
			c, e := readObject(filepath.Join(charactersRoot, entry.Name(), "character.json"))
			if e == nil {
				existing = append(existing, c)
			}
		}
	}
	charIDs := map[string]string{}
	charReport := make([]any, 0)
	for _, role := range roles {
		name, desc := mapText(role, "name"), mapText(role, "desc")
		vid := cast[name]
		var chosen, sameName map[string]any
		for _, c := range existing {
			if mapText(c, "name") == name {
				if sameName == nil {
					sameName = c
				}
				if mapText(c, "voice_id") == vid {
					chosen = c
					break
				}
			}
		}
		action := "复用"
		if chosen == nil && sameName != nil {
			chosen = sameName
			chosen["voice_id"] = vid
			if desc != "" {
				chosen["description"] = desc
			}
			_ = writeObject(filepath.Join(charactersRoot, mapText(chosen, "char_id"), "character.json"), chosen)
			action = "更新绑定"
		}
		if chosen == nil {
			id := randomID("c_")
			chosen = map[string]any{"char_id": id, "name": name, "description": desc, "voice_id": vid, "default_emotion": "", "clone_mode": "auto", "created_at": time.Now().Format("2006-01-02 15:04:05")}
			if err := writeObject(filepath.Join(charactersRoot, id, "character.json"), chosen); err != nil {
				apiError(w, 500, err.Error())
				return
			}
			existing = append(existing, chosen)
			action = "新建"
		}
		charIDs[name] = mapText(chosen, "char_id")
		charReport = append(charReport, map[string]any{"role": name, "char_id": charIDs[name], "voice_id": vid, "action": action})
	}
	dialogueLines := make([]any, 0, len(lines))
	for _, line := range lines {
		dialogueLines = append(dialogueLines, map[string]any{"character_id": charIDs[mapText(line, "role")], "text": line["text"], "emotion": line["emotion"]})
	}
	batchBody, _ := json.Marshal(map[string]any{"scene_name": sceneName, "lines": dialogueLines, "episode_id": episodeID})
	batchReq := httptest.NewRequest(http.MethodPost, "/api/dialogue/batch", bytes.NewReader(batchBody))
	batchRec := httptest.NewRecorder()
	a.synth.dialogueBatch(batchRec, batchReq)
	var scene map[string]any
	if batchRec.Code < 200 || batchRec.Code >= 300 || json.Unmarshal(batchRec.Body.Bytes(), &scene) != nil {
		apiError(w, 502, "剧本配音失败: "+batchRec.Body.String())
		return
	}
	result := map[string]any{"title": script["title"], "roles": roles, "cast": cast, "cast_report": castReport, "character_report": charReport, "line_count": len(lines), "scene_id": scene["scene_id"], "episode_id": optionalValue(episodeID), "merged": merge, "merge_mode": nil, "gap_ms": nil, "duration_sec": nil}
	if merge {
		form := new(bytes.Buffer)
		mw := multipart.NewWriter(form)
		_ = mw.WriteField("scene_id", mapText(scene, "scene_id"))
		_ = mw.WriteField("gap_ms", fmt.Sprint(gap))
		_ = mw.Close()
		mergeReq := httptest.NewRequest(http.MethodPost, "/api/script-dub/merge", form)
		mergeReq.Header.Set("Content-Type", mw.FormDataContentType())
		mergeRec := httptest.NewRecorder()
		(&audioAPI{root: a.root}).mergeScriptScene(mergeRec, mergeReq)
		var merged map[string]any
		if mergeRec.Code < 200 || mergeRec.Code >= 300 || json.Unmarshal(mergeRec.Body.Bytes(), &merged) != nil {
			apiError(w, 500, "台词已生成，但合并失败: "+mergeRec.Body.String())
			return
		}
		result["merge_mode"], result["gap_ms"], result["duration_sec"] = merged["merge_mode"], merged["gap_ms"], merged["duration_sec"]
		result["job_id"], result["audio_url"] = merged["job_id"], merged["audio_url"]
	}
	writeJSON(w, 200, result)
}

func readUploadedScript(w http.ResponseWriter, r *http.Request) ([]byte, string, error) {
	r.Body = http.MaxBytesReader(w, r.Body, 20<<20)
	if err := r.ParseMultipartForm(20 << 20); err != nil {
		return nil, "", fmt.Errorf("剧本文件为空或超过20MB")
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		return nil, "", fmt.Errorf("请选择剧本文件")
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, (20<<20)+1))
	if err != nil {
		return nil, "", err
	}
	if len(data) == 0 {
		return nil, "", fmt.Errorf("剧本文件为空")
	}
	if len(data) > 20<<20 {
		return nil, "", fmt.Errorf("剧本文件超过20MB")
	}
	return data, header.Filename, nil
}
