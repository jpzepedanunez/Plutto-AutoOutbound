import pandas as pd
import os

# ── Paths ─────────────────────────────────────────────────────
DIR             = os.path.dirname(os.path.abspath(__file__))
CSV_MADRE       = os.path.join(DIR, "composicion_sociedades.csv")
CSV_PADRE_HIJO  = os.path.join(DIR, "padre_hijo.csv")


# ── 1. Construir relación Padre → Hijo ────────────────────────

def build_relaciones():
    """
    Lee composicion_sociedades.csv y genera padre_hijo.csv con estructura:
        rut_padre | rut_hijo | pct
    Solo incluye filas donde el socio es una persona jurídica (RUT Socio no vacío).
    """
    df = pd.read_csv(CSV_MADRE, sep=";", dtype=str)

    # Normalizar nombres de columnas
    df.columns = df.columns.str.strip()

    # Quedarse solo con filas donde el socio es persona jurídica
    df_pj = df[df["RUT Socio"].notna() & (df["RUT Socio"].str.strip() != "")].copy()

    # Construir RUT completo con DV: "76888350-5"
    df_pj["rut_padre"] = df_pj["RUT Socio"].str.strip() + "-" + df_pj["DV Socio"].str.strip()
    df_pj["rut_hijo"]  = df_pj["Rut Sociedad"].str.strip() + "-" + df_pj["DV Sociedad"].str.strip()
    df_pj["pct"]       = df_pj["Participación"].fillna("").str.strip().replace("", "N/D")

    resultado = df_pj[["rut_padre", "rut_hijo", "pct"]].reset_index(drop=True)
    resultado.to_csv(CSV_PADRE_HIJO, index=False)

    print(f"✅ padre_hijo.csv generado: {len(resultado):,} relaciones")
    return resultado


# ── 2. Consultar hijos de un RUT ──────────────────────────────

def get_hijos(rut: str) -> dict:
    """
    Dado un RUT (formato '76888350-5'), devuelve cuántas sociedades
    hijas tiene y sus RUTs.

    Si padre_hijo.csv no existe, lo genera automáticamente.

    Retorna:
        {
            "rut":     str,
            "count":   int,
            "hijos":   [(rut_hijo, pct), ...]
        }
    """
    if not os.path.exists(CSV_PADRE_HIJO):
        print("⚠️  padre_hijo.csv no encontrado — generando...")
        build_relaciones()

    df = pd.read_csv(CSV_PADRE_HIJO, dtype=str)

    hijos = df[df["rut_padre"] == rut][["rut_hijo", "pct"]].values.tolist()

    return {
        "rut":   rut,
        "count": len(hijos),
        "hijos": [(h[0], h[1]) for h in hijos],
    }


# ── 3. Consultar padres de un RUT ────────────────────────────

def get_padres(rut: str) -> dict:
    """
    Dado un RUT (formato '76888350-5'), devuelve quiénes son los dueños
    (personas jurídicas) de esa sociedad.

    Si padre_hijo.csv no existe, lo genera automáticamente.

    Retorna:
        {
            "rut":    str,
            "count":  int,
            "padres": [(rut_padre, pct), ...]
        }
    """
    if not os.path.exists(CSV_PADRE_HIJO):
        print("⚠️  padre_hijo.csv no encontrado — generando...")
        build_relaciones()

    df = pd.read_csv(CSV_PADRE_HIJO, dtype=str)

    padres = df[df["rut_hijo"] == rut][["rut_padre", "pct"]].values.tolist()

    return {
        "rut":    rut,
        "count":  len(padres),
        "padres": [(p[0], p[1]) for p in padres],
    }


# ── Ejemplo de uso ────────────────────────────────────────────
if __name__ == "__main__":
    # Regenerar CSV padre_hijo siempre al correr directamente
    if os.path.exists(CSV_PADRE_HIJO):
        os.remove(CSV_PADRE_HIJO)
    build_relaciones()

    # Consultar hijos de un RUT
    rut_test = "99579260-5"
    resultado = get_hijos(rut_test)

    print(f"\nRUT: {resultado['rut']}")
    print(f"Sociedades hijas: {resultado['count']}")
    for rut_hijo, pct in resultado['hijos']:
        print(f"  → {rut_hijo}  ({pct}%)")

    print("── get_padres ──────────────────────────────────────")
    rut_test2 = "99579260-5"
    resultado2 = get_padres(rut_test2)
    print(f"\nRUT: {resultado2['rut']}")
    print(f"Dueños (personas jurídicas): {resultado2['count']}")
    for rut_padre, pct in resultado2['padres']:
        print(f"  → {rut_padre}  ({pct}%)")
