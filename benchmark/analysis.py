#!/usr/bin/env python3
"""
Analisis estadistico ANOVA de resultados de benchmark
=====================================================
Realiza un ANOVA de 3 factores (Algoritmo x Variante x Lenguaje)
sobre el CSV generado por runner.py.

Factores:
  - algorithm  (hash, stringproc, prime, jsonproc)
  - scenario   (seq, conc) -> "variante"
  - language   (python, go, c, rust)

Uso:
  python analysis.py --csv benchmark/results/benchmark_*.csv
  python analysis.py --csv results.csv --metric p95_ms
  python analysis.py --generate-sample --csv sample.csv  # datos ficticios
"""

import argparse
import os
import random
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy import stats

import statsmodels.api as sm
from statsmodels.formula.api import ols
from statsmodels.stats.multicomp import pairwise_tukeyhsd

# ---------------------------------------------------------------------------
# Configuracion global de plots
# ---------------------------------------------------------------------------

sns.set_style("whitegrid")
sns.set_context("paper", font_scale=1.15)
plt.rcParams.update({
    "figure.max_open_warning": 0,
    "figure.dpi": 120,
    "savefig.dpi": 150,
    "savefig.bbox": "tight",
})

# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------


def load_data(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["language"]  = df["language"].astype("category")
    df["algorithm"] = df["algorithm"].astype("category")
    df["scenario"]  = df["scenario"].astype("category")
    df["variante"]  = df["scenario"].cat.rename_categories(
        {"seq": "secuencial", "conc": "concurrente"}
    )
    return df


# ---------------------------------------------------------------------------
# ANOVA de 3 factores
# ---------------------------------------------------------------------------


def run_anova(df: pd.DataFrame, metric: str = "avg_ms"):
    formula = f"{metric} ~ C(language) * C(variante) * C(algorithm)"
    model = ols(formula, data=df).fit()

    if model.df_resid <= 0:
        print("  [!] Sin suficientes replicas para el modelo completo con interacciones.")
        print("  [!] Ajustando modelo solo con efectos principales...")
        formula = f"{metric} ~ C(language) + C(variante) + C(algorithm)"
        model = ols(formula, data=df).fit()

    if model.df_resid <= 0:
        print("  [!] Aun sin replicas suficientes para efectos principales.")
        print("  [!) Necesitas al menos 2 replicas por configuracion experimental.")
        print("  [!) Aumenta 'replicas' en config.yaml y vuelve a ejecutar el runner.")
        return model, pd.DataFrame()

    anova = sm.stats.anova_lm(model, typ=2)
    return model, anova


def print_anova(anova: pd.DataFrame) -> None:
    if anova.empty:
        print("\n  No se pudo calcular la tabla ANOVA (replicas insuficientes).\n")
        return
    print(f"\n{'='*70}")
    print(f"  ANOVA DE 3 FACTORES (Type II)")
    print(f"{'='*70}\n")
    print(f"{'Fuente':<45s} {'SS':>12s} {'df':>5s} {'F':>10s} {'p-valor':>10s}")
    print("-" * 85)
    for idx, row in anova.iterrows():
        pval = row["PR(>F)"]
        sig = "***" if pval < 0.001 else "**" if pval < 0.01 else "*" if pval < 0.05 else "ns"
        print(f"{idx:<45s} {row['sum_sq']:>12.4f} {row['df']:>5.1f} "
              f"{row['F']:>10.4f} {pval:>10.6f}  {sig}")
    print("-" * 85)
    print("  *** p<0.001  ** p<0.01  * p<0.05  ns: no significativo")
    print()


# ---------------------------------------------------------------------------
# Post-hoc (Tukey HSD para cada factor principal)
# ---------------------------------------------------------------------------


def run_tukey(df: pd.DataFrame, metric: str, factor: str) -> str:
    res = pairwise_tukeyhsd(df[metric], df[factor], alpha=0.05)
    lines = [f"\n=== Tukey HSD: {factor} ===", str(res)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Visualizaciones
# ---------------------------------------------------------------------------


def plot_interaction(df: pd.DataFrame, metric: str) -> plt.Figure:
    """Interaction plot: Language x Algorithm (para ver si el ranking depende del algoritmo)."""
    data = df.groupby(["language", "algorithm"], observed=True)[metric].agg(
        ["mean", "sem"]
    ).reset_index()

    fig, ax = plt.subplots(figsize=(10, 6))
    for algo in data["algorithm"].cat.categories:
        subset = data[data["algorithm"] == algo]
        ax.errorbar(
            x=range(len(subset)),
            y=subset["mean"],
            yerr=1.96 * subset["sem"],
            label=algo,
            marker="o",
            capsize=4,
            linewidth=2,
        )
    ax.set_xticks(range(len(data["language"].cat.categories)))
    ax.set_xticklabels(data["language"].cat.categories)
    ax.set_xlabel("Lenguaje")
    ax.set_ylabel(f"{metric}")
    ax.set_title(f"Interaccion Lenguaje x Algoritmo\nVariable: {metric}")
    ax.legend(title="Algoritmo")
    return fig


def plot_anova_diagnostics(model, output_path: str):
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))

    # Q-Q plot
    sm.qqplot(model.resid, line="s", ax=axes[0, 0])
    axes[0, 0].set_title("Q-Q Plot (Normalidad de residuales)")

    # Residuals vs Fitted
    axes[0, 1].scatter(model.fittedvalues, model.resid, alpha=0.4, edgecolors="none")
    axes[0, 1].axhline(y=0, color="r", linestyle="--", alpha=0.5)
    axes[0, 1].set_xlabel("Valores ajustados")
    axes[0, 1].set_ylabel("Residuales")
    axes[0, 1].set_title("Residuales vs Valores Ajustados")

    # Histogram of residuals
    axes[1, 0].hist(model.resid, bins=30, edgecolor="white", alpha=0.7)
    axes[1, 0].set_xlabel("Residual")
    axes[1, 0].set_ylabel("Frecuencia")
    axes[1, 0].set_title("Distribucion de Residuales")

    # Scale-Location
    std_resid = np.sqrt(np.abs(model.resid_pearson))
    axes[1, 1].scatter(model.fittedvalues, std_resid, alpha=0.4, edgecolors="none")
    axes[1, 1].axhline(y=0, color="r", linestyle="--", alpha=0.5)
    axes[1, 1].set_xlabel("Valores ajustados")
    axes[1, 1].set_ylabel("Raiz de |Residual Estandarizado|")
    axes[1, 1].set_title("Scale-Location")

    plt.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_boxplots(df: pd.DataFrame, metric: str, output_dir: str):
    """Boxplots agrupados por los 3 factores."""
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    sns.boxplot(data=df, x="language", y=metric, hue="algorithm", ax=axes[0])
    axes[0].set_title(f"{metric} por Lenguaje y Algoritmo")
    axes[0].set_xlabel("Lenguaje")
    axes[0].legend(title="Algoritmo", loc="upper left")

    sns.boxplot(data=df, x="language", y=metric, hue="variante", ax=axes[1])
    axes[1].set_title(f"{metric} por Lenguaje y Variante")
    axes[1].set_xlabel("Lenguaje")
    axes[1].legend(title="Variante")

    sns.boxplot(data=df, x="algorithm", y=metric, hue="variante", ax=axes[2])
    axes[2].set_title(f"{metric} por Algoritmo y Variante")
    axes[2].set_xlabel("Algoritmo")
    axes[2].legend(title="Variante")

    plt.tight_layout()
    path = os.path.join(output_dir, "boxplots.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_bar_chart(df: pd.DataFrame, output_dir: str):
    """
    Barras agrupadas: total de requests (sample_size) por lenguaje y variante,
    con una faceta (subplot) por algoritmo.
    """
    algo_order = df["algorithm"].cat.categories.tolist()
    lang_order = df["language"].cat.categories.tolist()
    n_algos = len(algo_order)

    cols = 2
    rows = (n_algos + 1) // 2
    fig, axes = plt.subplots(rows, cols, figsize=(7 * cols, 5 * rows))
    axes = axes.flatten() if rows * cols > 1 else [axes]

    for i, algo in enumerate(algo_order):
        ax = axes[i]
        subset = df[df["algorithm"] == algo]

        # Aggregate: sum sample_size per language x scenario
        agg = subset.groupby(
            ["language", "variante"], observed=True
        )["sample_size"].sum().reset_index()

        sns.barplot(
            data=agg,
            x="language",
            y="sample_size",
            hue="variante",
            ax=ax,
            palette={"secuencial": "#4C72B0", "concurrente": "#DD8452"},
            edgecolor="black",
            linewidth=0.5,
        )

        # Annotate bar tops
        for container in ax.containers:
            ax.bar_label(container, fmt="%.0f", fontsize=7, padding=1)

        ax.set_title(f"{algo}", fontsize=13, fontweight="bold")
        ax.set_xlabel("")
        ax.set_ylabel("Total requests")
        ax.legend(title="Variante", fontsize=8)
        ax.tick_params(axis="x", labelsize=9)

    # Hide unused subplots
    for j in range(i + 1, len(axes)):
        axes[j].set_visible(False)

    plt.tight_layout()
    path = os.path.join(output_dir, "bar_chart_total_requests.png")
    fig.savefig(path)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Reporte de texto
# ---------------------------------------------------------------------------


def generate_report(output_dir: str, df: pd.DataFrame, metric: str,
                    model, anova: pd.DataFrame) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("  REPORTE DE ANALISIS ESTADISTICO - BENCHMARK MULTI-LENGUAJE")
    lines.append("=" * 70)
    lines.append(f"")
    lines.append(f"Fecha: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"Metrica analizada: {metric}")
    lines.append(f"Observaciones: {len(df)}")
    lines.append(f"")
    lines.append(f"Lenguajes: {', '.join(sorted(df['language'].unique()))}")
    lines.append(f"Algoritmos: {', '.join(sorted(df['algorithm'].unique()))}")
    lines.append(f"Variantes: {', '.join(sorted(df['variante'].unique()))}")
    lines.append(f"")

    # Estadisticas descriptivas
    lines.append("-" * 70)
    lines.append("  ESTADISTICAS DESCRIPTIVAS")
    lines.append("-" * 70)
    desc = df.groupby(
        ["language", "algorithm", "variante"], observed=True
    )[metric].describe()
    lines.append(desc.to_string())
    lines.append(f"")

    # ANOVA
    lines.append("-" * 70)
    lines.append("  ANOVA DE 3 FACTORES")
    lines.append("-" * 70)
    lines.append(anova.to_string())
    lines.append(f"")

    # R-cuadrado del modelo
    lines.append(f"R-cuadrado del modelo completo: {model.rsquared:.4f}")
    lines.append(f"R-cuadrado ajustado: {model.rsquared_adj:.4f}")
    lines.append(f"")

    # Conclusiones
    lines.append("-" * 70)
    lines.append("  CONCLUSIONES PRELIMINARES")
    lines.append("-" * 70)
    if anova.empty or "PR(>F)" not in anova.columns:
        lines.append("No se pudo calcular la tabla ANOVA.")
        lines.append("Aumenta 'replicas' en config.yaml (minimo 2) y vuelve a ejecutar el runner.")
    else:
        sig_factors = anova[anova["PR(>F)"] < 0.05].index.tolist()
        if sig_factors:
            lines.append(f"Factores significativos (p<0.05):")
            for f in sig_factors:
                p = anova.loc[f, "PR(>F)"]
                star = "***" if p < 0.001 else "**" if p < 0.01 else "*"
                lines.append(f"  - {f}  (p={p:.6f}) {star}")
        else:
            lines.append("Ningun factor resulto significativo al nivel alpha=0.05.")
    lines.append(f"")

    path = os.path.join(output_dir, "reporte.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# Generador de datos de muestra
# ---------------------------------------------------------------------------


def generate_sample_data(output_path: str) -> str:
    """
    Genera datos ficticios con patrones conocidos para verificar el analisis.
    """
    random.seed(42)
    np.random.seed(42)

    algorithms = ["hash", "stringproc", "prime", "jsonproc"]
    languages = ["python", "go", "c", "rust"]
    scenarios = ["seq", "conc"]
    replicas = 10
    n_obs = len(algorithms) * len(languages) * len(scenarios) * replicas

    # Latencia base por algoritmo (diferentes escalas)
    base_latency = {
        "hash": 200,
        "stringproc": 1.5,
        "prime": 400,
        "jsonproc": 50,
    }

    # Penalizacion por lenguaje (logica: C < Rust < Go < Python)
    lang_penalty = {"c": 0.7, "rust": 0.85, "go": 1.0, "python": 1.8}

    # Penalizacion por scenario (seq es mas lento que conc si hay lock overhead)
    scenario_penalty = {"seq": 1.15, "conc": 1.0}

    # Interaccion: lock overhead afecta mas a Python/Rust (async)
    lock_overhead = {
        "python": 0.3,
        "rust": 0.25,
        "go": 0.05,
        "c": 0.05,
    }

    rows = []
    for algo in algorithms:
        for lang in languages:
            for scen in scenarios:
                base = base_latency[algo]
                penalty_factor = lang_penalty[lang]
                scen_factor = scenario_penalty[scen]
                seq_extra = lock_overhead[lang] if scen == "seq" else 0

                # Latencia esperada = base * lang * scen + interaccion
                expected = base * penalty_factor * scen_factor + seq_extra * base * 0.3

                for rep in range(1, replicas + 1):
                    # Ruido normal (proporcional a la base)
                    noise = np.random.normal(0, base * 0.08)
                    # Ruido multiplicativo (variabilidad relativa)
                    mult_noise = np.random.lognormal(0, 0.12) - 0.06
                    latency = max(0.1, expected * mult_noise + noise)

                    # throughput approx ~ 1000/latency (simulado)
                    throughput = 1000 / latency * (1 + np.random.normal(0, 0.05))

                    error_rate = max(0, min(0.05, np.random.exponential(0.002)))

                    rows.append({
                        "timestamp": datetime.now().isoformat(),
                        "language": lang,
                        "algorithm": algo,
                        "scenario": scen,
                        "replica": rep,
                        "seed": 42,
                        "vus": 10,
                        "duration": "30s",
                        "sample_size": int(30 * (1000 / latency)),
                        "avg_ms": round(latency, 2),
                        "min_ms": round(latency * 0.7, 2),
                        "max_ms": round(latency * 1.4, 2),
                        "p50_ms": round(latency * 0.95, 2),
                        "p90_ms": round(latency * 1.25, 2),
                        "p95_ms": round(latency * 1.35, 2),
                        "p99_ms": round(latency * 1.5, 2),
                        "error_rate": round(error_rate, 6),
                        "throughput_rps": round(throughput, 2),
                    })

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)
    print(f"Datos de muestra generados: {output_path} ({len(df)} filas)")
    return output_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="ANOVA de 3 factores sobre resultados de benchmark"
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="Ruta al CSV generado por runner.py",
    )
    parser.add_argument(
        "--metric",
        default="avg_ms",
        choices=["avg_ms", "p50_ms", "p95_ms", "p99_ms", "throughput_rps"],
        help="Metrica dependiente a analizar (default: avg_ms)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Directorio de salida para resultados",
    )
    parser.add_argument(
        "--generate-sample",
        action="store_true",
        help="Genera datos de muestra para pruebas",
    )
    args = parser.parse_args()

    # --- Generar datos de muestra ---
    if args.generate_sample:
        sample_path = args.csv or os.path.join(
            os.path.dirname(__file__), "results", "sample_data.csv"
        )
        os.makedirs(os.path.dirname(sample_path), exist_ok=True)
        generate_sample_data(sample_path)
        if not args.csv:
            print("Usa: --csv sample_data.csv --output ./analisis_muestra")
            return

    # --- Cargar datos ---
    if not args.csv:
        parser.print_help()
        print("\nError: Debes proporcionar --csv o --generate-sample")
        sys.exit(1)

    csv_path = args.csv
    if not os.path.exists(csv_path):
        print(f"Error: No se encuentra el archivo CSV: {csv_path}")
        sys.exit(1)

    print(f"Cargando datos: {csv_path}")
    df = load_data(csv_path)
    print(f"  {len(df)} observaciones")
    print(f"  Lenguajes: {list(df['language'].cat.categories)}")
    print(f"  Algoritmos: {list(df['algorithm'].cat.categories)}")
    print(f"  Variantes: {list(df['scenario'].cat.categories)}")
    print(f"  Replicas: {df['replica'].max()}")

    # --- Preparar directorio de salida ---
    if args.output:
        output_dir = args.output
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(
            os.path.dirname(csv_path),
            f"analisis_{timestamp}",
        )
    os.makedirs(output_dir, exist_ok=True)
    print(f"  Salida: {output_dir}/")

    # --- ANOVA ---
    metric = args.metric
    model, anova = run_anova(df, metric)
    print_anova(anova)

    # --- Post-hoc ---
    for factor in ["language", "variante", "algorithm"]:
        try:
            print(run_tukey(df, metric, factor))
        except Exception as e:
            print(f"Tukey para {factor} fallo: {e}")

    # --- Graficos ---
    print(f"\nGenerando graficos...")

    # Boxplots
    plot_boxplots(df, metric, output_dir)
    print(f"  boxplots.png")

    # Interaction plot
    fig = plot_interaction(df, metric)
    fig.savefig(os.path.join(output_dir, "interaction_plot.png"))
    plt.close(fig)
    print(f"  interaction_plot.png")

    # Diagnostico de residuales
    plot_anova_diagnostics(model, os.path.join(output_dir, "diagnostics.png"))
    print(f"  diagnostics.png")

    # Bar chart: total requests por lenguaje y algoritmo
    plot_bar_chart(df, output_dir)
    print(f"  bar_chart_total_requests.png")

    # --- Reporte de texto ---
    report_path = generate_report(output_dir, df, metric, model, anova)
    print(f"  reporte.txt")

    print(f"\nAnalisis completado. Resultados en: {output_dir}/")


if __name__ == "__main__":
    main()
