"""
Benchmark Backend - Python (FastAPI)
Tarea: Hashing intensivo (SHA-256 iterativo)
Simula escenarios reales de autenticación / derivación de claves (similar a PBKDF2).

Endpoints:
  POST /hash-seq   → procesa una solicitud a la vez (lock global)
  POST /hash-conc  → procesa solicitudes en paralelo (threadpool)

Cuerpo JSON esperado:
  {
    "text": "cualquier cadena de texto",
    "iterations": 10000   (cuántas veces se aplica SHA-256 en cadena)
  }
"""

import asyncio
import hashlib

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Hashing Benchmark – Python",
    description="Benchmark de hashing intensivo: secuencial vs concurrente",
    version="1.0.0",
)

# Lock global que fuerza atención de una sola petición a la vez en /hash-seq
_sequential_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Esquema de entrada
# ---------------------------------------------------------------------------

class HashRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Texto base para hashear")
    iterations: int = Field(
        default=10_000,
        ge=1,
        le=500_000,
        description="Número de iteraciones SHA-256 encadenadas",
    )


# ---------------------------------------------------------------------------
# Lógica de cómputo (CPU-bound puro, se ejecuta en hilo del threadpool)
# ---------------------------------------------------------------------------

def compute_hash(text: str, iterations: int) -> str:
    """
    Aplica SHA-256 de forma encadenada `iterations` veces.
    El resultado de cada ronda es la entrada de la siguiente,
    simulando derivación de claves (KDF-like).
    """
    data = text.encode("utf-8")
    for _ in range(iterations):
        data = hashlib.sha256(data).digest()
    return data.hex()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/hash-seq", summary="Hashing secuencial (una petición a la vez)")
async def hash_sequential(body: HashRequest):
    """
    Usa un asyncio.Lock para garantizar que sólo una petición
    ejecute el cómputo a la vez, sin importar la concurrencia del cliente.
    Esto replica un servidor de un solo hilo bloqueante.
    """
    async with _sequential_lock:
        # run_in_executor para no bloquear el event loop de asyncio
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, compute_hash, body.text, body.iterations
        )

    return JSONResponse(
        content={
            "mode": "sequential",
            "iterations": body.iterations,
            "hash": result,
        }
    )


@app.post("/hash-conc", summary="Hashing concurrente (threadpool)")
async def hash_concurrent(body: HashRequest):
    """
    Despacha el cómputo al threadpool de asyncio sin ningún lock,
    permitiendo que múltiples peticiones corran en paralelo
    (limitado por los workers del servidor y los núcleos del sistema).
    """
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, compute_hash, body.text, body.iterations
    )

    return JSONResponse(
        content={
            "mode": "concurrent",
            "iterations": body.iterations,
            "hash": result,
        }
    )


# ---------------------------------------------------------------------------
# Health check (útil para validar que el servidor está vivo antes de k6)
# ---------------------------------------------------------------------------

@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok", "language": "python"}