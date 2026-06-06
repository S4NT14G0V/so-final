//! Benchmark Backend – Rust (Axum + Tokio)
//! =========================================
//! Réplica fiel del servidor Python/FastAPI y C/libmicrohttpd de hashing
//! intensivo (SHA-256 encadenado), con los mismos dos modos:
//!
//!   POST /hash-seq   → una petición a la vez (Mutex global)
//!   POST /hash-conc  → paralelo (threadpool de Rayon / spawn_blocking)
//!   GET  /health     → health check
//!
//! Cuerpo JSON esperado:
//!   { "text": "cadena", "iterations": 10000 }
//!
//! Compilar y ejecutar:
//!   cargo build --release
//!   ./target/release/hash_benchmark
//!
//! O en modo desarrollo:
//!   cargo run

use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use digest::Digest;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use tokio::sync::Mutex;

// ---------------------------------------------------------------------------
// Configuración
// ---------------------------------------------------------------------------

const PORT: u16 = 8003;
const MAX_ITERATIONS: u32 = 500_000;
const MIN_ITERATIONS: u32 = 1;
const DEFAULT_ITERATIONS: u32 = 10_000;

// ---------------------------------------------------------------------------
// Estado compartido
// ---------------------------------------------------------------------------

/// Equivalente al `asyncio.Lock()` / `pthread_mutex_t` de Python y C.
/// El Mutex de Tokio es async-aware: no bloquea hilos del OS, sólo tareas.
#[derive(Clone)]
struct AppState {
    hash_lock:        Arc<Mutex<()>>,
    stringproc_lock:  Arc<Mutex<()>>,
    prime_lock:       Arc<Mutex<()>>,
    jsonproc_lock:    Arc<Mutex<()>>,
}

// ---------------------------------------------------------------------------
// Esquemas de entrada / salida  (equivalente a Pydantic BaseModel)
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
struct HashRequest {
    text:       String,
    #[serde(default = "default_iterations")]
    iterations: u32,
}

fn default_iterations() -> u32 {
    DEFAULT_ITERATIONS
}

#[derive(Serialize)]
struct HashResponse {
    mode:       &'static str,
    iterations: u32,
    hash:       String,
}

#[derive(Deserialize)]
struct StringProcRequest {
    text: String,
}

#[derive(Serialize)]
struct StringProcResponse {
    mode:        &'static str,
    original:    String,
    reversed:    String,
    uppercase:   String,
    vowel_count: usize,
}

#[derive(Deserialize)]
struct PrimeRequest {
    #[serde(default = "default_limit")]
    limit: u32,
}

fn default_limit() -> u32 {
    10_000_000
}

#[derive(Serialize)]
struct PrimeResponse {
    mode:          &'static str,
    limit:         u32,
    prime_count:   usize,
    largest_prime: u32,
}

#[derive(Deserialize)]
struct JsonProcRequest {
    #[serde(default = "default_count")]
    count:  u32,
    #[serde(default = "default_nested")]
    nested: u32,
}

fn default_count() -> u32 {
    5000
}

fn default_nested() -> u32 {
    10
}

#[derive(Serialize)]
struct JsonProcResponse {
    mode:             &'static str,
    serialized_bytes: usize,
    checksum:         String,
}

// ---------------------------------------------------------------------------
// Lógica de cómputo  (CPU-bound puro)
// ---------------------------------------------------------------------------

/// Aplica SHA-256 de forma encadenada `iterations` veces.
/// Equivalente exacto de `compute_hash()` en Python y C.
///
/// Esta función es síncrona y bloqueante; se debe llamar siempre desde
/// `tokio::task::spawn_blocking` para no bloquear el runtime async.
fn compute_hash(text: &str, iterations: u32) -> String {
    // Primera ronda: texto → bytes
    let mut digest = Sha256::digest(text.as_bytes());

    // Rondas restantes
    for _ in 1..iterations {
        digest = Sha256::digest(&digest);
    }

    // Convertir a hex string
    format!("{:x}", digest)
}

fn compute_string_proc(text: &str) -> (String, String, usize) {
    let reversed: String = text.chars().rev().collect();
    let uppercase = text.to_uppercase();
    let vowel_count = text
        .to_lowercase()
        .chars()
        .filter(|c| matches!(c, 'a' | 'e' | 'i' | 'o' | 'u'))
        .count();
    (reversed, uppercase, vowel_count)
}

fn compute_prime(limit: u32) -> (usize, u32) {
    let limit_usize = limit as usize;
    let mut sieve = vec![true; limit_usize + 1];
    sieve[0] = false;
    sieve[1] = false;
    for i in 2..=((limit_usize as f64).sqrt() as usize) {
        if sieve[i] {
            let mut j = i * i;
            while j <= limit_usize {
                sieve[j] = false;
                j += i;
            }
        }
    }
    let prime_count = sieve.iter().filter(|&&x| x).count();
    let largest_prime = (2..=limit_usize)
        .rev()
        .find(|&i| sieve[i])
        .unwrap_or(0) as u32;
    (prime_count, largest_prime)
}

fn compute_json_proc(count: u32, nested: u32) -> (usize, String) {
    let mut root: Vec<serde_json::Value> = Vec::with_capacity(count as usize);
    for i in 0..count {
        let mut inner: Option<serde_json::Value> = None;
        for j in (0..nested).rev() {
            let mut obj = serde_json::Map::new();
            obj.insert(
                "id".to_string(),
                serde_json::json!(format!("item_{}_{}", i, j)),
            );
            obj.insert(
                "value".to_string(),
                serde_json::json!(i * nested + j),
            );
            if let Some(prev) = inner.take() {
                obj.insert("inner".to_string(), prev);
            }
            inner = Some(serde_json::Value::Object(obj));
        }
        let mut item = serde_json::Map::new();
        item.insert(
            "outer".to_string(),
            serde_json::json!(format!("item_{}", i)),
        );
        if let Some(prev) = inner.take() {
            item.insert("inner".to_string(), prev);
        }
        root.push(serde_json::Value::Object(item));
    }

    let json_str = serde_json::to_string(&root).unwrap();
    let size = json_str.len();

    let _parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
    let checksum = format!("{:x}", Sha256::digest(json_str.as_bytes()));

    (size, checksum)
}

// ---------------------------------------------------------------------------
// Validación de la request
// ---------------------------------------------------------------------------

fn validate(req: &HashRequest) -> Result<(), String> {
    if req.text.is_empty() {
        return Err("Campo 'text' requerido y no vacío".into());
    }
    if req.iterations < MIN_ITERATIONS || req.iterations > MAX_ITERATIONS {
        return Err(format!(
            "iterations debe estar entre {} y {}",
            MIN_ITERATIONS, MAX_ITERATIONS
        ));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

/// POST /hash-seq
///
/// Adquiere el Mutex global antes de despachar el cómputo al threadpool.
/// Sólo una tarea puede estar dentro del bloqueo a la vez, replicando
/// el `asyncio.Lock()` de Python y el `pthread_mutex_t` del servidor C.
async fn hash_sequential(
    State(state): State<AppState>,
    Json(body): Json<HashRequest>,
) -> impl IntoResponse {
    if let Err(e) = validate(&body) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({ "error": e })),
        );
    }

    let text       = body.text.clone();
    let iterations = body.iterations;

    // Adquirir el lock ANTES de despachar al threadpool
    let _guard = state.hash_lock.lock().await;

    // spawn_blocking: mueve el cómputo CPU-bound fuera del runtime async,
    // equivalente a `loop.run_in_executor(None, ...)` en Python
    let hash = tokio::task::spawn_blocking(move || compute_hash(&text, iterations))
        .await
        .expect("spawn_blocking falló");

    (
        StatusCode::OK,
        Json(serde_json::json!(HashResponse {
            mode: "sequential",
            iterations,
            hash,
        })),
    )
}

/// POST /hash-conc
///
/// Sin ningún lock: cada petición despacha al threadpool de forma
/// independiente, permitiendo paralelismo real acotado por los núcleos
/// del sistema. Equivalente al endpoint `/hash-conc` de Python y C.
async fn hash_concurrent(Json(body): Json<HashRequest>) -> impl IntoResponse {
    if let Err(e) = validate(&body) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({ "error": e })),
        );
    }

    let text       = body.text.clone();
    let iterations = body.iterations;

    let hash = tokio::task::spawn_blocking(move || compute_hash(&text, iterations))
        .await
        .expect("spawn_blocking falló");

    (
        StatusCode::OK,
        Json(serde_json::json!(HashResponse {
            mode: "concurrent",
            iterations,
            hash,
        })),
    )
}

async fn stringproc_sequential(
    State(state): State<AppState>,
    Json(body): Json<StringProcRequest>,
) -> impl IntoResponse {
    if body.text.is_empty() {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "Campo 'text' requerido y no vacio"})),
        );
    }
    let text = body.text.clone();
    let text2 = text.clone();
    let _guard = state.stringproc_lock.lock().await;
    let (rev, up, vc) =
        tokio::task::spawn_blocking(move || compute_string_proc(&text2))
            .await
            .expect("spawn_blocking fallo");
    (
        StatusCode::OK,
        Json(serde_json::json!(StringProcResponse {
            mode: "sequential",
            original: text,
            reversed: rev,
            uppercase: up,
            vowel_count: vc,
        })),
    )
}

async fn stringproc_concurrent(
    Json(body): Json<StringProcRequest>,
) -> impl IntoResponse {
    if body.text.is_empty() {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "Campo 'text' requerido y no vacio"})),
        );
    }
    let text = body.text.clone();
    let text2 = text.clone();
    let (rev, up, vc) =
        tokio::task::spawn_blocking(move || compute_string_proc(&text2))
            .await
            .expect("spawn_blocking fallo");
    (
        StatusCode::OK,
        Json(serde_json::json!(StringProcResponse {
            mode: "concurrent",
            original: text,
            reversed: rev,
            uppercase: up,
            vowel_count: vc,
        })),
    )
}

async fn prime_sequential(
    State(state): State<AppState>,
    Json(body): Json<PrimeRequest>,
) -> impl IntoResponse {
    if body.limit < 2 || body.limit > 100_000_000 {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "limit debe estar entre 2 y 100000000"})),
        );
    }
    let limit = body.limit;
    let _guard = state.prime_lock.lock().await;
    let (pc, lp) = tokio::task::spawn_blocking(move || compute_prime(limit))
        .await
        .expect("spawn_blocking fallo");
    (
        StatusCode::OK,
        Json(serde_json::json!(PrimeResponse {
            mode: "sequential",
            limit,
            prime_count: pc,
            largest_prime: lp,
        })),
    )
}

async fn prime_concurrent(Json(body): Json<PrimeRequest>) -> impl IntoResponse {
    if body.limit < 2 || body.limit > 100_000_000 {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "limit debe estar entre 2 y 100000000"})),
        );
    }
    let limit = body.limit;
    let (pc, lp) = tokio::task::spawn_blocking(move || compute_prime(limit))
        .await
        .expect("spawn_blocking fallo");
    (
        StatusCode::OK,
        Json(serde_json::json!(PrimeResponse {
            mode: "concurrent",
            limit,
            prime_count: pc,
            largest_prime: lp,
        })),
    )
}

async fn jsonproc_sequential(
    State(state): State<AppState>,
    Json(body): Json<JsonProcRequest>,
) -> impl IntoResponse {
    if body.count < 1 || body.count > 50_000 {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "count debe estar entre 1 y 50000"})),
        );
    }
    if body.nested > 50 {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "nested debe estar entre 0 y 50"})),
        );
    }
    let count = body.count;
    let nested = body.nested;
    let _guard = state.jsonproc_lock.lock().await;
    let (size, checksum) =
        tokio::task::spawn_blocking(move || compute_json_proc(count, nested))
            .await
            .expect("spawn_blocking fallo");
    (
        StatusCode::OK,
        Json(serde_json::json!(JsonProcResponse {
            mode: "sequential",
            serialized_bytes: size,
            checksum,
        })),
    )
}

async fn jsonproc_concurrent(Json(body): Json<JsonProcRequest>) -> impl IntoResponse {
    if body.count < 1 || body.count > 50_000 {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "count debe estar entre 1 y 50000"})),
        );
    }
    if body.nested > 50 {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "nested debe estar entre 0 y 50"})),
        );
    }
    let count = body.count;
    let nested = body.nested;
    let (size, checksum) =
        tokio::task::spawn_blocking(move || compute_json_proc(count, nested))
            .await
            .expect("spawn_blocking fallo");
    (
        StatusCode::OK,
        Json(serde_json::json!(JsonProcResponse {
            mode: "concurrent",
            serialized_bytes: size,
            checksum,
        })),
    )
}

/// GET /health
async fn health() -> impl IntoResponse {
    Json(serde_json::json!({
        "status":   "ok",
        "language": "rust",
    }))
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

#[tokio::main]
async fn main() {
    // Inicializar logging (RUST_LOG=info para ver trazas)
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let state = AppState {
        hash_lock:        Arc::new(Mutex::new(())),
        stringproc_lock:  Arc::new(Mutex::new(())),
        prime_lock:       Arc::new(Mutex::new(())),
        jsonproc_lock:    Arc::new(Mutex::new(())),
    };

    let app = Router::new()
        .route("/hash-seq",        post(hash_sequential))
        .route("/hash-conc",       post(hash_concurrent))
        .route("/stringproc-seq",  post(stringproc_sequential))
        .route("/stringproc-conc", post(stringproc_concurrent))
        .route("/prime-seq",       post(prime_sequential))
        .route("/prime-conc",      post(prime_concurrent))
        .route("/jsonproc-seq",    post(jsonproc_sequential))
        .route("/jsonproc-conc",   post(jsonproc_concurrent))
        .route("/health",          get(health))
        .with_state(state);

    let addr = format!("0.0.0.0:{PORT}");
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .expect("No se pudo abrir el puerto");

    println!("Hashing Benchmark – Rust");
    println!("Escuchando en http://{addr}");
    println!("  POST /hash-seq   (secuencial)");
    println!("  POST /hash-conc  (concurrente)");
    println!("  GET  /health");
    println!("Ctrl+C para detener.\n");

    axum::serve(listener, app)
        .await
        .expect("Error en el servidor");
}