import json
import re
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

def _puntaje_holding(hijos) -> int:
    """Puntaje por estructura holding según cantidad de hijas."""
    try:
        h = int(hijos)
    except (ValueError, TypeError):
        return 0

    if h >= 3:
        return 15
    if h == 2:
        return 8
    if h == 1:
        return 3
    return 0

def _puntaje_trabajadores(numero_trabajadores) -> int:
    """Puntaje por cantidad de trabajadores."""
    try:
        n = int(numero_trabajadores)
    except (ValueError, TypeError):
        return 0

    if n > 1000:
        return 10
    if 500 <= n <= 1000:
        return 8
    if 200 <= n < 500:
        return 5
    if 50 <= n < 200:
        return 3
    return 0

def _puntaje_segmento(vertical: str) -> int:
    """Puntaje por vertical/segmento de la empresa."""
    v = vertical.strip().lower()
    if v in ("mining/energía", "utilities/infraestructura", "manufactura retail"):
        return 5
    if v == "financiero":
        return 3
    return 0

def _extraer_json(texto: str) -> dict:
    """Extrae el primer bloque JSON válido desde un texto."""
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if not match:
        raise ValueError("La respuesta de la LLM no contiene un JSON válido.")
    return json.loads(match.group(0))


def score_lead_adj2(
    company_name: str,
    rut: str,
    giro: str,
    tramo: str,
    region: str,
    num_hijos: str,
    num_trabajadores: str,
    num_padres: str,
    signal: str = "N/A",
):
    """Calcula score ICP 0-100 para Plutto.

    Componentes calculados en Python:
    - tamaño
    - holding
    - trabajadores

    Componentes estimados por LLM:
    - vertical (solo si giro no viene ya clasificado)
    - regulacion
    - proveedores
    - pain_point
    - reasoning
    """

    def _normalizar_vertical(giro_texto: str) -> str | None:
        if not giro_texto:
            return None

        g = giro_texto.strip().lower()

        validos = {
            "financiero": "Financiero",
            "mining/energía": "Mining/Energía",
            "mining / energía": "Mining/Energía",
            "utilities/infraestructura": "Utilities/Infraestructura",
            "utilities / infraestructura": "Utilities/Infraestructura",
            "manufactura retail": "Manufactura Retail",
            "manufactura/retail": "Manufactura Retail",
            "manufactura / retail": "Manufactura Retail",
            "otro": "Otro",
        }

        if g in validos:
            return validos[g]

        return None

    tamaño_label, tamaño_pts = _clasificar_tamaño(tramo)
    holding_pts = _puntaje_holding(num_hijos)
    trabajadores_pts = _puntaje_trabajadores(num_trabajadores)

    # Máximo bruto posible:
    # tamaño (20) + holding (15) + trabajadores (10) + regulacion (15) + proveedores (25) + segmento (5) + señal (10) = 100
    MAX_RAW_SCORE = 100

    vertical_detectado = _normalizar_vertical(giro)

    if vertical_detectado is not None:
        vertical_instruccion = f"""
El giro ya viene clasificado en un vertical válido.
Vertical: {vertical_detectado}

NO reclasifiques el vertical.
Devuélvelo exactamente igual en el campo "vertical".
""".strip()
    else:
        vertical_instruccion = f"""
El campo giro NO viene clasificado en uno de los verticales válidos.

Debes clasificar la empresa en UNO de estos verticales exactos:
- Financiero
- Mining/Energía
- Utilities/Infraestructura
- Manufactura Retail
- Otro

Usa el nombre de la empresa y el giro para inferirlo.
""".strip()

    prompt = f"""
Eres un analista de ICP para Plutto, plataforma de compliance y KYB B2B en Chile.

Plutto ayuda a empresas a evaluar proveedores, clientes y colaboradores mediante debida diligencia y detección de riesgos.

Datos disponibles de la empresa:
- Razón social: {company_name}
- RUT: {rut}
- Giro: {giro}
- Tramo SII: {tramo}
- Región: {region}
- Cantidad de empresas hijas: {num_hijos}
- Cantidad de empresas padres: {num_padres}
- Número de trabajadores: {num_trabajadores}
- Tamaño empresa ya calculado en Python: {tamaño_label} = {tamaño_pts} puntos
- Señal externa (si aplica): {signal}

{vertical_instruccion}

TAREA:
Con esta información, estima SOLO los siguientes campos:

1. vertical:
- Si ya venía clasificado, devuélvelo igual.
- Si no venía clasificado, clasifícalo en una de estas opciones exactas:
  - Financiero
  - Mining/Energía
  - Utilities/Infraestructura
  - Manufactura Retail
  - Otro

2. Regulación:
Determina si la empresa probablemente está regulada por alguna entidad externa relevante como:
- CMF
- Sernageomin
- SEC
- CNE

Puntaje:
- Si sí: 15
- Si no: 0

3. Cantidad aproximada de proveedores:
Estima la cantidad de proveedores que probablemente maneja la empresa.

Puntaje:
- >200 proveedores = 25
- 50-200 proveedores = 15
- <50 proveedores = 0

4. Señal externa:
Evalúa la señal proporcionada. Considera fuerte: multa regulatoria, licitación adjudicada, M&A, cambio de gerencia/directorio, nueva regulación. Considera débil: menciones en prensa sin urgencia, expansión vaga, interés genérico.

Puntaje:
- Señal fuerte (multa, licitación, M&A, nuevo gerente/directorio): 10
- Señal débil: 5
- Sin señal o N/A: 0

5. pain_point:
Un dolor potencial en español que Plutto podría resolver.

6. reasoning:
Explicación breve en español de por qué asignaste esos puntos.

Responde SOLO con este JSON:
{{
  "vertical": "Otro",
  "puntos": {{
    "regulacion": 0,
    "proveedores": 0,
    "señal": 0
  }},
  "pain_point": "texto",
  "reasoning": "texto"
}}
""".strip()

    response = client.chat.completions.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=350,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.choices[0].message.content.strip()
    raw = _extraer_json(text)

    vertical_raw = raw.get("vertical", "Otro")
    vertical_validos = {
        "financiero": "Financiero",
        "mining/energía": "Mining/Energía",
        "mining / energía": "Mining/Energía",
        "utilities/infraestructura": "Utilities/Infraestructura",
        "utilities / infraestructura": "Utilities/Infraestructura",
        "manufactura retail": "Manufactura Retail",
        "manufactura/retail": "Manufactura Retail",
        "manufactura / retail": "Manufactura Retail",
        "otro": "Otro",
    }

    vertical_final = vertical_validos.get(str(vertical_raw).strip().lower(), "Otro")

    # Si ya venía clasificado válido, mandamos ese por sobre la IA
    if vertical_detectado is not None:
        vertical_final = vertical_detectado

    segmento_pts = _puntaje_segmento(vertical_final)

    llm_puntos = raw.get("puntos", {})
    regulacion_pts = llm_puntos.get("regulacion", 0)
    proveedores_pts = llm_puntos.get("proveedores", 0)
    señal_pts = llm_puntos.get("señal", 0)

    # Validación defensiva
    if regulacion_pts not in (0, 15):
        regulacion_pts = 0

    if proveedores_pts not in (0, 15, 25):
        proveedores_pts = 0

    if señal_pts not in (0, 5, 10):
        señal_pts = 0

    puntos = {
        "tamaño": tamaño_pts,
        "holding": holding_pts,
        "trabajadores": trabajadores_pts,
        "regulacion": regulacion_pts,
        "proveedores": proveedores_pts,
        "segmento": segmento_pts,
        "señal": señal_pts,
    }

    raw_score = sum(puntos.values())
    score = round((raw_score / MAX_RAW_SCORE) * 100)

    return {
        "score": score,
        "vertical": vertical_final,
        "puntos": puntos,
        "pain_point": raw.get("pain_point", ""),
        "reasoning": raw.get("reasoning", ""),
    }


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
        max_tokens = 500,
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



def get_company_data(rut: str) -> dict:
    """
    Consulta Charon y devuelve campos clave de la empresa.
    No usa IA — consulta directamente el endpoint REST.
    """
    url  = f"https://charon-staging.herokuapp.com/api/businesses/{rut}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        raise ValueError(f"RUT '{rut}' no encontrado.")

    biz = data[0]

    addresses     = biz.get("addresses") or []
    num_addresses = len(addresses) if isinstance(addresses, list) else 0

    return {
        "tin":               biz.get("TIN") or rut,
        "name":              biz.get("name") or "",
        "economic_activity": biz.get("economic_activity") or "",
        "direct_employees":  biz.get("direct_employees"),
        "num_addresses":     num_addresses,
    }


# ── Ejemplo de uso ────────────────────────────────────────────────────────────
if __name__ == "__main__":

    # Parámetros para funciones antiguas
    params_base = dict(
    company_name="CHITA SPA",
    rut="76596744-9",
    giro="OTRAS ACTIVIDADES DE SERVICIOS FINANCIEROS, EXCEPTO LAS DE SEGUROS Y FONDOS DE PENSIONES N.C.P.",
    tramo="10",
    region="XIII REGION METROPOLITANA",
    signal="",
)

# Parámetros para la nueva función ajustada
    params_adj2 = dict(
    company_name="CHITA SPA",
    rut="76596744-9",
    giro="OTRAS ACTIVIDADES DE SERVICIOS FINANCIEROS, EXCEPTO LAS DE SEGUROS Y FONDOS DE PENSIONES N.C.P.",
    tramo="10",
    region="XIII REGION METROPOLITANA",
    num_hijos="5",
    num_trabajadores="65",
    num_padres="0",
    signal="" )

    print("── score_lead ──────────────────────────────────────")
    print_score(score_lead(**params_base), company_name=params_base["company_name"])

    print("── score_lead_adj1 ─────────────────────────────────")
    print_score(score_lead_adj1(**params_base), company_name=params_base["company_name"])

    print("── score_lead_adj2 ─────────────────────────────────")
    print_score(score_lead_adj2(**params_adj2), company_name=params_adj2["company_name"])

    # print("── score_lead_lookup (solo RUT) ────────────────────")
    # print_score(score_lead_lookup("59141000"), "DNB GROUP AGENCIA EN CHILE")

    print("── get_company_data ────────────────────────────────")
    data = get_company_data("76320186-4")
    for k, v in data.items():
        print(f"  {k:<20} {v}")

    # print("── score_lead_lookup (solo nombre) ─────────────────")
    # print_score(score_lead_lookup("Esbbio"), "Esbbio")


