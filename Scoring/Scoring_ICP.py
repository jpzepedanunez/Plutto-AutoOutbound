import json
import requests
from openai import OpenAI

LITELLM_BASE_URL = "https://hydra-portal-dev.fly.dev"
LITELLM_API_KEY  = "sk-VOoWAq-wV6TDvr6ZsSuBOQ"

client = OpenAI(
    base_url=f"{LITELLM_BASE_URL}/v1",
    api_key=LITELLM_API_KEY,
)

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
- Competidor existente: Si no usa competencia (+0),  Verificar si usa Regcheq, Lexis Nexis, KPMG, manual, etc (-10)

Responde SOLO con un JSON con esta estructura exacta:
{{
  "score": <número 0-100>,
  "vertical": "<mining|financiero|utilities|manufactura>",
  "pain_point": "<dolor principal en español, max 20 palabras>",
  "reasoning": "<explicación breve>"
}}"""

    response = client.chat.completions.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.choices[0].message.content.strip()
    resultado = json.loads(text[text.find("{") : text.rfind("}") + 1])

    return resultado


def _clasificar_tamaño(tramo) -> tuple[str, int]:
    """Clasifica el tamaño de empresa según escala SII (tramos 1-13)."""
    try:
        t = int(str(tramo).split("/")[0].strip())
    except (ValueError, AttributeError):
        return "sin información", 0

    if t >= 10: return "gran empresa", 20
    if t >= 8:  return "mediana",      12
    if t >= 5:  return "pequeña",       5
    if t >= 2:  return "micro",         2
    return "sin información", 0


def score_lead_adj1(
    company_name: str,
    rut: str,
    giro: str,
    tramo: str,
    region: str,
    signal: str = "N/A",
) -> dict:
    # Python calcula el score — la IA solo asigna puntos individuales
    MAX_SEGMENTO = {
        "financiero":  95,   # reg(40) + prov(20) + tamaño(20) + señal(15)
        "mining":      95,   # reg(30) + prov(30) + tamaño(20) + señal(15)
        "utilities":   90,   # reg(30) + prov(25) + tamaño(20) + señal(15)
        "manufactura": 90,   # reg(20) + prov(35) + tamaño(20) + señal(15)
        "otro":        90    # reg(30) + prov(25) + tamaño(20) + señal(15)
    }

    # Tamaño calculado en Python según escala SII — no lo decide la IA
    tamaño_label, tamaño_pts = _clasificar_tamaño(tramo)

    prompt = f"""Eres un analista de ICP para Plutto, plataforma de compliance y KYB B2B en Chile.

    Plutto ayuda a empresas a evaluar proveedores, clientes y colaboradores mediante debida diligencia y detección de riesgos.

Empresa: {company_name}
RUT: {rut}
Industria/Giro: {giro}
Tamaño empresa: {tamaño_label} (tramo SII {tramo}) → ya asignado {tamaño_pts} puntos, NO lo calcules
Región: {region}
Señal (si aplica): {signal}

TAREA: Clasifica la empresa en un segmento y asigna los puntos de los criterios restantes. 
Si el segmento ya viene NO lo calcules, si el segmento no es Financiero, Mining/Energía, Utilities/Infraestructura, Manofactura Retail. Dejalo como otro. 
NO calcules tamaño (ya está calculado) No calcules el score total.

CRITERIOS POR SEGMENTO:

Financiero → reg(0-40), proveedores(0-20), señal(0-15), competencia(0 o -10)
  - reg: 40 si regulada por CMF con obligación de verificar contrapartes, si no 0
  - proveedores: >200=+20, 50-200=+12, <50=+5
  - señal: licitación/multa=+15, M&A=+12, nueva regulación=+10, ninguna=0
  - competencia: usa Regcheq/LexisNexis/KPMG/manual=-10, desconocido=0

Mining/Energía → reg(0-30), proveedores(0-30), señal(0-15), competencia(0 o -10)
  - reg: 30 si regulada por Sernageomin/SEC/CNE, si no 0
  - proveedores: >200=+30, 50-200=+15, <50=+5
  - señal: licitación/multa=+15, M&A=+12, nueva regulación=+10, ninguna=0
  - competencia: usa Regcheq/LexisNexis/KPMG/manual=-10, desconocido=0

Utilities/Infraestructura → reg(0-30), proveedores(0-25), señal(0-15), competencia(0 o -10)
  - reg: 30 si regulada por SISS/SEC, si no 0
  - proveedores: >200=+25, 50-200=+15, <50=+5
  - señal: licitación/multa=+15, M&A=+12, nueva regulación=+10, ninguna=0
  - competencia: usa Regcheq/LexisNexis/KPMG/manual=-10, desconocido=0

Manufactura/Retail → reg(0-20), proveedores(0-35), señal(0-15), competencia(0 o -10)
  - reg: 20 si regulada, si no 0
  - proveedores: >200=+35, 50-200=+20, <50=+5
  - señal: licitación/multa=+15, M&A=+12, nueva regulación=+10, ninguna=0
  - competencia: usa Regcheq/LexisNexis/KPMG/manual=-10, desconocido=0

 Otro → reg(0-30), proveedores(0-25), señal(0-15), competencia(0 o -10)
  - reg: 30 si regulada por SISS/SEC, si no 0
  - proveedores: >200=+25, 50-200=+15, <50=+5
  - señal: licitación/multa=+15, M&A=+12, nueva regulación=+10, ninguna=0
  - competencia: usa Regcheq/LexisNexis/KPMG/manual=-10, desconocido=0


Responde SOLO con este JSON:
{{
  "vertical": "<financiero|mining|utilities|manufactura|otro>",
  "puntos": {{
    "regulacion": <número>,
    "proveedores": <número>,
    "señal": <número>,
    "competencia": <número>
  }},
  "pain_point": "<dolor potencial de la empresa principal en español que pluto puede resolver>",
  "reasoning": "<explicación breve de los puntos asignados, >"
}}"""

    response = client.chat.completions.create(
        model="claude-haiku-4-5-20251001",
        max_tokens = 300,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.choices[0].message.content.strip()
    raw = json.loads(text[text.find("{") : text.rfind("}") + 1])

    vertical = raw["vertical"].lower()
    p = raw["puntos"]
    puntos = {                          # orden fijo: reg → prov → tamaño → señal → comp
        "regulacion":  p.get("regulacion", 0),
        "proveedores": p.get("proveedores", 0),
        "tamaño":      tamaño_pts,
        "señal":       p.get("señal", 0),
        "competencia": p.get("competencia", 0),
    }
    max_seg  = MAX_SEGMENTO.get(vertical, 90)
    score    = round(max(0, min(100, sum(puntos.values()) / max_seg * 100)))

    return {
        "score":      score,
        "vertical":   raw["vertical"],
        "pain_point": raw["pain_point"],
        "reasoning":  raw["reasoning"],
    }


def print_score(resultado: dict, company_name: str = "") -> None:
    print(f"\nEmpresa:     {company_name}")
    print(f"Vertical:    {resultado.get('vertical', '?')}")
    print(f"Score total: {resultado.get('score', '?')}/100")
    if "puntos" in resultado:
        for k, v in resultado["puntos"].items():
            print(f"  {k:<15} {v:+}")
    print(f"Pain point:  {resultado.get('pain_point', '?')}")
    print(f"Resumen:     {resultado.get('reasoning', '?')}\n")


def score_lead_lookup(rut: str, signal: str = "N/A") -> dict:
    url = f"https://charon-staging.herokuapp.com/api/businesses/{rut}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
    except Exception:
        raise ValueError(f"No se pudo conectar a la API para el RUT '{rut}'.")

    if not data:
        raise ValueError(f"RUT '{rut}' no encontrado. Verifica que sea válido.")

    biz = data[0]

    nombre = biz["name"]
    giro   = biz["economic_activity"]
    tramo  = str(biz["sales_segment"])
    region = biz["region"]

    return score_lead_adj1(
        company_name = nombre,
        rut          = rut,
        giro         = giro,
        tramo        = tramo,
        region       = region,
        signal       = signal,
    )



# ── Ejemplo de uso ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    empresa = "Tecnofast"
    params = dict(
        company_name=empresa,
        rut="76320186-4",
        giro="TERMINACION Y ACABADO DE EDIFICIOS",
        tramo="13",
        region="XIII REGION METROPOLITANA",
        signal="",
    )

    print("── score_lead ──────────────────────────────────────")
    print_score(score_lead(**params), empresa)

    print("── score_lead_adj1 ─────────────────────────────────")
    print_score(score_lead_adj1(**params), empresa)

    # print("── score_lead_lookup (solo RUT) ────────────────────")
    # print_score(score_lead_lookup("59141000"), "DNB GROUP AGENCIA EN CHILE")

    # print("── score_lead_lookup (solo nombre) ─────────────────")
    # print_score(score_lead_lookup("Esbbio"), "Esbbio")
