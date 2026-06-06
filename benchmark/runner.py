#!/usr/bin/env python3
"""
Benchmark runner -- orquestador de experimentos multi-lenguaje
==============================================================
Ejecuta automaticamente todos los experimentos definidos en config.yaml:
  - Compila/inicia servidores de cada lenguaje
  - Ejecuta k6 con los parametros adecuados
  - Aleatoriza el orden de ejecucion para evitar sesgos
  - Genera un CSV consolidado listo para ANOVA

Uso:
  python runner.py                  # ejecucion completa
  python runner.py --dry-run        # muestra que se ejecutaria sin hacerlo
  python runner.py --config custom.yaml
"""

import argparse
import csv
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    print("Error: PyYAML es requerido. Instalalo con: pip install pyyaml")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "timestamp",
    "language",
    "algorithm",
    "scenario",
    "replica",
    "seed",
    "vus",
    "duration",
    "sample_size",
    "avg_ms",
    "min_ms",
    "max_ms",
    "p50_ms",
    "p90_ms",
    "p95_ms",
    "p99_ms",
    "error_rate",
    "throughput_rps",
]

HEALTH_TIMEOUT = 30
HEALTH_INTERVAL = 1.5
K6_TIMEOUT = 180


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_percentile(metrics_dict: Dict[str, float], p: int) -> Optional[float]:
    """Extrae un percentil del dict de metricas de k6."""
    key = f"p({p})"
    if key in metrics_dict:
        return round(metrics_dict[key], 2)
    if p == 50 and "med" in metrics_dict:
        return round(metrics_dict["med"], 2)
    return None


def _resolve_executable(cmd: List[str], workdir: Path) -> List[str]:
    """Resuelve la ruta del ejecutable en el workdir si es relativa."""
    if not cmd:
        return cmd
    exe = cmd[0]
    if exe.startswith("./") or exe.startswith(".\\"):
        resolved = (workdir / exe[2:]).resolve()
        if sys.platform == "win32" and not resolved.suffix:
            resolved = resolved.with_suffix(".exe")
        return [str(resolved)] + cmd[1:]
    return cmd


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class BenchmarkRunner:
    def __init__(self, config_path: str, dry_run: bool = False):
        self.dry_run = dry_run
        self.config = self._load_config(config_path)

        script_dir = Path(__file__).parent.resolve()
        self.project_root = script_dir.parent
        self.benchmark_js = script_dir / "benchmark.js"
        self.results_dir = script_dir / "results"
        self.tmp_dir = script_dir / ".tmp"

        self.servers: Dict[str, subprocess.Popen] = {}
        self.cancelled = False

    # -------------------------------------------------------------------
    # Carga y validacion
    # -------------------------------------------------------------------

    def _load_config(self, path: str) -> Dict[str, Any]:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No se encontro el archivo de configuracion: {path}")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _check_prerequisites(self) -> None:
        if not self.dry_run:
            if not shutil.which("k6"):
                raise RuntimeError("k6 no encontrado en el PATH. Instalalo desde https://k6.io/")

        if not self.benchmark_js.exists():
            raise FileNotFoundError(f"No se encontro el script k6: {self.benchmark_js}")

    # -------------------------------------------------------------------
    # Orquestacion principal
    # -------------------------------------------------------------------

    def run(self) -> None:
        self._check_prerequisites()
        self._create_dirs()
        self._setup_signal_handlers()

        experiments = self._build_experiment_block()
        csv_path = self.results_dir / f"benchmark_{datetime.now():%Y%m%d_%H%M%S}.csv"

        total = len(experiments)
        n_langs = len(self.config["languages"])
        n_algos = len(self.config["algorithms"])
        n_replicas = self.config["experiment"]["replicas"]

        print(f"Experimento: {n_langs} lenguajes x {n_algos} algoritmos x "
              f"2 escenarios x {n_replicas} replicas")
        print(f"Total: {total} ejecuciones de k6 (orden completamente aleatorio)")
        print(f"Semilla: {self.config['experiment']['seed']}")
        print(f"CSV: {csv_path}")
        print()

        if self.dry_run:
            self._print_dry_run(experiments)
            return

        try:
            # Fase 1: compilar todos los lenguajes
            print(f"{'='*60}")
            print("  COMPILANDO")
            print(f"{'='*60}")
            for lang in self.config["languages"]:
                if lang.get("build"):
                    self._build_language(lang)

            # Fase 2: iniciar todos los servidores
            print(f"{'='*60}")
            print("  INICIANDO SERVIDORES")
            print(f"{'='*60}")
            for lang in self.config["languages"]:
                self._start_server(lang)
                self._wait_for_health(lang["name"], lang["port"])

            # Fase 3: ejecutar experimentos (ya mezclados)
            print(f"{'='*60}")
            print(f"  EJECUTANDO {total} EXPERIMENTOS (orden completamente aleatorio)")
            print(f"{'='*60}")

            with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
                writer.writeheader()

                for idx, exp in enumerate(experiments):
                    if self.cancelled:
                        break

                    progress = f"[{idx + 1:>{len(str(total))}}/{total}]"
                    label = f"{exp['language']} | {exp['algorithm']} | {exp['scenario']} | rep {exp['replica']}"
                    print(f"  {progress} {label}  ", end="", flush=True)

                    try:
                        result = self._run_single_k6(exp["language"], exp["port"], exp)
                    except Exception as exc:
                        print(f"ERROR: {exc}")
                        continue

                    row = {
                        "timestamp": datetime.now().isoformat(),
                        "language": exp["language"],
                        "algorithm": exp["algorithm"],
                        "scenario": exp["scenario"],
                        "replica": exp["replica"],
                        "seed": self.config["experiment"]["seed"],
                        "vus": self.config["experiment"]["k6_vus"],
                        "duration": self.config["experiment"]["k6_duration"],
                        **result,
                    }
                    writer.writerow(row)
                    csvfile.flush()

                    print(f"OK  avg={result['avg_ms']}ms  p95={result['p95_ms']}ms  "
                          f"rps={result['throughput_rps']}")

        finally:
            self._stop_all_servers()

        if self.cancelled:
            print(f"\nInterrumpido. Resultados parciales en {csv_path}")
        else:
            print(f"\nCompletado. CSV: {csv_path}")


    # -------------------------------------------------------------------
    # Build
    # -------------------------------------------------------------------

    def _build_language(self, lang: Dict[str, Any]) -> None:
        workdir = self.project_root / lang["workdir"]
        cmd = lang["build"]
        print(f"  Build: {' '.join(cmd)}")
        if self.dry_run:
            return
        try:
            subprocess.run(cmd, cwd=str(workdir), check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print(f"  ERROR de compilacion:\n{e.stderr}")
            raise

    # -------------------------------------------------------------------
    # Start / Stop server
    # -------------------------------------------------------------------

    def _start_server(self, lang: Dict[str, Any]) -> None:
        workdir = self.project_root / lang["workdir"]
        cmd = _resolve_executable(lang["start_cmd"], workdir)

        print(f"  Iniciando servidor {lang['name']}: {' '.join(cmd)}")
        if self.dry_run:
            return

        self.servers[lang["name"]] = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _stop_all_servers(self) -> None:
        if not self.servers:
            return

        print("  Deteniendo servidores...")
        for name, proc in self.servers.items():
            if proc.poll() is None:
                print(f"    {name}: terminando...")
                proc.terminate()
        for name, proc in self.servers.items():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.servers.clear()

    # -------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------

    def _wait_for_health(self, name: str, port: int) -> None:
        url = f"http://127.0.0.1:{port}/health"
        print(f"  Esperando health check en {url} ...", end="", flush=True)

        if self.dry_run:
            print(" OK (dry-run)")
            return

        start = time.monotonic()
        while time.monotonic() - start < HEALTH_TIMEOUT:
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    if resp.status == 200:
                        print(" OK")
                        time.sleep(1)  # small grace period after healthy
                        return
            except Exception:
                pass
            time.sleep(HEALTH_INTERVAL)

        raise TimeoutError(
            f"El servidor {name} no respondio en {url} tras {HEALTH_TIMEOUT}s"
        )

    # -------------------------------------------------------------------
    # Ejecucion individual de k6
    # -------------------------------------------------------------------

    def _run_single_k6(
        self, lang_name: str, port: int, exp: Dict[str, Any]
    ) -> Dict[str, Any]:
        algo = self._find_algorithm(exp["algorithm"])
        endpoint = algo["endpoints"][exp["scenario"]]
        payload = json.dumps(algo["payload"])
        base_url = f"http://127.0.0.1:{port}"

        summary_file = self.tmp_dir / f"k6_{lang_name}_{exp['algorithm']}_{exp['scenario']}_{exp['replica']}.json"

        cmd = [
            "k6", "run",
            "--quiet",
            "-e", f"BASE_URL={base_url}",
            "-e", f"ENDPOINT={endpoint}",
            "-e", f"PAYLOAD={payload}",
            "--summary-export", str(summary_file),
            "--duration", self.config["experiment"]["k6_duration"],
            "--vus", str(self.config["experiment"]["k6_vus"]),
            "--no-color",
            str(self.benchmark_js),
        ]

        subprocess.run(cmd, capture_output=True, text=True, timeout=K6_TIMEOUT)

        with open(summary_file, encoding="utf-8") as f:
            summary = json.load(f)

        metrics = summary["metrics"]
        dur = metrics["request_duration"]
        err = metrics["error_rate"]
        reqs = metrics["total_requests"]

        return {
            "avg_ms": round(dur.get("avg", 0), 2),
            "min_ms": round(dur.get("min", 0), 2),
            "max_ms": round(dur.get("max", 0), 2),
            "p50_ms": _get_percentile(dur, 50) or 0,
            "p90_ms": _get_percentile(dur, 90) or 0,
            "p95_ms": _get_percentile(dur, 95) or 0,
            "p99_ms": _get_percentile(dur, 99) or 0,
            "sample_size": int(reqs.get("count", 0)),
            "error_rate": round(err.get("value", 1), 6),
            "throughput_rps": round(reqs.get("rate", 0), 2),
        }

    # -------------------------------------------------------------------
    # Construccion del disenio experimental
    # -------------------------------------------------------------------

    def _build_experiment_block(self) -> List[Dict[str, Any]]:
        """
        Retorna una lista plana con todos los experimentos (todos los lenguajes,
        algoritmos, escenarios y replicas) completamente mezclada.
        """
        experiments: List[Dict[str, Any]] = []

        for lang in self.config["languages"]:
            for algo in self.config["algorithms"]:
                for scenario in ("seq", "conc"):
                    for replica in range(1, self.config["experiment"]["replicas"] + 1):
                        experiments.append({
                            "language": lang["name"],
                            "port": lang["port"],
                            "algorithm": algo["name"],
                            "scenario": scenario,
                            "replica": replica,
                        })

        rng = random.Random(self.config["experiment"]["seed"])
        rng.shuffle(experiments)
        return experiments

    def _find_algorithm(self, name: str) -> Dict[str, Any]:
        for algo in self.config["algorithms"]:
            if algo["name"] == name:
                return algo
        raise KeyError(f"Algoritmo no encontrado: {name}")

    # -------------------------------------------------------------------
    # Utilidades
    # -------------------------------------------------------------------

    def _create_dirs(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def _setup_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._on_interrupt)
        signal.signal(signal.SIGTERM, self._on_interrupt)

    def _on_interrupt(self, signum: int, frame: Any) -> None:
        print("\n\nInterrupcion recibida. Finalizando...")
        self.cancelled = True
        self._stop_all_servers()

    def _print_dry_run(self, experiments: List[Dict[str, Any]]) -> None:
        print("\n=== DRY RUN (no se ejecuta nada) ===\n")
        print(f"  Orden completamente aleatorio (seed={self.config['experiment']['seed']})")
        print()
        for exp in experiments[:15]:
            print(f"    {exp['language']:6s} | {exp['algorithm']:10s} | {exp['scenario']:4s} | rep {exp['replica']}")
        if len(experiments) > 15:
            print(f"    ... y {len(experiments) - 15} mas")
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark runner multi-lenguaje con k6"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Ruta al archivo de configuracion YAML (default: config.yaml en el directorio del script)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar que se ejecutaria sin ejecutar nada",
    )
    args = parser.parse_args()

    config_path = args.config or str(Path(__file__).parent / "config.yaml")

    runner = BenchmarkRunner(config_path, dry_run=args.dry_run)
    runner.run()


if __name__ == "__main__":
    main()
