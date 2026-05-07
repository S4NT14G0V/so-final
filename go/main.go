// Benchmark Backend — Go (net/http + goroutines)
//
// Equivalente exacto del main.py en Python.
// Misma lógica, mismos endpoints, misma respuesta JSON.
//
// Endpoints:
//
//	POST /hash-seq   → mutex global, una solicitud a la vez
//	POST /hash-conc  → goroutines sin lock, concurrencia real
//	GET  /health     → health check
//
// Cuerpo JSON esperado:
//
//	{ "text": "...", "iterations": 10000 }
//
// Ejecutar:
//
//	go run main.go
//	go run main.go -port 8001          (puerto distinto al de Python)
//	go run main.go -port 8001 -workers 8
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"log"
	"net/http"
	"sync"
)

// ─── Estructuras JSON ────────────────────────────────────────────────────────

// hashRequest es el cuerpo de entrada — idéntico al HashRequest de Pydantic.
type hashRequest struct {
	Text       string `json:"text"`
	Iterations int    `json:"iterations"`
}

// hashResponse es la respuesta — idéntica a la del endpoint Python.
type hashResponse struct {
	Mode       string `json:"mode"`
	Iterations int    `json:"iterations"`
	Hash       string `json:"hash"`
}

// errorResponse para errores de validación.
type errorResponse struct {
	Error string `json:"error"`
}

// ─── Lógica de cómputo ───────────────────────────────────────────────────────

// computeHash aplica SHA-256 de forma encadenada `iterations` veces.
// Replica exactamente la función compute_hash de Python:
//
//	data = text.encode("utf-8")
//	for _ in range(iterations):
//	    data = hashlib.sha256(data).digest()
//	return data.hex()
func computeHash(text string, iterations int) string {
	data := []byte(text)
	for i := 0; i < iterations; i++ {
		sum := sha256.Sum256(data)
		data = sum[:] // Sum256 retorna [32]byte, lo convertimos a slice
	}
	return hex.EncodeToString(data)
}

// ─── Estado global ───────────────────────────────────────────────────────────

// seqMutex serializa las peticiones en /hash-seq, igual que asyncio.Lock() en Python.
var seqMutex sync.Mutex

// ─── Handlers ────────────────────────────────────────────────────────────────

// writeJSON escribe una respuesta JSON con el status code indicado.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

// parseRequest decodifica el cuerpo JSON y valida los campos.
// Retorna false si hubo error (la respuesta de error ya fue enviada).
func parseRequest(w http.ResponseWriter, r *http.Request) (hashRequest, bool) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errorResponse{"method not allowed"})
		return hashRequest{}, false
	}

	var req hashRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, errorResponse{"invalid JSON: " + err.Error()})
		return hashRequest{}, false
	}

	// Validaciones equivalentes a los Field(...) de Pydantic
	if req.Text == "" {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"text must not be empty"})
		return hashRequest{}, false
	}
	if req.Iterations == 0 {
		req.Iterations = 10_000 // default igual que en Python
	}
	if req.Iterations < 1 || req.Iterations > 500_000 {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"iterations must be between 1 and 500000"})
		return hashRequest{}, false
	}

	return req, true
}

// hashSeqHandler — equivalente a hash_sequential en Python.
// Usa un sync.Mutex para garantizar que sólo una goroutine ejecute el cómputo
// a la vez, independientemente de cuántas conexiones haya activas.
func hashSeqHandler(w http.ResponseWriter, r *http.Request) {
	req, ok := parseRequest(w, r)
	if !ok {
		return
	}

	// Lock global: serializa el cómputo igual que asyncio.Lock en Python.
	seqMutex.Lock()
	result := computeHash(req.Text, req.Iterations)
	seqMutex.Unlock()

	writeJSON(w, http.StatusOK, hashResponse{
		Mode:       "sequential",
		Iterations: req.Iterations,
		Hash:       result,
	})
}

// hashConcHandler — equivalente a hash_concurrent en Python.
// En Go cada request ya corre en su propia goroutine (gestionada por net/http),
// así que no hace falta hacer nada especial: el runtime de Go distribuye el
// trabajo entre todos los núcleos disponibles (GOMAXPROCS).
func hashConcHandler(w http.ResponseWriter, r *http.Request) {
	req, ok := parseRequest(w, r)
	if !ok {
		return
	}

	result := computeHash(req.Text, req.Iterations)

	writeJSON(w, http.StatusOK, hashResponse{
		Mode:       "concurrent",
		Iterations: req.Iterations,
		Hash:       result,
	})
}

// healthHandler — equivalente a /health en Python.
func healthHandler(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{
		"status":   "ok",
		"language": "go",
	})
}

// ─── Main ────────────────────────────────────────────────────────────────────

func main() {
	port := flag.String("port", "8001", "Puerto en el que escucha el servidor")
	flag.Parse()

	mux := http.NewServeMux()
	mux.HandleFunc("/hash-seq",  hashSeqHandler)
	mux.HandleFunc("/hash-conc", hashConcHandler)
	mux.HandleFunc("/health",    healthHandler)

	addr := "0.0.0.0:" + *port
	log.Printf("🚀 Go benchmark server escuchando en http://%s", addr)
	log.Printf("   Endpoints: POST /hash-seq | POST /hash-conc | GET /health")

	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatalf("Error arrancando servidor: %v", err)
	}
}