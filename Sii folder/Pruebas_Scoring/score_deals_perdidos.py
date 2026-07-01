"""
Score Deals Perdidos
====================
Lee el CSV de deals perdidos, busca el RUT de cada empresa (Gemini Search),
aplica score_rut() y guarda los resultados en score_deals_perdidos.csv.

CONFIGURACIÓN:
  N_PROCESAR — cuántas empresas procesar (None = todas)
  REESCRIBIR — True: sobreescribe si el RUT ya fue buscado antes
"""

import sys, os, time, json
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/Users/juanpablozepeda/Proyecto Plutto /Scoring")
from Scoring_ICP import score_rut, _extraer_json, client

# ─────────────────────────────────────────────────────────────────────────────
CSV_PATH   = "/Users/juanpablozepeda/Proyecto Plutto /Sii folder/Pruebas_Scoring/deals_perdidos_noesprioridad_por_industria - Deals por Industria.csv"
OUTPUT     = "/Users/juanpablozepeda/Proyecto Plutto /Sii folder/Pruebas_Scoring/score_deals_perdidos.csv"
N_PROCESAR = None     # ← None para todas
REESCRIBIR = False  # ← True para re-buscar RUTs ya encontrados
MAX_WORKERS = 3
DELAY       = 0.3
# ─────────────────────────────────────────────────────────────────────────────


def _buscar_rut(company_name: str, email_hint: str = "") -> str | None:
    """Usa Gemini para encontrar el RUT de una empresa chilena."""
    import re as _re
    dominio = f" (email o dominio conocido: {email_hint})" if email_hint else ""
    prompt = (
        f"¿Cuál es el RUT tributario chileno de la empresa '{company_name}'{dominio}?\n"
        f"El RUT es el identificador tributario chileno, formato ej: 76045184-2 o 12.345.678-9.\n"
        f"Puedes encontrarlo en el sitio del SII (sii.cl), en el sitio web de la empresa, "
        f"en directorios como empresasenchilemapa.cl, dateas.cl, o en sus facturas.\n"
        f"Responde SOLO con el RUT sin puntos y con guión (ej: 76045184-2). "
        f"Si definitivamente no lo encuentras, responde: null"
    )
    try:
        response = client.chat.completions.create(
            model="gemini-2.5-flash",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text or text.lower() in ("null", "none", "no encontrado", "no sé", "desconocido"):
            return None
        # Extraer RUT con regex — acepta con o sin puntos
        match = _re.search(r'\d[\d.]{5,11}-[\dkK]', text)
        if match:
            rut = match.group(0).replace(".", "")
            return rut
    except Exception:
        pass
    return None


def _procesar_fila(row) -> dict:
    company_name = str(row.get("Deal Name") or "").strip()
    rut_existente = str(row.get("rut_encontrado") or "").strip()

    # Extraer email del contacto como pista
    contacto = str(row.get("Associated Contact") or "")
    email_hint = ""
    import re as _re
    match = _re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', contacto)
    if match:
        email_hint = match.group(0)

    # Reutilizar RUT ya encontrado si REESCRIBIR = False
    if not REESCRIBIR and rut_existente and rut_existente.lower() not in ("nan", "none", ""):
        rut = rut_existente
        rut_fuente = "cache"
    else:
        rut = _buscar_rut(company_name, email_hint=email_hint)
        rut_fuente = "gemini" if rut else "no_encontrado"

    resultado = {
        "idx":         row.name,
        "deal_name":   company_name,
        "rut_encontrado": rut or "",
        "rut_fuente":  rut_fuente,
        "score":       None,
        "vertical":    None,
        "pain_point":  None,
        "reasoning":   None,
        "charon_ok":   None,
    }

    if rut:
        try:
            score = score_rut(rut)
            resultado.update({
                "score":      score.get("score"),
                "vertical":   score.get("vertical"),
                "pain_point": score.get("pain_point"),
                "reasoning":  score.get("reasoning"),
                "charon_ok":  score.get("charon_ok"),
            })
        except Exception as e:
            resultado["reasoning"] = f"Error score: {e}"

    return resultado


def main():
    print("=" * 62)
    print("  SCORE DEALS PERDIDOS")
    print(f"  N_PROCESAR: {N_PROCESAR or 'todas'} | REESCRIBIR: {REESCRIBIR}")
    print("=" * 62)

    df_in = pd.read_csv(CSV_PATH)
    print(f"\n📂 {len(df_in)} deals en el CSV")

    # Cargar output anterior si existe (para no re-procesar)
    if os.path.exists(OUTPUT) and not REESCRIBIR:
        df_out = pd.read_csv(OUTPUT)
        ya_procesados = set(df_out["deal_name"].dropna())
        print(f"♻️  {len(ya_procesados)} ya procesados en output anterior")
    else:
        df_out = pd.DataFrame()
        ya_procesados = set()

    # Agregar columnas al df_in si no existen
    for col in ("rut_encontrado", "rut_fuente"):
        if col not in df_in.columns:
            df_in[col] = None

    # Fusionar RUTs ya encontrados
    if not df_out.empty and "rut_encontrado" in df_out.columns:
        rut_map = df_out.set_index("deal_name")["rut_encontrado"].to_dict()
        df_in["rut_encontrado"] = df_in["Deal Name"].map(rut_map).fillna(df_in["rut_encontrado"])

    # Filtrar pendientes
    def _necesita_procesar(row):
        if REESCRIBIR:
            return True
        nombre = str(row.get("Deal Name") or "")
        if nombre in ya_procesados:
            return False
        return True

    candidatas = df_in[df_in.apply(_necesita_procesar, axis=1)]
    if N_PROCESAR:
        candidatas = candidatas.head(N_PROCESAR)

    print(f"🎯 A procesar: {len(candidatas)}")
    if candidatas.empty:
        print("✅ Nada nuevo. Revisa OUTPUT o activa REESCRIBIR=True.")
        return

    # Procesar en paralelo
    filas = [row for _, row in candidatas.iterrows()]
    resultados = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futuros = {ex.submit(_procesar_fila, row): row.name for row in filas}
        for n, fut in enumerate(as_completed(futuros), 1):
            try:
                res = fut.result()
                resultados.append(res)
                rut_str  = res["rut_encontrado"] or "sin RUT"
                score_str = f"score={res['score']}" if res["score"] is not None else "sin score"
                print(f"  {n}/{len(filas)}  {res['deal_name'][:45]:<46} {rut_str:<15} {score_str}")
            except Exception as e:
                print(f"\n  ⚠️  Error: {e}")
            time.sleep(DELAY)

    # Construir DataFrame de resultados nuevos
    df_nuevos = pd.DataFrame(resultados).drop(columns=["idx"], errors="ignore")

    # Unir con input para conservar columnas originales
    df_merge = df_in[["Deal Name", "Industry", "Área de la empresa", "Deal Stage"]].copy()
    df_merge = df_merge.rename(columns={"Deal Name": "deal_name"})
    df_nuevos = df_nuevos.merge(df_merge, on="deal_name", how="left")

    # Concatenar con output anterior
    df_final = pd.concat([df_out, df_nuevos], ignore_index=True).drop_duplicates(
        subset=["deal_name"], keep="last"
    )

    df_final.to_csv(OUTPUT, index=False, encoding="utf-8")
    con_rut   = df_final["rut_encontrado"].apply(lambda x: bool(str(x).strip() not in ("", "nan", "None"))).sum()
    con_score = df_final["score"].notna().sum()
    print(f"\n✅ {len(resultados)} procesados")
    print(f"   RUT encontrado: {con_rut}/{len(df_final)}")
    print(f"   Con score:      {con_score}/{len(df_final)}")
    print(f"\n💾 Guardado en: {OUTPUT}")


if __name__ == "__main__":
    main()
