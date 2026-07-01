"""
Re-Scorear Empresas
===================
Lee el CSV de la carpeta, re-corre score_rut() por cada empresa
y actualiza las columnas reason_score1 y pain_point1.

CONFIGURACIÓN:
  N_PROCESAR      — cuántas empresas procesar (None = todas)
  REESCRIBIR      — True: reprocesa aunque ya tenga reason_score1
  ACTUALIZAR_SCORE — True: también sobreescribe Score_1 con el nuevo score
  MAX_WORKERS     — llamadas paralelas al LLM/Charon
"""

import sys, os, time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/Users/juanpablozepeda/Proyecto Plutto /Scoring")
from Scoring_ICP import score_rut

# ─────────────────────────────────────────────────────────────────────────────
CSV_PATH        = "/Users/juanpablozepeda/Proyecto Plutto /Re-Scoreo_companies/empresas_sii_15mayo_sin_outreach (2).csv"
N_PROCESAR      = None   # ← None = todas
REESCRIBIR      = True   # ← todas tienen datos previos, hay que sobreescribir
ACTUALIZAR_SCORE = False # ← True para también sobreescribir Score_1
MAX_WORKERS     = 3
DELAY           = 0.3
# ─────────────────────────────────────────────────────────────────────────────


def _str(val) -> str:
    try:
        if pd.isna(val): return ""
    except (TypeError, ValueError): pass
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none", "nat", "null", "") else s


def _procesar_fila(row) -> dict:
    rut    = _str(row.get("tin"))
    nombre = _str(row.get("company_name")) or _str(row.get("legal_name")) or rut

    resultado = {
        "idx":          row.name,
        "tin":          rut,
        "company_name": nombre,
        "reason_score1": None,
        "pain_point1":   None,
        "Score_1_new":   None,
        "error":         None,
    }

    if not rut:
        resultado["error"] = "sin RUT"
        return resultado

    try:
        score = score_rut(rut)
        resultado["reason_score1"] = score.get("reasoning", "")
        resultado["pain_point1"]   = score.get("pain_point", "")
        resultado["Score_1_new"]   = score.get("score")
    except Exception as e:
        resultado["error"] = str(e)[:120]

    return resultado


def main():
    print("=" * 62)
    print("  RE-SCOREAR EMPRESAS")
    print(f"  N_PROCESAR: {N_PROCESAR or 'todas'} | REESCRIBIR: {REESCRIBIR} | ACTUALIZAR_SCORE: {ACTUALIZAR_SCORE}")
    print("=" * 62)

    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
    print(f"\n📂 {len(df)} empresas en el CSV")

    # Asegurar columnas
    for col in ["reason_score1", "pain_point1"]:
        if col not in df.columns:
            df[col] = None

    # Filtrar las que necesitan procesarse
    def _necesita(row):
        if REESCRIBIR:
            return True
        return _str(row.get("reason_score1")) == ""

    candidatas = df[df.apply(_necesita, axis=1)]
    if N_PROCESAR:
        candidatas = candidatas.head(N_PROCESAR)

    print(f"🎯 A procesar: {len(candidatas)}  |  Ya con datos: {len(df) - len(candidatas)}\n")

    if candidatas.empty:
        print("✅ Nada que procesar. Activa REESCRIBIR=True para forzar.")
        return

    filas = [row for _, row in candidatas.iterrows()]
    resultados = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futuros = {ex.submit(_procesar_fila, row): row.name for row in filas}
        for n, fut in enumerate(as_completed(futuros), 1):
            try:
                res = fut.result()
                resultados.append(res)
                nombre = res["company_name"][:40]
                if res["error"]:
                    print(f"  {n}/{len(filas)}  ✗  {nombre:<42}  Error: {res['error']}")
                else:
                    score_str = f"score={res['Score_1_new']}" if res["Score_1_new"] is not None else ""
                    print(f"  {n}/{len(filas)}  ✓  {nombre:<42}  {score_str}")
            except Exception as e:
                print(f"\n  ⚠️  Error inesperado: {e}")
            time.sleep(DELAY)

    # Aplicar resultados al DataFrame
    ok = err = 0
    for res in resultados:
        idx = res["idx"]
        if res["error"]:
            err += 1
            continue
        df.at[idx, "reason_score1"] = res["reason_score1"]
        df.at[idx, "pain_point1"]   = res["pain_point1"]
        if ACTUALIZAR_SCORE and res["Score_1_new"] is not None:
            df.at[idx, "Score_1"] = res["Score_1_new"]
        ok += 1

    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

    print(f"\n✅ {ok} actualizadas | ⚠️ {err} errores")
    print(f"💾 Guardado en: {CSV_PATH}")


if __name__ == "__main__":
    main()
