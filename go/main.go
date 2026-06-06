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
	"fmt"
	"log"
	"net/http"
	"strings"
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

// errorResponse para errores de validacion.
type errorResponse struct {
	Error string `json:"error"`
}

// stringProcRequest / stringProcResponse
type stringProcRequest struct {
	Text string `json:"text"`
}

type stringProcResponse struct {
	Mode       string `json:"mode"`
	Original   string `json:"original"`
	Reversed   string `json:"reversed"`
	Uppercase  string `json:"uppercase"`
	VowelCount int    `json:"vowel_count"`
}

// primeRequest / primeResponse
type primeRequest struct {
	Limit int `json:"limit"`
}

type primeResponse struct {
	Mode         string `json:"mode"`
	Limit        int    `json:"limit"`
	PrimeCount   int    `json:"prime_count"`
	LargestPrime int    `json:"largest_prime"`
}

// jsonProcRequest / jsonProcResponse
type jsonProcRequest struct {
	Count  int `json:"count"`
	Nested int `json:"nested"`
}

type jsonProcResponse struct {
	Mode            string `json:"mode"`
	SerializedBytes int64  `json:"serialized_bytes"`
	Checksum        string `json:"checksum"`
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

func computeStringProc(text string) (string, string, int) {
	runes := []rune(text)
	for i, j := 0, len(runes)-1; i < j; i, j = i+1, j-1 {
		runes[i], runes[j] = runes[j], runes[i]
	}
	reversed := string(runes)
	upper := strings.ToUpper(text)
	vowels := 0
	for _, c := range strings.ToLower(text) {
		if c == 'a' || c == 'e' || c == 'i' || c == 'o' || c == 'u' {
			vowels++
		}
	}
	return reversed, upper, vowels
}

func computePrime(limit int) (int, int) {
	sieve := make([]bool, limit+1)
	for i := 2; i <= limit; i++ {
		sieve[i] = true
	}
	for i := 2; i*i <= limit; i++ {
		if sieve[i] {
			for j := i * i; j <= limit; j += i {
				sieve[j] = false
			}
		}
	}
	count := 0
	largest := 0
	for i := 2; i <= limit; i++ {
		if sieve[i] {
			count++
			largest = i
		}
	}
	return count, largest
}

func computeJsonProc(count, nested int) (int64, string) {
	root := make([]map[string]interface{}, count)
	for i := 0; i < count; i++ {
		var inner map[string]interface{}
		for j := nested - 1; j >= 0; j-- {
			obj := map[string]interface{}{
				"id":    fmt.Sprintf("item_%d_%d", i, j),
				"value": i*nested + j,
			}
			if inner != nil {
				obj["inner"] = inner
			}
			inner = obj
		}
		item := map[string]interface{}{"outer": fmt.Sprintf("item_%d", i)}
		if inner != nil {
			item["inner"] = inner
		}
		root[i] = item
	}

	jsonBytes, _ := json.Marshal(root)
	size := int64(len(jsonBytes))

	var parsed interface{}
	json.Unmarshal(jsonBytes, &parsed)

	sum := sha256.Sum256(jsonBytes)
	checksum := hex.EncodeToString(sum[:])

	return size, checksum
}

// ─── Estado global ───────────────────────────────────────────────────────────

// hashMutex serializa las peticiones en /hash-seq, igual que asyncio.Lock() en Python.
var hashMutex sync.Mutex
var stringProcMutex sync.Mutex
var primeMutex sync.Mutex
var jsonProcMutex sync.Mutex

// ─── Handlers ────────────────────────────────────────────────────────────────

// writeJSON escribe una respuesta JSON con el status code indicado.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

// parseRequest decodifica el cuerpo JSON y valida los campos.
// Retorna false si hubo error (la respuesta de error ya fue enviada).
func parseHashRequest(w http.ResponseWriter, r *http.Request) (hashRequest, bool) {
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
	req, ok := parseHashRequest(w, r)
	if !ok {
		return
	}

	// Lock global: serializa el computo igual que asyncio.Lock en Python.
	hashMutex.Lock()
	result := computeHash(req.Text, req.Iterations)
	hashMutex.Unlock()

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
	req, ok := parseHashRequest(w, r)
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

func stringProcSeqHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errorResponse{"method not allowed"})
		return
	}
	var req stringProcRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, errorResponse{"invalid JSON"})
		return
	}
	if req.Text == "" {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"text must not be empty"})
		return
	}
	stringProcMutex.Lock()
	rev, up, vc := computeStringProc(req.Text)
	stringProcMutex.Unlock()
	writeJSON(w, http.StatusOK, stringProcResponse{
		Mode: "sequential", Original: req.Text, Reversed: rev, Uppercase: up, VowelCount: vc,
	})
}

func stringProcConcHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errorResponse{"method not allowed"})
		return
	}
	var req stringProcRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, errorResponse{"invalid JSON"})
		return
	}
	if req.Text == "" {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"text must not be empty"})
		return
	}
	rev, up, vc := computeStringProc(req.Text)
	writeJSON(w, http.StatusOK, stringProcResponse{
		Mode: "concurrent", Original: req.Text, Reversed: rev, Uppercase: up, VowelCount: vc,
	})
}

func primeSeqHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errorResponse{"method not allowed"})
		return
	}
	var req primeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, errorResponse{"invalid JSON"})
		return
	}
	if req.Limit == 0 {
		req.Limit = 10_000_000
	}
	if req.Limit < 2 || req.Limit > 100_000_000 {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"limit must be between 2 and 100000000"})
		return
	}
	primeMutex.Lock()
	count, largest := computePrime(req.Limit)
	primeMutex.Unlock()
	writeJSON(w, http.StatusOK, primeResponse{
		Mode: "sequential", Limit: req.Limit, PrimeCount: count, LargestPrime: largest,
	})
}

func primeConcHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errorResponse{"method not allowed"})
		return
	}
	var req primeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, errorResponse{"invalid JSON"})
		return
	}
	if req.Limit == 0 {
		req.Limit = 10_000_000
	}
	if req.Limit < 2 || req.Limit > 100_000_000 {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"limit must be between 2 and 100000000"})
		return
	}
	count, largest := computePrime(req.Limit)
	writeJSON(w, http.StatusOK, primeResponse{
		Mode: "concurrent", Limit: req.Limit, PrimeCount: count, LargestPrime: largest,
	})
}

func jsonProcSeqHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errorResponse{"method not allowed"})
		return
	}
	var req jsonProcRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, errorResponse{"invalid JSON"})
		return
	}
	if req.Count == 0 {
		req.Count = 5000
	}
	if req.Count < 1 || req.Count > 50_000 {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"count must be between 1 and 50000"})
		return
	}
	if req.Nested < 0 || req.Nested > 50 {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"nested must be between 0 and 50"})
		return
	}
	jsonProcMutex.Lock()
	size, checksum := computeJsonProc(req.Count, req.Nested)
	jsonProcMutex.Unlock()
	writeJSON(w, http.StatusOK, jsonProcResponse{
		Mode: "sequential", SerializedBytes: size, Checksum: checksum,
	})
}

func jsonProcConcHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, errorResponse{"method not allowed"})
		return
	}
	var req jsonProcRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, errorResponse{"invalid JSON"})
		return
	}
	if req.Count == 0 {
		req.Count = 5000
	}
	if req.Count < 1 || req.Count > 50_000 {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"count must be between 1 and 50000"})
		return
	}
	if req.Nested < 0 || req.Nested > 50 {
		writeJSON(w, http.StatusUnprocessableEntity, errorResponse{"nested must be between 0 and 50"})
		return
	}
	size, checksum := computeJsonProc(req.Count, req.Nested)
	writeJSON(w, http.StatusOK, jsonProcResponse{
		Mode: "concurrent", SerializedBytes: size, Checksum: checksum,
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
	mux.HandleFunc("/hash-seq",        hashSeqHandler)
	mux.HandleFunc("/hash-conc",       hashConcHandler)
	mux.HandleFunc("/stringproc-seq",  stringProcSeqHandler)
	mux.HandleFunc("/stringproc-conc", stringProcConcHandler)
	mux.HandleFunc("/prime-seq",       primeSeqHandler)
	mux.HandleFunc("/prime-conc",      primeConcHandler)
	mux.HandleFunc("/jsonproc-seq",    jsonProcSeqHandler)
	mux.HandleFunc("/jsonproc-conc",   jsonProcConcHandler)
	mux.HandleFunc("/health",          healthHandler)

	addr := "0.0.0.0:" + *port
	log.Printf("🚀 Go benchmark server escuchando en http://%s", addr)
	log.Printf("   Endpoints: POST /hash-seq | POST /hash-conc | GET /health")

	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatalf("Error arrancando servidor: %v", err)
	}
}