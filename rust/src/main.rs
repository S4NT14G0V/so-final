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
    sequential_lock: Arc<Mutex<()>>,
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

#[derive(Serialize)]
struct ErrorResponse {
    error: String,
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
    let _guard = state.sequential_lock.lock().await;

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
        sequential_lock: Arc::new(Mutex::new(())),
    };

    let app = Router::new()
        .route("/hash-seq",  post(hash_sequential))
        .route("/hash-conc", post(hash_concurrent))
        .route("/health",    get(health))
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