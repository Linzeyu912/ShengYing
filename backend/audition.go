package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

func registerAuditionRoutes(mux *http.ServeMux, root string, worker *url.URL) {
	a := &auditionAPI{root: root, synth: &synthAPI{root: root, worker: worker}}
	mux.HandleFunc("POST /api/auditions/preview", a.preview)
	mux.HandleFunc("POST /api/auditions/generate", a.generate)
}

type auditionAPI struct {
	root  string
	synth *synthAPI
}

func scriptGender(text string) string {
	if strings.ContainsAny(text, "女姐姨母妹娘") {
		return "female"
	}
	if strings.ContainsAny(text, "男哥叔爷兄弟父亲爸爸少年") {
		return "male"
	}
	return "unknown"
}
func roleTraits(text string) map[string]bool {
	sets := map[string][]string{"年轻": {"年轻", "少年", "少女", "青年"}, "年长": {"年老", "老人", "老年", "奶奶", "爷爷"}, "童声": {"儿童", "小孩", "童声", "萝莉"}, "温柔": {"温柔", "温和", "柔和"}, "低沉": {"低沉", "浑厚", "低音"}, "沙哑": {"沙哑", "嘶哑", "烟嗓"}, "成熟": {"成熟", "御姐", "大叔", "干练"}, "机械": {"机械", "机器人", "电子音"}}
	out := map[string]bool{}
	for key, words := range sets {
		for _, word := range words {
			if strings.Contains(text, word) {
				out[key] = true
				break
			}
		}
	}
	return out
}
func (a *auditionAPI) voices() []map[string]any {
	entries, _ := os.ReadDir(filepath.Join(a.root, "assets", "voices"))
	out := make([]map[string]any, 0)
	for _, entry := range entries {
		if entry.IsDir() {
			v, _, err := (&libraryAPI{root: a.root}).loadVoice(entry.Name())
			if err == nil {
				out = append(out, v)
			}
		}
	}
	return out
}
func (a *auditionAPI) matchVoice(description string, voices []map[string]any) (string, string) {
	gender := scriptGender(description)
	wanted := roleTraits(description)
	best := ""
	bestCount := -1
	for _, v := range voices {
		if len(listMap(v["samples"])) == 0 || mapText(v, "voice_source") == "lora" {
			continue
		}
		if gender != "unknown" && mapText(v, "gender") != gender {
			continue
		}
		voiceText := mapText(v, "name") + " " + mapText(v, "description")
		for _, tag := range anyStrings(v["timbre_tags"]) {
			voiceText += " " + tag
		}
		available := roleTraits(voiceText)
		matches := 0
		ok := true
		for trait := range wanted {
			if available[trait] {
				matches++
			} else {
				ok = false
			}
		}
		if len(wanted) > 0 && !ok {
			continue
		}
		if len(wanted) == 0 && gender == "unknown" {
			continue
		}
		if matches > bestCount {
			best = mapText(v, "voice_id")
			bestCount = matches
		}
	}
	if best != "" {
		return best, "库内音色符合：" + gender
	}
	return "", "未找到符合已知特征的可用音色，将设计新音色"
}

func (a *auditionAPI) preview(w http.ResponseWriter, r *http.Request) {
	data, name, err := readUploadedScript(w, r)
	if err != nil {
		apiError(w, 400, err.Error())
		return
	}
	paragraphs, err := scriptParagraphs(data, name)
	if err != nil {
		apiError(w, 400, err.Error())
		return
	}
	parsed := parseShotScript(paragraphs)
	hints := map[string]string{}
	for _, hint := range listMap(parsed["cast_hints"]) {
		hints[mapText(hint, "name")] = mapText(hint, "desc")
	}
	lines := listMap(parsed["lines"])
	if len(lines) == 0 && len(hints) == 0 {
		apiError(w, 400, "未识别到角色。请使用“角色名（情绪）：台词”或分镜剧本格式。")
		return
	}
	names := make([]string, 0)
	for _, line := range lines {
		name := mapText(line, "role")
		if !containsString(names, name) {
			names = append(names, name)
		}
	}
	for name := range hints {
		if !containsString(names, name) {
			names = append(names, name)
		}
	}
	if len(names) > 100 {
		apiError(w, 400, "一次最多处理100个角色，请拆分剧本")
		return
	}
	voices := a.voices()
	items := make([]any, 0, len(names))
	for _, name := range names {
		own := make([]map[string]any, 0)
		for _, line := range lines {
			if mapText(line, "role") == name {
				own = append(own, line)
			}
		}
		var rep map[string]any
		for _, line := range own {
			if n := len([]rune(mapText(line, "text"))); n >= 8 && n <= 150 {
				rep = line
				break
			}
		}
		if rep == nil && len(own) > 0 {
			rep = own[0]
		}
		text := fmt.Sprintf("你好，我是%s，这是我的声音。", name)
		emotion := "平静"
		if rep != nil {
			text = mapText(rep, "text")
			emotion = mapText(rep, "emotion")
			if m := regexp.MustCompile(`.*?[。！？!?](?:[”"])?`).FindString(text); m != "" {
				text = m
			} else {
				text = clip(text, 300)
			}
		}
		desc := hints[name]
		vid, reason := a.matchVoice(name+" "+desc, voices)
		items = append(items, map[string]any{"name": name, "description": desc, "control": firstNonEmpty(desc, "自然清晰的普通话，适合该角色的声音"), "text": text, "emotion": emotion, "voice_id": vid, "reason": reason, "line_note": optionalValue(func() string {
			if rep == nil {
				return "剧本无台词，使用试音句"
			}
			return ""
		}())})
	}
	writeJSON(w, 200, map[string]any{"title": parsed["title"], "items": items, "note": "依据剧本人物设定和声音关键词匹配；请核对角色列表。新音色需试听确认。"})
}

func (a *auditionAPI) generate(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Name        string `json:"name"`
		Description string `json:"description"`
		Control     string `json:"control"`
		Text        string `json:"text"`
		Emotion     string `json:"emotion"`
		VoiceID     string `json:"voice_id"`
	}
	if json.NewDecoder(r.Body).Decode(&req) != nil || strings.TrimSpace(req.Name) == "" || strings.TrimSpace(req.Text) == "" || strings.TrimSpace(req.Control) == "" {
		apiError(w, 400, "角色名、试音台词和声音描述不能为空")
		return
	}
	req.Name = strings.TrimSpace(req.Name)
	if req.VoiceID != "" {
		voice, _, err := (&libraryAPI{root: a.root}).loadVoice(req.VoiceID)
		if err != nil || len(listMap(voice["samples"])) == 0 {
			apiError(w, 400, "所选音色不存在或缺少参考音频，请重新选择")
			return
		}
	}
	mode := "design"
	control := req.Control
	if req.VoiceID != "" {
		mode = "auto"
		control = ""
	}
	record, err := a.synth.generateRecord(ttsRequest{Text: req.Text, Control: control, VoiceID: req.VoiceID, Emotion: req.Emotion, Mode: mode}, map[string]any{"audition_role": req.Name, "audition_description": req.Description})
	if err != nil {
		apiError(w, 502, "角色试音失败: "+err.Error())
		return
	}
	voiceID := req.VoiceID
	if voiceID == "" {
		rec := httptest.NewRecorder()
		body, _ := json.Marshal(map[string]any{"name": req.Name + " · 试音", "gender": scriptGender(req.Description + req.Name)})
		pr := httptest.NewRequest(http.MethodPost, "/api/tts/records/"+mapText(record, "record_id")+"/promote", bytes.NewReader(body))
		pr.SetPathValue("recordID", mapText(record, "record_id"))
		(&recordAPI{root: a.root}).promote(rec, pr)
		var voice map[string]any
		if rec.Code < 200 || rec.Code >= 300 || json.Unmarshal(rec.Body.Bytes(), &voice) != nil {
			apiError(w, 500, "试音音色固化失败")
			return
		}
		voiceID = mapText(voice, "voice_id")
	}
	charID := randomID("c_")
	char := map[string]any{"char_id": charID, "name": req.Name, "description": req.Description, "voice_id": voiceID, "default_emotion": req.Emotion, "clone_mode": "auto", "created_at": time.Now().Format("2006-01-02 15:04:05")}
	if err := writeObject(filepath.Join(a.root, "characters", charID, "character.json"), char); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, map[string]any{"record_id": record["record_id"], "voice_id": voiceID, "char_id": charID, "audio_url": "/api/tts/records/" + mapText(record, "record_id") + "/audio", "created_voice": req.VoiceID == ""})
}

func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}
