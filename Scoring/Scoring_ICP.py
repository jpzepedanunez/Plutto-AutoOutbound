import json
import re
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus
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


_VERTICALES_VALIDOS = {
    "FSI-REG", "GAS-COMBUSTIBLE", "MFG-RETAIL-HOLD",
    "MINING-HSE", "POWER-EPC", "UTIL-INFRA",
}

_CONTEXT_SEGMENTOS = """
Plutto tiene 6 verticales de clientes objetivo. Clasifica la empresa en el más adecuado:

FSI-REG — Financiero / Seguros / Fintech regulado
  Banca, aseguradoras, isapres, AFP, cajas de compensación, fintech reguladas CMF.
  Reguladas por CMF, UAF, SUSESO o Superintendencia de Salud.
  Ejemplos: Banco Ripley, Mapfre, Cruz Blanca, Fintual, Cumplo.

GAS-COMBUSTIBLE — Gas / Combustibles / Distribución energética
  Distribuidoras de GLP y gas natural, operadores de hidrocarburos.
  CIIU 3520, 4661 o 4930. Base de proveedores >500.
  Ejemplos: Abastible, Lipigas, Gasco, Copec Gas, Sonacol.

MFG-RETAIL-HOLD — Manufactura / Retail / Agro / Food / Holdings
  Manufactureras, retailers, agroindustriales, holdings familiares/corporativos.
  CIIU 10, 11, 46, 47, 01. No reguladas CMF en primera línea.
  Ejemplos: Soprole, Carozzi, CCU, Santa Rita, Arauco, Salcobrand.

MINING-HSE — Minería + contratistas HSE-críticos
  Gran minería del cobre, litio, oro, hierro, molibdeno. Filiales de multinacionales cotizadas.
  CIIU 0710–0729. Facturaciòn >UF 1M/año.
  Ejemplos: Codelco, BHP, SQM, Antofagasta Minerals, Teck.

POWER-EPC — Generación / Transmisión Eléctrica / EPC
  Generadoras de energía renovable, transmisoras eléctricas, EPC de infraestructura eléctrica.
  Frecuentemente filiales de fondos o multinacionales con project finance.
  Ejemplos: Engie Chile, AES Andes, Enel Green Power, ISA Interchile, Statkraft.

UTIL-INFRA — Utilities / Sanitarias / Infraestructura / Telecomunicaciones / Puertos
  Sanitarias reguladas, telcos, concesionarias MOP, constructoras con contratos públicos,
  operadores portuarios y aeroportuarios.
  CIIU 3600/3700 (sanitarias), 6110–6190 (telcos).
  Ejemplos: Aguas Andinas, Entel, Movistar, SAAM, Besalco, Nuevo Pudahuel.
""".strip()


def clasificar_vertical(
    company_name: str,
    giro: str,
    vertical_actual: str = "",
) -> dict:
    """
    Clasifica una empresa en uno de los 6 verticales de Plutto usando LLM.

    Parámetros:
    - company_name:   Razón social o nombre comercial de la empresa
    - giro:           Giro o actividad económica (SII o descripción libre)
    - vertical_actual: Vertical asignado previamente (opcional). Si es válido,
                       el LLM decide si confirmarlo o corregirlo.

    Retorna dict con:
    - vertical:   Uno de FSI-REG | GAS-COMBUSTIBLE | MFG-RETAIL-HOLD |
                  MINING-HSE | POWER-EPC | UTIL-INFRA
    - confianza:  Alta | Media | Baja
    - razon:      Explicación breve en español
    - cambiado:   True si se corrigió el vertical_actual, False si se confirmó
    """
    if vertical_actual and vertical_actual.upper() in _VERTICALES_VALIDOS:
        instruccion_actual = (
            f"El vertical asignado actualmente es '{vertical_actual}'. "
            f"Confírmalo si es correcto o cámbialo si hay evidencia clara de otro vertical."
        )
    else:
        instruccion_actual = "No hay vertical asignado previamente. Clasifica desde cero."

    prompt = f"""Eres un analista de ICP para Plutto, plataforma de compliance y KYB B2B en Chile.

{_CONTEXT_SEGMENTOS}

Empresa a clasificar:
- Nombre: {company_name or "Sin nombre"}
- Giro / Actividad económica: {giro or "Sin giro"}

{instruccion_actual}

Reglas:
1. Elige EXACTAMENTE uno de los 6 verticales tal como aparece escrito arriba.
2. Si el giro corresponde a gas/combustibles, usa GAS-COMBUSTIBLE aunque también sea distribución.
3. Si es minería o contratista crítico de minería, usa MINING-HSE.
4. Si es generación o transmisión eléctrica o EPC de energía, usa POWER-EPC.
5. Si no encaja en ninguno, usa MFG-RETAIL-HOLD.
6. Confianza: Alta=giro explícito, Media=inferido por nombre/industria, Baja=suposición.

Responde SOLO con este JSON:
{{"vertical": "MFG-RETAIL-HOLD", "confianza": "Media", "razon": "texto breve"}}"""

    try:
        response = client.chat.completions.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content.strip()
        raw = _extraer_json(text)
    except Exception as e:
        return {
            "vertical": vertical_actual or "MFG-RETAIL-HOLD",
            "confianza": "Baja",
            "razon": f"Error al clasificar: {e}",
            "cambiado": False,
        }

    vertical_nuevo = str(raw.get("vertical", "")).strip().upper()
    if vertical_nuevo not in _VERTICALES_VALIDOS:
        vertical_nuevo = vertical_actual or "MFG-RETAIL-HOLD"

    # Preservar el casing oficial del vertical
    vertical_final = next(
        (v for v in _VERTICALES_VALIDOS if v.upper() == vertical_nuevo),
        "MFG-RETAIL-HOLD",
    )

    return {
        "vertical":   vertical_final,
        "confianza":  raw.get("confianza", "Baja"),
        "razon":      raw.get("razon", ""),
        "cambiado":   bool(
            vertical_actual
            and vertical_actual.upper() in _VERTICALES_VALIDOS
            and vertical_actual.upper() != vertical_nuevo
        ),
    }



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
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.choices[0].message.content.strip()
    try:
        raw = _extraer_json(text)
    except Exception:
        print(f"\n⚠️  LLM no devolvió JSON. Respuesta cruda:\n{text}\n")
        # LLM no devolvió JSON válido — usar defaults seguros
        raw = {
            "vertical": vertical_detectado or "Otro",
            "puntos": {"regulacion": 0, "proveedores": 0, "señal": 0},
            "pain_point": "",
            "reasoning": f"[parse error — respuesta LLM no era JSON: {text[:120]}]",
        }

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


# ── Prefijos genéricos que preceden al nombre real ────────────────────────────
_PREFIJOS = re.compile(
    r'^(SOCIEDAD\s+(DE\s+)?|SOCIEDAD\s+COMERCIAL\s+|SOCIEDAD\s+CONTRACTUAL\s+'
    r'|SOCIEDAD\s+AGRICOLA\s+|INVERSIONES\s+|SERVICIOS\s+|DISTRIBUIDORA\s+'
    r'|CONSTRUCTORA\s+|TRANSPORTES?\s+|INGENIERIA\s+(Y\s+PROYECTOS\s+)?'
    r'|COMERCIAL\s+|INDUSTRIAL\s+|AGRICOLA\s+|MINERA\s+)',
    re.IGNORECASE
)

_SUFIJOS = re.compile(
    r'\s+(S\.?A\.?|SpA|LTDA\.?|LIMITADA|S\.?R\.?L\.?|E\.?I\.?R\.?L\.?'
    r'|Y\s+CIA\.?\s*LTDA\.?|Y\s+COMPANIA\s+LIMITADA|AGENCIA\s+EN\s+CHILE'
    r'|SUCURSAL\s+CHILE)\s*$',
    re.IGNORECASE
)

_PALABRAS_GENERICAS = re.compile(
    r'^(INVERSIONES|SOCIEDAD|COMERCIAL|SERVICIOS|EMPRESA|COMPANIA|CIA'
    r'|DISTRIBUIDORA|CONSTRUCTORA|TRANSPORTES?|INGENIERIA|AGRICOLA'
    r'|INDUSTRIAL|MINERA|MAYORISTA|LOGISTICOS?)\s*$',
    re.IGNORECASE
)


def _es_nombre_persona(razon: str) -> bool:
    limpio = _SUFIJOS.sub('', razon).strip()
    palabras = limpio.split()
    palabras_comerciales = {
        'TRANSPORTES', 'SERVICIOS', 'CONSTRUCTORA', 'INVERSIONES',
        'COMERCIAL', 'DISTRIBUIDORA', 'INGENIERIA', 'AGRICOLA', 'MINERA',
        'INDUSTRIAL', 'LOGISTICA', 'CONSULTORA', 'TECNOLOGIA',
    }
    tiene_comercial = any(p.upper() in palabras_comerciales for p in palabras)
    if not tiene_comercial and len(palabras) >= 3:
        return True
    return False


def _extraer_nombre_desde_razon(razon: str) -> str:
    limpio = _SUFIJOS.sub('', razon).strip()
    sin_prefijo = _PREFIJOS.sub('', limpio).strip(' .,')
    if not sin_prefijo or _PALABRAS_GENERICAS.match(sin_prefijo):
        sin_prefijo = limpio
    palabras = sin_prefijo.split()
    if len(palabras) <= 4:
        return sin_prefijo.title()
    return ""


def _detectar_acronimo(razon: str) -> str | None:
    palabras = [p for p in razon.upper().split()
                if p not in ('DE', 'DEL', 'LA', 'LOS', 'LAS', 'Y', 'E',
                             'S.A.', 'SPA', 'LTDA', 'LIMITADA')]
    if len(palabras) < 3:
        return None
    acronimo = ''.join(p[0] for p in palabras if p[0].isalpha())
    if re.search(r'\b' + re.escape(acronimo) + r'\b', razon.upper()) and len(acronimo) >= 3:
        return acronimo
    return None


def _google_search_urls(query: str, n: int = 3) -> list[str]:
    ignorar = {
        'google.com', 'youtube.com', 'facebook.com', 'linkedin.com',
        'wikipedia.org', 'mercantil.com', 'buscaempresas.cl', 'empresas.cl',
        'sii.cl', 'genealog.cl', 'portalchile.org', 'direcmin.com',
        'panjiva.com', 'emol.com', 'latercera.com', 'biobiochile.cl',
    }
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "es-CL,es;q=0.9",
        }
        url = f"https://www.google.com/search?q={quote_plus(query)}&hl=es&gl=cl&num=10"
        r = requests.get(url, headers=headers, timeout=6)
        soup = BeautifulSoup(r.text, "html.parser")

        urls = []
        for a in soup.select('a[href^="/url?q="]'):
            href = a['href']
            match = re.search(r'/url\?q=(https?://[^&]+)', href)
            if not match:
                continue
            link = match.group(1)
            dominio = re.search(r'https?://(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z]{2,})', link)
            if not dominio:
                continue
            if dominio.group(1) not in ignorar:
                urls.append(link)
            if len(urls) >= n:
                break
        return urls
    except Exception:
        return []


def _ddg_search(query: str) -> str:
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": "1", "no_html": "1"},
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = r.json()
        partes = []
        if data.get("AbstractText"):
            partes.append(f"Descripción: {data['AbstractText']}")
        if data.get("AbstractURL"):
            partes.append(f"URL: {data['AbstractURL']}")
        for topic in (data.get("RelatedTopics") or [])[:3]:
            if isinstance(topic, dict) and topic.get("FirstURL"):
                partes.append(f"{topic.get('Text', '')} → {topic['FirstURL']}")
        return "\n".join(partes)
    except Exception:
        return ""


def _fetch_title_and_text(url: str, max_chars: int = 800) -> str:
    try:
        r = requests.get(url, timeout=5, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code >= 400:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.string.strip() if soup.title else ""
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        body = " ".join(soup.get_text(separator=" ").split())[:max_chars]
        return f"[Title: {title}]\n{body}" if title else body
    except Exception:
        return ""


def _validar_url(url: str) -> bool:
    try:
        r = requests.head(url, timeout=4, allow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code < 400
    except Exception:
        return False


def _recolectar_contexto(rut: str, razon_social: str) -> tuple[str, str]:
    nombre_candidato = _extraer_nombre_desde_razon(razon_social)
    acronimo = _detectar_acronimo(razon_social)

    queries_ddg = [f"{rut} Chile empresa", f'"{razon_social}" Chile']
    if nombre_candidato:
        queries_ddg.append(f'"{nombre_candidato}" Chile sitio web')
    if acronimo:
        queries_ddg.append(f"{acronimo} empresa Chile sitio web oficial")

    partes_ddg = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futuros = {ex.submit(_ddg_search, q): q for q in queries_ddg}
        for f in as_completed(futuros):
            r = f.result()
            if r:
                partes_ddg.append(r)

    contexto_ddg = "\n".join(partes_ddg)

    query_google = (
        f'"{nombre_candidato}" Chile sitio web oficial'
        if nombre_candidato
        else f'"{razon_social}" Chile sitio web'
    )
    urls_google = _google_search_urls(query_google, n=3)

    contexto_web = contexto_ddg
    fuente = "DuckDuckGo"

    if urls_google:
        fuente = "Google + fetch"
        urls_str = "\n".join(f"URL candidata: {u}" for u in urls_google)
        contexto_web = contexto_ddg + "\n" + urls_str

        texto_site = _fetch_title_and_text(urls_google[0])
        if texto_site:
            contexto_web += f"\nContenido del sitio:\n{texto_site}"

    return contexto_web, fuente


def _lookup_con_gemini_search(rut: str, razon_social: str) -> dict | None:
    """
    Usa Gemini 2.5 Flash con Google Search grounding para encontrar nombre
    comercial y sitio web. Retorna dict con los campos estándar o None si falla.
    """
    prompt = (
        f"Eres un experto en empresas chilenas. Busca en internet información sobre "
        f"la empresa con razón social '{razon_social}' (RUT: {rut}).\n\n"
        f"Determina:\n"
        f"1. Nombre comercial o de fantasía (ej: 'Coca-Cola Andina' para "
        f"'EMBOTELLADORA ANDINA S.A.'). Si la razón social ya es el nombre comercial, "
        f"ponlo igual en formato Title Case.\n"
        f"2. Sitio web oficial (https://, dominio raíz, prefiere .cl).\n\n"
        f"Reglas:\n"
        f"- Si la razón social es 'PREFIJO NOMBRE SUFIJO S.A./SPA/LTDA', "
        f"el nombre comercial suele ser solo NOMBRE.\n"
        f"- Si el prefijo describe el negocio (ej: TRANSPORTES NAHUELBUTA), "
        f"úsalo como nombre comercial en Title Case.\n"
        f"- Confianza: Alta=confirmado en web, Media=inferido con buena lógica, "
        f"Baja=suposición.\n"
        f"- Si no encuentras sitio web, usa null.\n\n"
        f"Responde SOLO con este JSON (sin texto adicional):\n"
        f'{{"Rut":"{rut}","Razon_Social":"{razon_social}",'
        f'"Nombre_Fantasia":null,"Sitio_Web":null,"Confianza":"Baja","Fuente":"Gemini Search"}}'
    )
    try:
        response = client.chat.completions.create(
            model="gemini-2.5-flash",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"google_search": {}}],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return None
        result = _extraer_json(text)
        result["Fuente"] = "Gemini Search"
        return result
    except Exception:
        return None


def lookup_empresa(rut: str, razon_social: str) -> dict:
    """
    Busca nombre de fantasía y sitio web de una empresa chilena.
    Estrategia: Gemini Search (Google grounding) → fallback scraping manual + LLM.
    """
    if len(razon_social.strip()) < 10 or razon_social.strip().endswith(('DE', 'Y', 'DE LA')):
        return {
            "Rut": rut, "Razon_Social": razon_social,
            "Nombre_Fantasia": None, "Sitio_Web": None,
            "Confianza": "Baja", "Fuente": "razón social truncada",
        }

    if _es_nombre_persona(razon_social):
        return {
            "Rut": rut, "Razon_Social": razon_social,
            "Nombre_Fantasia": None, "Sitio_Web": None,
            "Confianza": "Alta", "Fuente": "nombre persona natural",
        }

    # Intento 1: Gemini con Google Search grounding
    resultado_gemini = _lookup_con_gemini_search(rut, razon_social)
    if resultado_gemini and resultado_gemini.get("Nombre_Fantasia"):
        sitio = resultado_gemini.get("Sitio_Web")
        if sitio and isinstance(sitio, str) and sitio.startswith("http"):
            if not _validar_url(sitio):
                resultado_gemini["Sitio_Web"] = None
                resultado_gemini["Confianza"] = "Media"
        return resultado_gemini

    # Intento 2: fallback con scraping manual + Claude
    nombre_candidato = _extraer_nombre_desde_razon(razon_social)
    acronimo = _detectar_acronimo(razon_social)
    contexto, fuente = _recolectar_contexto(rut, razon_social)

    hints = []
    if nombre_candidato:
        hints.append(
            f"- Nombre candidato extraído de la razón social: '{nombre_candidato}'. "
            f"Úsalo si no hay evidencia de otro nombre en el contexto."
        )
    if acronimo:
        hints.append(f"- Acrónimo detectado: '{acronimo}'.")

    prompt = (
        f"Eres un experto en empresas chilenas. Identifica el nombre comercial "
        f"(nombre de fantasía) y sitio web oficial.\n\n"
        f"Empresa:\n  Razón social: {razon_social}\n  RUT: {rut}\n"
        f"\nContexto web (fuente: {fuente}):\n{contexto}\n"
        f"\nPistas:\n" + "\n".join(hints) + "\n\n"
        f"Reglas:\n"
        f"1. Si la razón social es 'PREFIJO NOMBRE SUFIJO' (ej: SOCIEDAD COMERCIAL TENAUN SPA), "
        f"el nombre de fantasía es NOMBRE (Tenaun).\n"
        f"2. Si el prefijo ES el descriptor del negocio (ej: TRANSPORTES NAHUELBUTA), "
        f"el nombre de fantasía puede incluir el prefijo (Transportes Nahuelbuta) "
        f"o solo el nombre propio (Nahuelbuta) — usa lo que aparezca en el contexto web.\n"
        f"3. Si el contexto web muestra un dominio o title distinto a la razón social, "
        f"ese es el nombre de fantasía y/o el sitio web.\n"
        f"4. Sitio web: URL con https://, prefiere .cl. Sin directorios.\n"
        f"5. Confianza: Alta=confirmado, Media=inferido con buena lógica, Baja=suposición.\n\n"
        f"Responde SOLO con JSON:\n"
        f'{{"Rut":"{rut}","Razon_Social":"{razon_social}",'
        f'"Nombre_Fantasia":null,"Sitio_Web":null,"Confianza":"Baja","Fuente":"{fuente}"}}'
    )

    response = client.chat.completions.create(
        model="claude-4-6-sonnet",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.choices[0].message.content.strip()
    try:
        result = _extraer_json(text)
    except Exception:
        return {
            "Rut": rut, "Razon_Social": razon_social,
            "Nombre_Fantasia": nombre_candidato or None,
            "Sitio_Web": None, "Confianza": "Baja", "Fuente": fuente,
        }

    sitio = result.get("Sitio_Web")
    if sitio and isinstance(sitio, str) and sitio.startswith("http"):
        if not _validar_url(sitio):
            result["Sitio_Web"] = None
            result["Confianza"] = "Baja"

    return result


def _normalizar_rut(rut: str) -> list[str]:
    """
    Genera variantes del RUT para probar en la API.
    Ej: '76596744-9' → ['76596744-9', '765967449', '76596744']
    """
    rut = rut.strip()
    variantes = [rut]
    # Sin guión
    sin_guion = rut.replace("-", "")
    if sin_guion not in variantes:
        variantes.append(sin_guion)
    # Sin dígito verificador (últimos 1-2 chars tras guión o sin él)
    if "-" in rut:
        cuerpo = rut.split("-")[0]
        if cuerpo not in variantes:
            variantes.append(cuerpo)
    elif len(rut) >= 8:
        cuerpo = rut[:-1]
        if cuerpo not in variantes:
            variantes.append(cuerpo)
    return variantes


def _charon_fetch(rut: str, retries: int = 3) -> dict | None:
    """
    Intenta obtener datos de Charon probando variantes del RUT.
    Retorna el dict del negocio o None si no se encontró / falló.
    """
    import time as _time

    for variante in _normalizar_rut(rut):
        url = f"https://charon-staging.herokuapp.com/api/businesses/{variante}"
        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, timeout=20)
                if resp.status_code == 404:
                    break  # esta variante no existe, probar la siguiente
                resp.raise_for_status()
                data = resp.json()
                if data:
                    return data[0]
                break  # respuesta vacía, probar siguiente variante
            except ValueError:
                break
            except Exception as e:
                last_exc = e
                if attempt < retries:
                    _time.sleep(1.5 * attempt)
    return None


def score_rut(rut: str, signal: str = "N/A") -> dict:
    """
    Dado un RUT, obtiene datos desde Charon y retorna el scoring completo.
    Si Charon no tiene el RUT, produce un score de mejor esfuerzo con los
    datos disponibles (score calculable, pero sin tramo/giro/región reales).

    Parámetros opcionales:
    - signal: señal externa de la empresa (ej: "licitación adjudicada")
    """
    biz = _charon_fetch(rut)

    if biz:
        nombre           = biz.get("name") or ""
        giro             = biz.get("economic_activity") or ""
        tramo            = str(biz.get("sales_segment") or "0")
        region           = biz.get("region") or ""
        num_trabajadores = str(biz.get("direct_employees") or 0)
        num_hijos        = str(len(biz.get("subsidiaries") or []))
        num_padres       = str(len(biz.get("parents") or []))
        charon_ok        = True
    else:
        # Degradación elegante: score parcial con datos vacíos
        nombre           = ""
        giro             = ""
        tramo            = "0"
        region           = ""
        num_trabajadores = "0"
        num_hijos        = "0"
        num_padres       = "0"
        charon_ok        = False

    resultado = score_lead_adj2(
        company_name     = nombre,
        rut              = rut,
        giro             = giro,
        tramo            = tramo,
        region           = region,
        num_hijos        = num_hijos,
        num_trabajadores = num_trabajadores,
        num_padres       = num_padres,
        signal           = signal,
    )

    resultado["company_name"] = nombre
    resultado["rut"]          = rut
    resultado["giro"]         = giro
    resultado["tramo"]        = tramo
    resultado["region"]       = region
    resultado["charon_ok"]    = charon_ok  # flag para saber si los datos son completos

    return resultado


def score_ruts(ruts: list[str], signal: str = "N/A", max_workers: int = 3) -> list[dict]:
    """
    Dado una lista de RUTs, retorna el scoring adj2 de cada uno en paralelo.

    Parámetros:
    - ruts:        lista de RUTs (ej: ["76596744-9", "78383730-7"])
    - signal:      señal externa aplicada a todos (opcional)
    - max_workers: hilos paralelos (default 3; reducido para no saturar Charon)

    Retorna lista de dicts con los campos de score_rut, más:
    - "error": mensaje de error si falló ese RUT (en vez de score)

    Ejemplo de uso:
        resultados = score_ruts(["76596744-9", "78383730-7"])
        for r in resultados:
            print(r["rut"], r.get("score"), r.get("company_name"))
    """
    def _score_one(rut):
        try:
            return score_rut(rut.strip(), signal=signal)
        except Exception as e:
            return {"rut": rut, "error": str(e)}

    ruts = [r for r in ruts if r and r.strip()]  # limpiar vacíos
    results = [None] * len(ruts)
    errores = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(_score_one, rut): i for i, rut in enumerate(ruts)}
        done = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            res = future.result()
            results[idx] = res
            done += 1
            nombre = res.get("company_name", "?")
            score  = res.get("score", "—")
            err    = res.get("error", "")
            if err:
                errores += 1
                print(f"  [{done}/{len(ruts)}] {ruts[idx]:<15}  ⚠️  {err[:60]}")
            else:
                charon = "✓" if res.get("charon_ok") else "~"
                print(f"  [{done}/{len(ruts)}] {ruts[idx]:<15}  [{charon}]  {nombre:<40}  score={score}")

    ok = len(ruts) - errores
    print(f"\n  Resumen: {ok}/{len(ruts)} exitosos ({ok/len(ruts)*100:.0f}%)  |  {errores} errores")
    return results


def score_ruts_to_csv(ruts: list[str], output_csv: str = "scoring_output.csv",
                      signal: str = "N/A", max_workers: int = 5) -> list[dict]:
    """
    Igual que score_ruts pero además guarda los resultados en un CSV.

    Columnas: rut, company_name, score, vertical, tamaño, holding,
              trabajadores, regulacion, proveedores, segmento, señal,
              pain_point, reasoning, giro, tramo, region, error
    """
    import csv

    print(f"Procesando {len(ruts)} RUTs → {output_csv}")
    resultados = score_ruts(ruts, signal=signal, max_workers=max_workers)

    fieldnames = [
        "rut", "company_name", "score", "vertical",
        "tamaño", "holding", "trabajadores", "regulacion",
        "proveedores", "segmento", "señal",
        "pain_point", "reasoning",
        "giro", "tramo", "region", "charon_ok", "error",
    ]

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in resultados:
            puntos = r.get("puntos", {})
            writer.writerow({
                "rut":           r.get("rut", ""),
                "company_name":  r.get("company_name", ""),
                "score":         r.get("score", ""),
                "vertical":      r.get("vertical", ""),
                "tamaño":        puntos.get("tamaño", ""),
                "holding":       puntos.get("holding", ""),
                "trabajadores":  puntos.get("trabajadores", ""),
                "regulacion":    puntos.get("regulacion", ""),
                "proveedores":   puntos.get("proveedores", ""),
                "segmento":      puntos.get("segmento", ""),
                "señal":         puntos.get("señal", ""),
                "pain_point":    r.get("pain_point", ""),
                "reasoning":     r.get("reasoning", ""),
                "giro":          r.get("giro", ""),
                "tramo":         r.get("tramo", ""),
                "region":        r.get("region", ""),
                "charon_ok":     r.get("charon_ok", ""),
                "error":         r.get("error", ""),
            })

    print(f"\n✅ CSV guardado: {output_csv}  ({len(resultados)} filas)")
    return resultados


# ── Ejemplo de uso ────────────────────────────────────────────────────────────
if __name__ == "__main__":

#     # Parámetros para funciones antiguas
#     params_base = dict(
#     company_name="CHITA SPA",
#     rut="76596744-9",
#     giro="OTRAS ACTIVIDADES DE SERVICIOS FINANCIEROS, EXCEPTO LAS DE SEGUROS Y FONDOS DE PENSIONES N.C.P.",
#     tramo="10",
#     region="XIII REGION METROPOLITANA",
#     signal="",
# )

# # Parámetros para la nueva función ajustada
#     params_adj2 = dict(
#     company_name="CHITA SPA",
#     rut="76596744-9",
#     giro="OTRAS ACTIVIDADES DE SERVICIOS FINANCIEROS, EXCEPTO LAS DE SEGUROS Y FONDOS DE PENSIONES N.C.P.",
#     tramo="10",
#     region="XIII REGION METROPOLITANA",
#     num_hijos="5",
#     num_trabajadores="65",
#     num_padres="0",
#     signal="" )

#     print("── score_lead ──────────────────────────────────────")
#     print_score(score_lead(**params_base), company_name=params_base["company_name"])

#     print("── score_lead_adj1 ─────────────────────────────────")
#     print_score(score_lead_adj1(**params_base), company_name=params_base["company_name"])

    # print("── score_lead_adj2 ─────────────────────────────────")
    # print_score(score_lead_adj2(**params_adj2), company_name=params_adj2["company_name"])

#     # print("── score_lead_lookup (solo RUT) ────────────────────")
#     # print_score(score_lead_lookup("59141000"), "DNB GROUP AGENCIA EN CHILE")

#     print("── get_company_data ────────────────────────────────")
#     data = get_company_data("76320186-4")
#     for k, v in data.items():
#         print(f"  {k:<20} {v}")

    # print("── score_lead_lookup (solo nombre) ─────────────────")
    # print_score(score_lead_lookup("Esbbio"), "Esbbio")

    print("── score_rut (solo RUT) ────────────────────────────")
    resultado = score_rut("76788050-2")
    print_score(resultado, company_name=resultado["company_name"])

    # # ── Test lookup_empresa ──────────────────────────────────────────────────
    # print("\n── lookup_empresa (nombre fantasía + sitio web) ────")
    # casos = [
    #     ("76045184-2",  "EMBOTELLADORA ANDINA S.A."),         # conocida, debería dar Coca-Cola Andina
    #     ("96690440-3",  "CENCOSUD S.A."),                     # conocida, debería dar Jumbo/Paris/Easy
    #     ("96874030-K",  "WALMART CHILE S.A."),                # conocida internacional
    #     ("76543210-1",  "INVERSIONES PEREZ Y CIA LTDA"),      # holding sin marca → null esperado
    #     ("59141000-0",  "DNB GROUP AGENCIA EN CHILE"),        # internacional, puede tener web
    # ]

    # for rut_test, razon in casos:
    #     print(f"\n  {razon}")
    #     res = lookup_empresa(rut_test, razon)
    #     fantasia = res.get("Nombre_Fantasia") or "—"
    #     sitio    = res.get("Sitio_Web")       or "—"
    #     conf     = res.get("Confianza")       or "—"
    #     print(f"    Fantasía : {fantasia}")
    #     print(f"    Sitio    : {sitio}")
    #     print(f"    Confianza: {conf}")

