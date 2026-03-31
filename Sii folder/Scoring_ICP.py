import json
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


def score_lead_adj1(
    company_name: str,
    rut: str,
    giro: str,
    tramo: str,
    region: str,
    signal: str = "N/A",
) -> dict:
    prompt = f"""Eres un analista de ICP para Plutto, plataforma de compliance y KYB B2B en Chile.

Tienes que evaluar empresas como lead potencial y asigna un score de 0 a 100.

Empresa: {company_name}
RUT: {rut}
Industria/Giro: {giro}
Tramo ventas SII: {tramo}
Región: {region}
Señal (si aplica): {signal}

CONTEXTO

Plutto ayuda a empresas a evaluar proveedores, clientes y colaboradores mediante debida diligencia, monitoreo y detección de riesgos.

El fit depende de:
- regulación / presión de compliance
- volumen de terceros (proveedores, clientes, contratistas)
- tamaño empresa
- señales de urgencia
- herramientas actuales

REGLA CRÍTICA

Debes usar el SEGMENTO entregado como base del scoring.
NO reclasifiques la empresa salvo que sea evidentemente incorrecto.
Si parece incorrecto, puedes ajustarlo, pero debes mantener consistencia.

SCORING POR SEGMENTO

Si segmento = Financiero:
- Regulación: hasta 40 (todo el puntaje si la industria está regulada y tiene obligación legal de verificar contrapartes o proveedores)
- Contrapartes (no proveedores): hasta 20 (>200 = +10, 50-200 = +7, <50 = +3)
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10 (Regcheq, Lexis Nexis, KPMG, manual, etc.)
Max = 95

Si segmento = Mining / Energía:
- Regulación: hasta 30
- Proveedores: hasta 30 (>200 = +30, 50-200 = +15, <50 = +5)
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10
Max = 95

Si segmento = Utilities / Infraestructura:
- Regulación: hasta 30 (todo el puntaje si la industria está regulada y tiene obligación legal de verificar contrapartes o proveedores)
- Proveedores: hasta 25 (más de 200 (+25), entre 50 y 200 = (+15), menos de 50 = (+5))
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10
Max = 95

Si segmento = Manufactura / Retail:
- Regulación: hasta 20
- Proveedores: hasta 35 (>200 = +35, 50-200 = +20, <50 = +5)
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10
Max = 90

NORMALIZACIÓN

Score = ((reg + prov + tam + señal + comp) / max_segmento) * 100
CONTEXTO

Plutto ayuda a empresas a evaluar proveedores, clientes y colaboradores mediante debida diligencia, monitoreo y detección de riesgos.

El fit depende de:
- regulación / presión de compliance
- volumen de terceros (proveedores, clientes, contratistas)
- tamaño empresa
- señales de urgencia
- herramientas actuales

REGLA CRÍTICA

Debes usar el SEGMENTO entregado como base del scoring.
NO reclasifiques la empresa salvo que sea evidentemente incorrecto.
Si parece incorrecto, puedes ajustarlo, pero debes mantener consistencia.

SCORING POR SEGMENTO

Si segmento = Financiero:
- Regulación: hasta 40 (todo el puntaje si la industria está regulada y tiene obligación legal de verificar contrapartes o proveedores)
- Contrapartes (no proveedores): hasta 20 (>200 = +10, 50-200 = +7, <50 = +3)
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10 (Regcheq, Lexis Nexis, KPMG, manual, etc.)
Max = 95

Si segmento = Mining / Energía:
- Regulación: hasta 30
- Proveedores: hasta 30 (>200 = +30, 50-200 = +15, <50 = +5)
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10
Max = 95

Si segmento = Utilities / Infraestructura:
- Regulación: hasta 30 (todo el puntaje si la industria está regulada y tiene obligación legal de verificar contrapartes o proveedores)
- Proveedores: hasta 25 (más de 200 (+25), entre 50 y 200 = (+15), menos de 50 = (+5))
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10
Max = 95

Si segmento = Manufactura / Retail:
- Regulación: hasta 20
- Proveedores: hasta 35 (>200 = +35, 50-200 = +20, <50 = +5)
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10
Max = 90

NORMALIZACIÓN

Score = ((reg + prov + tam + señal + comp) / max_segmento) * 100
CONTEXTO

Plutto ayuda a empresas a evaluar proveedores, clientes y colaboradores mediante debida diligencia, monitoreo y detección de riesgos.

El fit depende de:
- regulación / presión de compliance
- volumen de terceros (proveedores, clientes, contratistas)
- tamaño empresa
- señales de urgencia
- herramientas actuales

REGLA CRÍTICA

Debes usar el SEGMENTO entregado como base del scoring.
NO reclasifiques la empresa salvo que sea evidentemente incorrecto.
Si parece incorrecto, puedes ajustarlo, pero debes mantener consistencia.

SCORING POR SEGMENTO

Si segmento = Financiero:
- Regulación: hasta 40 (todo el puntaje si la industria está regulada y tiene obligación legal de verificar contrapartes o proveedores)
- Contrapartes (no proveedores): hasta 20 (>200 = +10, 50-200 = +7, <50 = +3)
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10 (Regcheq, Lexis Nexis, KPMG, manual, etc.)
Max = 95

Si segmento = Mining / Energía:
- Regulación: hasta 30
- Proveedores: hasta 30 (>200 = +30, 50-200 = +15, <50 = +5)
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10
Max = 95

Si segmento = Utilities / Infraestructura:
- Regulación: hasta 30 (todo el puntaje si la industria está regulada y tiene obligación legal de verificar contrapartes o proveedores)
- Proveedores: hasta 25 (más de 200 (+25), entre 50 y 200 = (+15), menos de 50 = (+5))
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10
Max = 95

Si segmento = Manufactura / Retail:
- Regulación: hasta 20
- Proveedores: hasta 35 (>200 = +35, 50-200 = +20, <50 = +5)
- Tamaño: hasta 20 (gran empresa +20, mediana +12, pequeña +5)
- Señal: hasta 15 (licitación adjudicada +15, multa regulatoria +15, M&A +12, nueva regulación sectorial +10)
- Competencia: 0 a -10
Max = 90

NORMALIZACIÓN

Score = ((reg + prov + tam + señal + comp) / max_segmento) * 100
- mínimo 0, máximo 100, redondear a entero

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


def print_score(resultado: dict, company_name: str = "") -> None:
    print(f"\nEmpresa:     {company_name}")
    print(f"Vertical:    {resultado.get('vertical', '?')}")
    print(f"Score total: {resultado.get('score', '?')}/100")
    print(f"Pain point:  {resultado.get('pain_point', '?')}")
    print(f"Resumen:     {resultado.get('reasoning', '?')}\n")


# ── Ejemplo de uso ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    empresa = "DNB GROUP AGENCIA EN CHILE"
    params = dict(
        company_name=empresa,
        rut="59141000",
        giro="OTRAS ACTIVIDADES AUXILIARES DE LAS ACTIVIDADES DE SERVICIOS FINANCIEROS N.C.P.",
        tramo="10/13",
        region="XIII REGION METROPOLITANA",
        signal="",
    )

    print("── score_lead ──────────────────────────────────────")
    print_score(score_lead(**params), empresa)

    print("── score_lead_adj1 ─────────────────────────────────")
    print_score(score_lead_adj1(**params), empresa)