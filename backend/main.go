package main

import (
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func main() {
	root, err := filepath.Abs(".")
	if err != nil {
		log.Fatal(err)
	}
	workerURL := os.Getenv("SHENGYING_WORKER_URL")
	if workerURL == "" {
		workerURL = "http://127.0.0.1:8318"
	}
	target, err := url.Parse(workerURL)
	if err != nil {
		log.Fatalf("invalid worker URL: %v", err)
	}
	api := httputil.NewSingleHostReverseProxy(target)
	clientDir := filepath.Join(root, "client", "dist")
	static := http.FileServer(http.Dir(clientDir))
	legacyStatic := http.FileServer(http.Dir(filepath.Join(root, "server", "static")))
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true,"frontend":"react","server":"go"}`))
	})
	registerLibraryRoutes(mux, root)
	registerRecordRoutes(mux, root)
	registerSynthesisRoutes(mux, root, target)
	registerReviewRoutes(mux, root, target)
	registerAudioRoutes(mux, root)
	registerImportRoutes(mux, root)
	registerScriptRoutes(mux, root, target)
	registerAuditionRoutes(mux, root, target)
	registerWorkflowRoutes(mux, root)
	mux.HandleFunc("/api/", func(w http.ResponseWriter, r *http.Request) {
		modelAPI := r.Method == http.MethodGet && r.URL.Path == "/api/qwen/design/status"
		visualInference := r.Method == http.MethodPost && r.URL.Path == "/api/auditions/visual-preview"
		if modelAPI || visualInference {
			api.ServeHTTP(w, r)
			return
		}
		apiError(w, http.StatusNotFound, "该接口尚未由 Go 实现")
	})
	mux.Handle("/legacy", http.RedirectHandler("/legacy/", http.StatusTemporaryRedirect))
	// The old workbench is static HTML/JS; serve it from Go rather than the Python app.
	mux.Handle("/legacy/", http.StripPrefix("/legacy/", legacyStatic))
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		path := filepath.Clean(filepath.FromSlash(strings.TrimPrefix(r.URL.Path, "/")))
		if path == "." || strings.HasSuffix(r.URL.Path, "/") {
			path = filepath.Join(path, "index.html")
		}
		candidate := filepath.Join(clientDir, path)
		if info, statErr := os.Stat(candidate); statErr != nil || info.IsDir() {
			http.ServeFile(w, r, filepath.Join(clientDir, "index.html"))
			return
		}
		static.ServeHTTP(w, r)
	})

	addr := os.Getenv("SHENGYING_ADDR")
	if addr == "" {
		addr = "127.0.0.1:8317"
	}
	server := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	log.Printf("ShengYing React + Go listening at http://%s (model worker: %s)", addr, target)
	log.Fatal(server.ListenAndServe())
}
