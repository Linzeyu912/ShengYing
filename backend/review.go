package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type reviewAPI struct {
	root   string
	worker *url.URL
}

func registerReviewRoutes(mux *http.ServeMux, root string, worker *url.URL) {
	a := &reviewAPI{root: root, worker: worker}
	mux.HandleFunc("POST /api/voices/{voiceID}/auto-review", a.autoReview)
}

func signalReview(path string) (map[string]any, error) {
	wav, err := readWav(path)
	if err != nil {
		return nil, err
	}
	samples := wav.samples
	if len(samples) == 0 {
		return nil, fmt.Errorf("音频为空")
	}
	peak, sumSq, clipped := 0.0, 0.0, 0
	for _, v := range samples {
		if math.Abs(v) > peak {
			peak = math.Abs(v)
		}
		sumSq += v * v
		if math.Abs(v) >= .999 {
			clipped++
		}
	}
	rms := math.Sqrt(sumSq / float64(len(samples)))
	frame := max(1, wav.rate*20/1000)
	levels := make([]float64, 0, (len(samples)+frame-1)/frame)
	maxLevel := 0.0
	for start := 0; start < len(samples); start += frame {
		end := min(start+frame, len(samples))
		s := 0.0
		for _, v := range samples[start:end] {
			s += v * v
		}
		level := math.Sqrt(s / float64(end-start))
		levels = append(levels, level)
		if level > maxLevel {
			maxLevel = level
		}
	}
	quietThreshold := math.Max(.001, maxLevel*.03)
	first, last := -1, -1
	longest, run := 0, 0
	for i, level := range levels {
		quiet := level < quietThreshold
		if !quiet {
			if first < 0 {
				first = i
			}
			last = i
		}
		if quiet && first >= 0 {
			run++
			if run > longest {
				longest = run
			}
		} else if !quiet {
			run = 0
		}
	}
	interiorLongest := 0
	if first >= 0 && last >= first {
		run = 0
		for i := first; i <= last; i++ {
			if levels[i] < quietThreshold {
				run++
				if run > interiorLongest {
					interiorLongest = run
				}
			} else {
				run = 0
			}
		}
	}
	issues := make([]string, 0)
	if rms < .001 {
		issues = append(issues, "接近静音或整体音量过低")
	} else if rms < .01 {
		issues = append(issues, "整体音量偏低，请试听确认")
	}
	clipRatio := float64(clipped) / float64(len(samples))
	if clipRatio > .001 {
		issues = append(issues, "疑似削波失真")
	}
	if float64(interiorLongest)*.02 > 1.5 {
		issues = append(issues, "句中有超过1.5秒的低能量停顿，可能是正常表演，请复核")
	}
	leading, trailing := float64(len(samples))/float64(wav.rate), float64(len(samples))/float64(wav.rate)
	if first >= 0 {
		leading = float64(first*frame) / float64(wav.rate)
		trailing = float64((len(levels)-1-last)*frame) / float64(wav.rate)
	}
	if math.Max(leading, trailing) > 1.5 {
		issues = append(issues, "首尾静音超过1.5秒")
	}
	return map[string]any{"issues": issues, "metrics": map[string]any{"duration_sec": math.Round(float64(len(samples))/float64(wav.rate)*1000) / 1000, "peak": peak, "rms_dbfs": math.Round(20*math.Log10(math.Max(rms, 1e-12))*100) / 100, "clipping_ratio": clipRatio, "longest_pause_sec": math.Round(float64(interiorLongest)*.02*100) / 100, "leading_silence_sec": leading, "trailing_silence_sec": trailing}}, nil
}

func (a *reviewAPI) autoReview(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("voiceID")
	voice, dir, err := (&libraryAPI{root: a.root}).loadVoice(id)
	if err != nil {
		apiError(w, 404, "音色不存在")
		return
	}
	rows := make([]any, 0)
	issues := make([]string, 0)
	errorsFound := make([]string, 0)
	for _, sample := range listMap(voice["samples"]) {
		rel := mapText(sample, "file")
		path := filepath.Join(dir, filepath.FromSlash(rel))
		row := map[string]any{"file": rel}
		if !safeUnder(dir, path) {
			msg := "样本路径越界"
			row["error"] = msg
			errorsFound = append(errorsFound, msg)
			rows = append(rows, row)
			continue
		}
		data, e := os.ReadFile(path)
		if e != nil {
			msg := e.Error()
			row["error"] = msg
			errorsFound = append(errorsFound, msg)
			rows = append(rows, row)
			continue
		}
		digest := sha256.Sum256(data)
		row["sha256"] = hex.EncodeToString(digest[:])
		signal, e := signalReview(path)
		if e != nil {
			row["error"] = e.Error()
			errorsFound = append(errorsFound, e.Error())
			rows = append(rows, row)
			continue
		}
		row["signal"] = signal
		for _, item := range anyStrings(signal["issues"]) {
			issues = append(issues, item)
		}
		worker := *a.worker
		worker.Path = strings.TrimSuffix(worker.Path, "/") + "/internal/inference/nisqa"
		payload, _ := json.Marshal(map[string]string{"path": path})
		call, _ := http.NewRequestWithContext(r.Context(), http.MethodPost, worker.String(), strings.NewReader(string(payload)))
		call.Header.Set("Content-Type", "application/json")
		response, e := (&http.Client{Timeout: 4 * time.Minute}).Do(call)
		if e != nil {
			row["error"] = e.Error()
			errorsFound = append(errorsFound, e.Error())
			rows = append(rows, row)
			continue
		}
		var listening map[string]any
		decodeErr := json.NewDecoder(response.Body).Decode(&listening)
		response.Body.Close()
		if decodeErr != nil || response.StatusCode < 200 || response.StatusCode >= 300 {
			msg := "NISQA 推理失败"
			if decodeErr != nil {
				msg = decodeErr.Error()
			}
			row["error"] = msg
			errorsFound = append(errorsFound, msg)
			rows = append(rows, row)
			continue
		}
		row["listening"] = listening
		for _, item := range anyStrings(listening["issues"]) {
			issues = append(issues, item)
		}
		rows = append(rows, row)
	}
	if len(rows) == 0 {
		errorsFound = append(errorsFound, "没有可审核的音频样本")
	}
	status := "no_findings"
	if len(errorsFound) > 0 {
		status = "incomplete"
	}
	if len(issues) > 0 {
		status = "needs_review"
	}
	report := map[string]any{"status": status, "checked_at": time.Now().Format("2006-01-02 15:04:05"), "issues": issues, "errors": errorsFound, "samples": rows, "offline": true, "version": "1.0"}
	voice["auto_review"] = report
	if err := writeObject(filepath.Join(dir, "voice.json"), voice); err != nil {
		apiError(w, 500, err.Error())
		return
	}
	writeJSON(w, 200, report)
}
