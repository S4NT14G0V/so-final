
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
`k6 (Carga)` → `Backend HTTP` → `Procesamiento del algoritmo` → `Respuesta JSON`

### Algoritmos implementados
| Algoritmo | Tipo de carga | Descripcion |
|---|---|---|
| `hash` | CPU | SHA-256 iterativo (KDF-like) |
| `stringproc` | Trivial | Transformacion de strings (mide overhead del framework) |
| `prime` | CPU pesado | Criba de Eratostenes |
| `jsonproc` | I/O simulado | Construccion/serializacion/parseo de JSON masivo |

Cada algoritmo tiene dos modos: `-seq` (secuencial, con lock) y `-conc` (concurrente, sin lock).

### Endpoints
| Endpoint | Descripcion |
| :--- | :--- |
| `/hash-seq` / `/hash-conc` | Hashing SHA-256 iterativo |
| `/stringproc-seq` / `/stringproc-conc` | Transformacion de strings |
| `/prime-seq` / `/prime-conc` | Criba de Eratostenes |
| `/jsonproc-seq` / `/jsonproc-conc` | Procesamiento JSON masivo |
| `/health` | Health check del servidor |

**Requests de ejemplo:**
```json
// hash
{ "text": "benchmark", "iterations": 100000 }

// stringproc
{ "text": "The quick brown fox jumps over the lazy dog" }

// prime
{ "limit": 10000000 }

// jsonproc
{ "count": 5000, "nested": 10 }
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
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
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

---

## Ejecutar Benchmarks

El runner automatico (`benchmark/runner.py`) orquesta todo el experimento:

```bash
pip install pyyaml          # dependencia unica del runner
cd benchmark
python runner.py            # ejecucion completa
python runner.py --dry-run  # previsualizar sin ejecutar
```

### Que hace el runner:

1. **Compila** cada lenguaje (C, Rust) si es necesario
2. **Inicia** el servidor de cada lenguaje
3. **Aleatoriza** el orden de experimentos usando la semilla configurada
4. **Ejecuta k6** para cada combinacion (lenguaje x algoritmo x escenario x replica)
5. **Genera** un CSV en `benchmark/results/` listo para ANOVA

### ANOVA

El CSV generado se carga directamente en R:

```r
df <- read.csv("benchmark/results/benchmark_YYYYMMDD_HHMMSS.csv")

# ANOVA por algoritmo (cada algoritmo se analiza por separado)
hash <- subset(df, algorithm == "hash")
anova_hash <- aov(avg_latency_ms ~ language * scenario, data = hash)
summary(anova_hash)
TukeyHSD(anova_hash, "language")
```

Repite para `stringproc`, `prime` y `jsonproc`.

### Ejecucion manual de un solo backend (opcional)

```bash
cd benchmark
# Ejemplos para cada algoritmo:
k6 run -e BASE_URL=http://127.0.0.1:8000 -e ENDPOINT=/hash-seq       -e PAYLOAD='{"text":"test","iterations":10000}'      --duration 30s --vus 10 benchmark.js
k6 run -e BASE_URL=http://127.0.0.1:8000 -e ENDPOINT=/stringproc-seq -e PAYLOAD='{"text":"hello world"}'                  --duration 30s --vus 10 benchmark.js
k6 run -e BASE_URL=http://127.0.0.1:8000 -e ENDPOINT=/prime-seq      -e PAYLOAD='{"limit":10000000}'                      --duration 30s --vus 10 benchmark.js
k6 run -e BASE_URL=http://127.0.0.1:8000 -e ENDPOINT=/jsonproc-seq   -e PAYLOAD='{"count":5000,"nested":10}'             --duration 30s --vus 10 benchmark.js
```

### Configuracion del experimento

Edita `benchmark/config.yaml` para ajustar replicas, semilla, carga de k6, y agregar nuevos algoritmos en el futuro.
```

