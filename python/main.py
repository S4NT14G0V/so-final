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
_hash_lock = asyncio.Lock()
_stringproc_lock = asyncio.Lock()
_prime_lock = asyncio.Lock()
_jsonproc_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Esquema de entrada
# ---------------------------------------------------------------------------

class HashRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Texto base para hashear")
    iterations: int = Field(
        default=10_000,
        ge=1,
        le=500_000,
        description="Numero de iteraciones SHA-256 encadenadas",
    )


class StringProcRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Texto a procesar")


class PrimeRequest(BaseModel):
    limit: int = Field(
        default=10_000_000,
        ge=2,
        le=100_000_000,
        description="Limite maximo para la criba de Eratostenes",
    )


class JsonProcRequest(BaseModel):
    count: int = Field(
        default=5000,
        ge=1,
        le=50_000,
        description="Numero de elementos en el array raiz",
    )
    nested: int = Field(
        default=10,
        ge=0,
        le=50,
        description="Niveles de anidamiento por elemento",
    )


# ---------------------------------------------------------------------------
# Lógica de cómputo (CPU-bound puro, se ejecuta en hilo del threadpool)
# ---------------------------------------------------------------------------

def compute_hash(text: str, iterations: int) -> str:
    """
    Aplica SHA-256 de forma encadenada `iterations` veces.
    El resultado de cada ronda es la entrada de la siguiente,
    simulando derivacion de claves (KDF-like).
    """
    data = text.encode("utf-8")
    for _ in range(iterations):
        data = hashlib.sha256(data).digest()
    return data.hex()


def compute_stringproc(text: str) -> dict:
    reversed_text = text[::-1]
    upper_text = text.upper()
    vowel_count = sum(1 for c in text.lower() if c in "aeiou")
    return {
        "original": text,
        "reversed": reversed_text,
        "uppercase": upper_text,
        "vowel_count": vowel_count,
    }


def compute_prime(limit: int) -> dict:
    sieve = bytearray(b"\x01") * (limit + 1)
    sieve[0:2] = b"\x00\x00"
    for i in range(2, int(limit ** 0.5) + 1):
        if sieve[i]:
            step = i
            start = i * i
            sieve[start:limit + 1:step] = b"\x00" * ((limit - start) // step + 1)
    prime_count = sum(sieve)
    largest = limit
    while largest >= 2 and not sieve[largest]:
        largest -= 1
    return {
        "limit": limit,
        "prime_count": prime_count,
        "largest_prime": largest,
    }


def compute_jsonproc(count: int, nested: int) -> dict:
    import json as json_mod
    root = []
    for i in range(count):
        inner = None
        for j in range(nested - 1, -1, -1):
            obj = {
                "id": f"item_{i}_{j}",
                "value": i * nested + j,
            }
            if inner is not None:
                obj["inner"] = inner
            inner = obj
        item = {"outer": f"item_{i}"}
        if inner is not None:
            item["inner"] = inner
        root.append(item)
    json_str = json_mod.dumps(root, separators=(",", ":"))
    serialized_size = len(json_str)
    _parsed = json_mod.loads(json_str)
    checksum = hashlib.sha256(json_str.encode()).hexdigest()
    return {
        "serialized_bytes": serialized_size,
        "checksum": checksum,
    }


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
    async with _hash_lock:
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
# String Processing  (algoritmo simple - overhead del framework)
# ---------------------------------------------------------------------------


@app.post("/stringproc-seq", summary="String proc secuencial")
async def stringproc_sequential(body: StringProcRequest):
    async with _stringproc_lock:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, compute_stringproc, body.text
        )
    result["mode"] = "sequential"
    return JSONResponse(content=result)


@app.post("/stringproc-conc", summary="String proc concurrente")
async def stringproc_concurrent(body: StringProcRequest):
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, compute_stringproc, body.text
    )
    result["mode"] = "concurrent"
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Prime Sieve  (algoritmo CPU-bound - criba de Eratostenes)
# ---------------------------------------------------------------------------


@app.post("/prime-seq", summary="Criba de primos secuencial")
async def prime_sequential(body: PrimeRequest):
    async with _prime_lock:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, compute_prime, body.limit
        )
    result["mode"] = "sequential"
    return JSONResponse(content=result)


@app.post("/prime-conc", summary="Criba de primos concurrente")
async def prime_concurrent(body: PrimeRequest):
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, compute_prime, body.limit
    )
    result["mode"] = "concurrent"
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# JSON Processing  (algoritmo I/O simulado - serializacion masiva)
# ---------------------------------------------------------------------------


@app.post("/jsonproc-seq", summary="JSON proc secuencial")
async def jsonproc_sequential(body: JsonProcRequest):
    async with _jsonproc_lock:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, compute_jsonproc, body.count, body.nested
        )
    result["mode"] = "sequential"
    return JSONResponse(content=result)


@app.post("/jsonproc-conc", summary="JSON proc concurrente")
async def jsonproc_concurrent(body: JsonProcRequest):
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, compute_jsonproc, body.count, body.nested
    )
    result["mode"] = "concurrent"
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Health check (util para validar que el servidor esta vivo antes de k6)
# ---------------------------------------------------------------------------
# Health check (útil para validar que el servidor está vivo antes de k6)
# ---------------------------------------------------------------------------

@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok", "language": "python"}