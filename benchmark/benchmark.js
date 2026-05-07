import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate, Counter } from "k6/metrics";

// ─────────────────────────────────────────────
// MÉTRICAS (compartidas)
// ─────────────────────────────────────────────
const seqDuration  = new Trend("seq_request_duration", true);
const concDuration = new Trend("conc_request_duration", true);
const seqErrors    = new Rate("seq_error_rate");
const concErrors   = new Rate("conc_error_rate");
const seqRequests  = new Counter("seq_total_requests");
const concRequests = new Counter("conc_total_requests");

// ─────────────────────────────────────────────
// CONFIG MULTI-LENGUAJE
// ─────────────────────────────────────────────
const LANG = __ENV.LANG || "python";

/**
 * Puertos por defecto:
 * python → 8000
 * go     → 8001
 * c      → 8004
 * rust   → 8003
 */
const DEFAULT_PORTS = {
  python: "8000",
  go:     "8001",
  rust:   "8003",
  c:      "8004",
};

const BASE_URL =
  __ENV.BASE_URL ||
  `http://127.0.0.1:${DEFAULT_PORTS[LANG] || "8000"}`;

const ITERATIONS = parseInt(__ENV.ITERATIONS || "10000", 10);

const SEQ_VUS  = parseInt(__ENV.SEQ_VUS  || "10", 10);
const CONC_VUS = parseInt(__ENV.CONC_VUS || "10", 10);

const SEQ_DUR  = __ENV.SEQ_DURATION  || "30s";
const CONC_DUR = __ENV.CONC_DURATION || "30s";
const CONC_START = __ENV.CONC_START  || "35s";

// ─────────────────────────────────────────────
// OPTIONS
// ─────────────────────────────────────────────
export const options = {
  scenarios: {
    sequential: {
      executor: "constant-vus",
      vus: SEQ_VUS,
      duration: SEQ_DUR,
      exec: "runSequential",
      tags: { scenario: "sequential", lang: LANG },
    },
    concurrent: {
      executor: "constant-vus",
      vus: CONC_VUS,
      duration: CONC_DUR,
      startTime: CONC_START,
      exec: "runConcurrent",
      tags: { scenario: "concurrent", lang: LANG },
    },
  },

  thresholds: {
    "seq_request_duration":  ["p(95)<1000"],
    "conc_request_duration": ["p(95)<1000"],
    "seq_error_rate":  ["rate<0.01"],
    "conc_error_rate": ["rate<0.01"],
  },
};

// ─────────────────────────────────────────────
// PAYLOAD
// ─────────────────────────────────────────────
const PAYLOAD = JSON.stringify({
  text: "benchmark-hashing-so-udea-2025",
  iterations: ITERATIONS,
});

const HEADERS = {
  "Content-Type": "application/json",
};

// ─────────────────────────────────────────────
// VALIDACIÓN UNIFICADA POR BACKEND
// ─────────────────────────────────────────────
function validate(res, expectedMode) {
  try {
    const body = JSON.parse(res.body);

    const hashOk =
      typeof body.hash === "string" &&
      body.hash.length === 64;

    // Python / Go → solo hash
    if (LANG === "python" || LANG === "go") {
      return hashOk;
    }

    // C / Rust → validación estricta
    if (LANG === "c" || LANG === "rust") {
      return (
        hashOk &&
        body.mode === expectedMode &&
        body.iterations === ITERATIONS
      );
    }

    return false;
  } catch {
    return false;
  }
}

// ─────────────────────────────────────────────
// SEQUENTIAL
// ─────────────────────────────────────────────
export function runSequential() {
  const res = http.post(`${BASE_URL}/hash-seq`, PAYLOAD, {
    headers: HEADERS,
  });

  const ok = check(res, {
    "seq status 200": (r) => r.status === 200,
    "seq valid": (r) => validate(r, "sequential"),
  });

  seqDuration.add(res.timings.duration);
  seqErrors.add(!ok);
  seqRequests.add(1);

  sleep(0.05);
}

// ─────────────────────────────────────────────
// CONCURRENT
// ─────────────────────────────────────────────
export function runConcurrent() {
  const res = http.post(`${BASE_URL}/hash-conc`, PAYLOAD, {
    headers: HEADERS,
  });

  const ok = check(res, {
    "conc status 200": (r) => r.status === 200,
    "conc valid": (r) => validate(r, "concurrent"),
  });

  concDuration.add(res.timings.duration);
  concErrors.add(!ok);
  concRequests.add(1);
}

// ─────────────────────────────────────────────
// SETUP
// ─────────────────────────────────────────────
export function setup() {
  const res = http.get(`${BASE_URL}/health`);

  if (res.status !== 200) {
    throw new Error(
      `Backend ${LANG} no responde en ${BASE_URL}/health`
    );
  }

  try {
    const body = JSON.parse(res.body);
    console.log(`Backend activo: ${body.language || LANG}`);
  } catch {}

  console.log(`🚀 Benchmark LANG=${LANG} @ ${BASE_URL}`);
  console.log(`ITERATIONS=${ITERATIONS}`);
}