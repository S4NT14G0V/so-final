
# Backend Benchmark: Python vs Go vs C vs Rust

Comparación exhaustiva de rendimiento entre aplicaciones backend HTTP (secuenciales vs concurrentes) implementadas en distintos lenguajes.

## Objetivo
Analizar el impacto de la concurrencia y el tipo de lenguaje (interpretado vs compilado) en el rendimiento de un servicio backend bajo carga.

### Qué evaluamos:
* **Modelos:** Secuencial vs. Concurrente.
* **Métricas:** Throughput (RPS), Latencia y Escalabilidad.
* **Lenguajes:** Python, Go, C y Rust.

---

## Arquitectura
El flujo de procesamiento para cada request es:
`k6 (Carga)` → `Backend HTTP` → `Procesamiento CPU-heavy (SHA256 iterativo)` → `Respuesta JSON`

### Endpoints
| Endpoint | Descripción |
| :--- | :--- |
| `/hash-seq` | Procesamiento secuencial (bloqueante) |
| `/hash-conc` | Procesamiento concurrente (no bloqueante) |
| `/health` | Health check del servidor |

**Request:**
```json
{
  "text": "benchmark",
  "iterations": 100000
}
```

---

## Requisitos Previos
* **Python:** 3.11+
* **Go:** 1.22+
* **Rust:** Cargo / Rustc
* **C:** GCC
* **Herramientas:** [k6](https://k6.io/) instalado para ejecutar los benchmarks.

---

## Ejecución de Servidores

### Python
```bash
cd python
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Go
```bash
cd go
# Ejecución directa
go run main.go -port 8001
# O versión compilada (recomendado)
go build -o server_go main.go && ./server_go -port 8001
```

### C
```bash
cd c
gcc -O2 -o hash_c main.c -lmicrohttpd -lssl -lcrypto -lpthread -lcjson
./hash_c
```

### Rust
```bash
cd rust
cargo build --release
./target/release/hash_benchmark
```

**Nota:** Durante la compilación o ejecución de los binarios, es posible que aparezcan *warnings* o advertencias relacionadas con la arquitectura o librerías específicas. Estos pueden ser ignorados
## Ejecutar Benchmarks
Utiliza los scripts de k6 proporcionados para medir el rendimiento de cada lenguaje:

---

```bash
cd benchmark
k6 run -e LANG=python benchmark.js
k6 run -e LANG=go benchmark.js
k6 run -e LANG=c benchmark.js
k6 run -e LANG=rust benchmark.js
```

