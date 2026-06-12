import pandas as pd
import numpy as np
 
df = pd.read_csv('./results/Data.csv')
df['variante'] = df['scenario'].map({'seq': 'secuencial', 'conc': 'concurrente'})
 
SEP = "=" * 70
 
# ── 1. THROUGHPUT TOTAL ────────────────────────────────────────────────
print(SEP)
print("1. THROUGHPUT TOTAL (suma sample_size por lenguaje+algoritmo+variante)")
print("   (sumando los 3 niveles de VUs y sus 3 réplicas)")
print(SEP)
total_req = df.groupby(['language','algorithm','variante'])['sample_size'].sum().reset_index()
total_req.columns = ['language','algorithm','variante','total_requests']
print(total_req.to_string(index=False))
 
# ── 2. HASH – totales concurrentes ────────────────────────────────────
print(f"\n{SEP}")
print("2. HASH – Total concurrente por lenguaje")
print(SEP)
print(total_req[(total_req['algorithm']=='hash') & (total_req['variante']=='concurrente')].to_string(index=False))
 
# ── 3. HASH – avg_ms a 100 VUs ────────────────────────────────────────
print(f"\n{SEP}")
print("3. HASH – avg_ms a 100 VUs (concurrente vs secuencial)")
print(SEP)
print(df[(df['algorithm']=='hash') & (df['vus']==100)]
      .groupby(['language','variante'])['avg_ms'].mean())
 
# ── 4. HASH – ratios secuencial→concurrente a 100 VUs ─────────────────
print(f"\n{SEP}")
print("4. HASH – ratio seq/conc a 100 VUs por lenguaje")
print(SEP)
for lang in ['rust','go','c','python']:
    s = df[(df['algorithm']=='hash')&(df['language']==lang)&(df['vus']==100)&(df['scenario']=='seq')]['avg_ms'].mean()
    c = df[(df['algorithm']=='hash')&(df['language']==lang)&(df['vus']==100)&(df['scenario']=='conc')]['avg_ms'].mean()
    print(f"  {lang:6s}: seq={s:.1f}ms  conc={c:.1f}ms  ratio={s/c:.2f}x")
 
# ── 5. PRIME – totales concurrentes ───────────────────────────────────
print(f"\n{SEP}")
print("5. PRIME – Total concurrente por lenguaje")
print(SEP)
print(total_req[(total_req['algorithm']=='prime') & (total_req['variante']=='concurrente')].to_string(index=False))
 
# ── 6. PRIME – avg_ms a 10 VUs concurrente ───────────────────────────
print(f"\n{SEP}")
print("6. PRIME – avg_ms a 10 VUs (concurrente)")
print(SEP)
print(df[(df['algorithm']=='prime')&(df['vus']==10)&(df['scenario']=='conc')]
      .groupby('language')['avg_ms'].mean().round(3))
 
# ── 7. JSON – totales y RPS promedio ──────────────────────────────────
print(f"\n{SEP}")
print("7. JSON – Total requests y avg RPS por lenguaje+variante")
print(SEP)
print(df[df['algorithm']=='jsonproc'].groupby(['language','variante']).agg(
    total_requests=('sample_size','sum'),
    avg_rps=('throughput_rps','mean')
).reset_index().to_string(index=False))
 
# ── 8. JSON – avg_ms por lenguaje+variante+VUs ────────────────────────
print(f"\n{SEP}")
print("8. JSON – avg_ms por lenguaje+variante+VUs")
print(SEP)
print(df[df['algorithm']=='jsonproc']
      .groupby(['language','variante','vus'])['avg_ms'].mean()
      .reset_index().to_string(index=False))
 
# ── 9. JSON Python – conc vs seq por VUs ──────────────────────────────
print(f"\n{SEP}")
print("9. JSON Python – conc vs seq por nivel de VUs")
print(SEP)
for vus in [10, 100]:
    s = df[(df['algorithm']=='jsonproc')&(df['language']=='python')&(df['vus']==vus)&(df['scenario']=='seq')]['avg_ms'].mean()
    c = df[(df['algorithm']=='jsonproc')&(df['language']=='python')&(df['vus']==vus)&(df['scenario']=='conc')]['avg_ms'].mean()
    print(f"  {vus} VUs: seq={s:.1f}ms  conc={c:.1f}ms")
 
print("\n  Total requests JSON Python:")
for sc in ['seq','conc']:
    t = df[(df['algorithm']=='jsonproc')&(df['language']=='python')&(df['scenario']==sc)]['sample_size'].sum()
    print(f"    {sc}: {t}")
 
# ── 10. STRINGPROC – totales y RPS a 100 VUs ──────────────────────────
print(f"\n{SEP}")
print("10. STRINGPROC – Total requests y RPS a 100 VUs por lenguaje+variante")
print(SEP)
print(df[(df['algorithm']=='stringproc')&(df['vus']==100)]
      .groupby(['language','variante']).agg(
          total_requests=('sample_size','sum'),
          avg_rps=('throughput_rps','mean')
      ).reset_index().to_string(index=False))
 
# ── 11. STRINGPROC – avg_ms a 100 VUs ────────────────────────────────
print(f"\n{SEP}")
print("11. STRINGPROC – avg_ms a 100 VUs")
print(SEP)
print(df[(df['algorithm']=='stringproc')&(df['vus']==100)]
      .groupby(['language','variante'])['avg_ms'].mean().round(2))
 
# ── 12. MEDIANAS globales avg_ms por lenguaje+algoritmo ───────────────
print(f"\n{SEP}")
print("12. MEDIANAS avg_ms por lenguaje+algoritmo (todas variantes+VUs) — BOXPLOT panel sup-izq")
print(SEP)
print(df.groupby(['language','algorithm'])['avg_ms'].median().unstack().round(1))
 
# ── 13. MEDIANAS por variante+VUs ─────────────────────────────────────
print(f"\n{SEP}")
print("13. MEDIANAS avg_ms por variante+VUs — BOXPLOT panel inf-der")
print(SEP)
print(df.groupby(['variante','vus'])['avg_ms'].median().round(1))
 
# ── 14. LATENCIA MEDIA por lenguaje+algoritmo (interaction plot) ───────
print(f"\n{SEP}")
print("14. MEDIA avg_ms por lenguaje+algoritmo — INTERACTION PLOT")
print(SEP)
print(df.groupby(['language','algorithm'])['avg_ms'].mean().unstack().round(1))
 
# ── 15. PENDIENTES secuencial vs concurrente ──────────────────────────
print(f"\n{SEP}")
print("15. seq vs conc (ms/VU entre 10 y 100 VUs)")
print(SEP)
for var in ['secuencial','concurrente']:
    d = df[df['variante']==var].groupby('vus')['avg_ms'].mean()
    slope = (d[100] - d[10]) / (100 - 10)
    print(f"  {var}: {slope:.1f} ms/VU  (10VUs={d[10]:.1f}ms  100VUs={d[100]:.1f}ms)")
 
# ── 16. DESGLOSE VUs=100 – sample_size ───────────────────────────────
print(f"\n{SEP}")
print("16. SAMPLE_SIZE a 100 VUs por lenguaje+algoritmo+variante")
print(SEP)
print(df[df['vus']==100].groupby(['language','algorithm','variante'])['sample_size']
      .sum().reset_index().to_string(index=False))
 
# ── 17. DISCREPANCIA CLAVE – C jsonproc mediana ───────────────────────
print(f"\n{SEP}")
print("17.C jsonproc")
print(SEP)
c_json = df[(df['algorithm']=='jsonproc')&(df['language']=='c')].sort_values('avg_ms')
print(c_json[['scenario','vus','replica','avg_ms']].to_string(index=False))
med   = df[(df['algorithm']=='jsonproc')&(df['language']=='c')]['avg_ms'].median()
media = df[(df['algorithm']=='jsonproc')&(df['language']=='c')]['avg_ms'].mean()
obs   = df[(df['algorithm']=='jsonproc')&(df['language']=='c')&(df['scenario']=='conc')&(df['vus']==100)&(df['replica']==3)]['avg_ms'].values[0]
print(f"\n  Mediana real C jsonproc : {med:.1f} ms  ← valor correcto")
print(f"  Media real C jsonproc   : {media:.1f} ms")
print(f"  Observación conc/100VU/réplica3: {obs:.2f} ms  ← valor que cita el paper por error")
 
# ── 18. DISCREPANCIA CLAVE – ratio Go/Rust en jsonproc ────────────────
print(f"\n{SEP}")
print("18.  – Ratio Go/Rust mediana jsonproc")
print(SEP)
go_med   = df[(df['algorithm']=='jsonproc')&(df['language']=='go')]['avg_ms'].median()
rust_med = df[(df['algorithm']=='jsonproc')&(df['language']=='rust')]['avg_ms'].median()
rust_hash_med = df[(df['algorithm']=='hash')&(df['language']=='rust')]['avg_ms'].median()
print(f"  Go jsonproc mediana   : {go_med:.1f} ms")
print(f"  Rust jsonproc mediana : {rust_med:.1f} ms  ← paper cita 97ms (es la mediana de Rust en HASH)")
print(f"  Rust hash mediana     : {rust_hash_med:.1f} ms  ← ese 97ms es de aquí")
print(f"  Ratio Go/Rust jsonproc mediana real : {go_med/rust_med:.1f}×  (paper dice 4.4×)")
 
