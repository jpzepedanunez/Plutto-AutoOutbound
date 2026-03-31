import anthropic
import json

client = anthropic.Anthropic()


def score_lead(
    company_name: str,
    rut: str,
    giro: str,
    tramo: str,
    region: str,
    signal: str = "N/A",
) -> dict:
    prompt = f"""Eres un analista de ICP para Plutto, plataforma de compliance y KYB B2B en Chile.
Evalúa esta empresa como lead potencial y asigna un score de 0 a 100:

Empresa: {company_name}
RUT: {rut}
Industria/Giro: {giro}
Tramo ventas SII: {tramo}
Región: {region}
Señal (si aplica): {signal}

Criterios de scoring:
- Regulación compliance obligatoria (+30): ¿La industria tiene obligación legal de verificar contrapartes? Financiero (CMF), energía (SEC/CNE), minería (Sernageomin), utilities (SISS/SEC)
- Volumen de proveedores: Según industria y tamaño, ¿maneja muchos proveedores?, >200 proveedores (+25), entre 50 y 200 = (+15), menos de 50 (+5)
- Tamaño empresa: ¿Es gran empresa o mediana? Gran empresa 20, mediana +12 y pequeña +5.
- Señal reciente: ¿Hay un evento que genera urgencia? Licitación adjudicada (+15), multa regulatoria (+15), M&A (+12), nueva regulación sectorial (+10)
- Competidor existente (-10): ¿Es probable que ya tenga solución? Verificar si usa Regcheq, Lexis Nexis, KPMG, manual, etc.

Responde SOLO con un JSON con esta estructura exacta:
{{
  "score_total": <número 0-100>,
  "breakdown": {{
    "regulacion_compliance": {{"puntos": <número>, "razon": "<texto breve>"}},
    "volumen_proveedores": {{"puntos": <número>, "razon": "<texto breve>"}},
    "tamaño_empresa": {{"puntos": <número>, "razon": "<texto breve>"}},
    "señal_reciente": {{"puntos": <número>, "razon": "<texto breve>"}},
    "competidor_existente": {{"puntos": <número>, "razon": "<texto breve>"}}
  }},
  "resumen": "<una línea con la conclusión>"
}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    resultado = json.loads(text[text.find("{") : text.rfind("}") + 1])

    # ── Imprimir breakdown ────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  SCORING PLUTTO — {company_name}")
    print(f"{'='*55}")

    criterios = {
        "regulacion_compliance": "Regulación compliance",
        "volumen_proveedores":   "Volumen de proveedores",
        "tamaño_empresa":        "Tamaño empresa",
        "señal_reciente":        "Señal reciente",
        "competidor_existente":  "Competidor existente",
    }

    suma = 0
    for key, label in criterios.items():
        item = resultado["breakdown"][key]
        puntos = item["puntos"]
        razon = item["razon"]
        suma += puntos
        signo = "+" if puntos >= 0 else ""
        print(f"  {label:<28} {signo}{puntos:>3}  →  {razon}")

    print(f"{'─'*55}")
    print(f"  {'SCORE TOTAL':<28} {resultado['score_total']:>4} / 100")
    print(f"{'='*55}")
    print(f"  {resultado['resumen']}")
    print(f"{'='*55}\n")

    return resultado


# ── Ejemplo de uso ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    resultado = score_lead(
        company_name="Banco de Chile",
        rut="97.004.000-5",
        giro="Intermediación monetaria / Banca",
        tramo="Tramo 13 — sobre 1MM UF",
        region="Región Metropolitana",
        signal="Licitación adjudicada en Mercado Público Q1 2025",
    )
