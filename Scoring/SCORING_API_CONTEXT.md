# Scoring ICP — Contexto para implementación de API

Este documento describe las funciones disponibles en `Scoring_ICP.py` para que puedan ser expuestas vía API REST.

---

## Infraestructura LLM

Todas las llamadas a LLM van a través de un proxy LiteLLM:

```
Base URL: https://hydra-portal-dev.fly.dev/v1
API Key:  sk-VOoWAq-wV6TDvr6ZsSuBOQ
Cliente:  OpenAI SDK (compatible)
```

Los modelos usados internamente son `claude-haiku-4-5-20251001` (scoring) y `gemini-2.5-flash` (lookup/clasificación).

---

## Fuente de datos empresariales: Charon

Las funciones de scoring usan la API interna de Charon para obtener datos fiscales de empresas chilenas:

```
GET https://charon-staging.herokuapp.com/api/businesses/{rut}
```

**Campos que retorna:**
| Campo | Descripción |
|---|---|
| `name` | Razón social |
| `economic_activity` | Giro SII |
| `sales_segment` | Tramo de ventas SII (1–13) |
| `region` | Región |
| `direct_employees` | Número de trabajadores |
| `subsidiaries` | Lista de empresas hijas |
| `parents` | Lista de empresas madres |

Si Charon no tiene el RUT, el scoring igual se ejecuta con degradación elegante (score parcial basado en los datos disponibles). El campo `charon_ok: bool` en la respuesta indica si se usaron datos reales.

---

## Funciones principales (candidatas a endpoints)

### 1. `score_rut(rut, signal?) → dict`

**La función más importante.** Dado un RUT chileno, retorna el score ICP completo.

**Input:**
```python
rut: str       # RUT en cualquier formato: "76596744-9", "765967449", "76596744"
signal: str    # Señal externa opcional (ej: "licitación adjudicada")
```

**Output:**
```json
{
  "score": 72,
  "vertical": "Mining/Energía",
  "puntos": {
    "tamaño": 20,
    "holding": 8,
    "trabajadores": 10,
    "regulacion": 15,
    "proveedores": 15,
    "segmento": 5,
    "señal": 0
  },
  "pain_point": "Gestión de proveedores en cadena de suministro minera",
  "reasoning": "Gran empresa minera con estructura holding...",
  "company_name": "MINERA XYZ S.A.",
  "rut": "76596744-9",
  "giro": "Extracción de cobre",
  "tramo": "12",
  "region": "Antofagasta",
  "charon_ok": true
}
```

**Endpoint sugerido:** `POST /score/rut`
```json
{ "rut": "76596744-9", "signal": "licitación adjudicada" }
```

---

### 2. `score_ruts(ruts, signal?, max_workers?) → list[dict]`

Igual que `score_rut` pero para una lista de RUTs en paralelo.

**Input:**
```python
ruts: list[str]    # Lista de RUTs
signal: str        # Señal aplicada a todos (opcional)
max_workers: int   # Paralelismo (default 3)
```

**Output:** Lista de dicts, mismo formato que `score_rut`. Si un RUT falla, incluye `"error": "mensaje"` en lugar del score.

**Endpoint sugerido:** `POST /score/ruts`
```json
{ "ruts": ["76596744-9", "78383730-7"], "signal": "N/A" }
```

---

### 3. `clasificar_vertical(company_name, giro, vertical_actual?) → dict`

Clasifica una empresa en uno de los **6 verticales de Plutto** usando LLM. Puede validar o corregir un vertical ya asignado.

**Verticales válidos:**
`FSI-REG` | `GAS-COMBUSTIBLE` | `MFG-RETAIL-HOLD` | `MINING-HSE` | `POWER-EPC` | `UTIL-INFRA`

**Input:**
```python
company_name: str      # Nombre o razón social
giro: str              # Actividad económica
vertical_actual: str   # Vertical previo (opcional, para validar)
```

**Output:**
```json
{
  "vertical": "MINING-HSE",
  "confianza": "Alta",
  "razon": "Empresa minera del cobre regulada por Sernageomin",
  "cambiado": true
}
```

**Endpoint sugerido:** `POST /clasificar/vertical`
```json
{
  "company_name": "Minera XYZ",
  "giro": "Extracción de cobre",
  "vertical_actual": "MFG-RETAIL-HOLD"
}
```

---

### 4. `lookup_empresa(rut, razon_social) → dict`

Busca el **nombre de fantasía** y **sitio web oficial** de una empresa. Usa Gemini con Google Search grounding como primera estrategia, y scraping DDG/Google como fallback.

**Input:**
```python
rut: str           # RUT de la empresa
razon_social: str  # Razón social exacta
```

**Output:**
```json
{
  "Rut": "76045184-2",
  "Razon_Social": "EMBOTELLADORA ANDINA S.A.",
  "Nombre_Fantasia": "Coca-Cola Andina",
  "Sitio_Web": "https://www.koandina.com",
  "Confianza": "Alta",
  "Fuente": "Gemini Search"
}
```

**`Confianza`:** `Alta` = confirmado en web · `Media` = inferido · `Baja` = suposición

**Endpoint sugerido:** `POST /lookup/empresa`
```json
{ "rut": "76045184-2", "razon_social": "EMBOTELLADORA ANDINA S.A." }
```

---

## Estructura de puntos del scoring

El score final (0–100) es la suma de 7 componentes:

| Componente | Quién lo calcula | Máximo | Criterio |
|---|---|---|---|
| `tamaño` | Python (tramo SII) | 20 | Tramo ≥ 10 → 20 pts |
| `holding` | Python (subsidiaries) | 15 | ≥ 3 hijas → 15 pts |
| `trabajadores` | Python (direct_employees) | 10 | > 1000 → 10 pts |
| `regulacion` | LLM | 15 | Regulada CMF/SEC/Sernageomin/CNE → 15 |
| `proveedores` | LLM | 25 | > 200 proveedores → 25 · 50–200 → 15 |
| `segmento` | Python (vertical) | 5 | Según vertical |
| `señal` | LLM | 10 | Señal fuerte → 10 · débil → 5 |

**Total máximo:** 100 puntos

---

## Tramos SII (referencia)

| Tramo | Categoría | Puntos |
|---|---|---|
| 10–13 | Gran empresa | 20 |
| 8–9 | Mediana | 12 |
| 5–7 | Pequeña | 5 |
| 2–4 | Micro | 2 |
| 0–1 | Sin información | 0 |

---

---

## Código fuente de las funciones

### Setup (imports y cliente LLM)

```python
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
```

---

### Helpers de scoring (calculados en Python, sin LLM)

```python
def _clasificar_tamaño(tramo) -> tuple[str, int]:
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
    try:
        h = int(hijos)
    except (ValueError, TypeError):
        return 0
    if h >= 3: return 15
    if h == 2: return 8
    if h == 1: return 3
    return 0

def _puntaje_trabajadores(numero_trabajadores) -> int:
    try:
        n = int(numero_trabajadores)
    except (ValueError, TypeError):
        return 0
    if n > 1000:       return 10
    if 500 <= n <= 1000: return 8
    if 200 <= n < 500:   return 5
    if 50 <= n < 200:    return 3
    return 0

def _puntaje_segmento(vertical: str) -> int:
    v = vertical.strip().lower()
    if v in ("mining/energía", "utilities/infraestructura", "manufactura retail"):
        return 5
    if v == "financiero":
        return 3
    return 0

def _extraer_json(texto: str) -> dict:
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if not match:
        raise ValueError("La respuesta de la LLM no contiene un JSON válido.")
    return json.loads(match.group(0))
```

---

### `_normalizar_rut` y `_charon_fetch`

```python
def _normalizar_rut(rut: str) -> list[str]:
    rut = rut.strip()
    variantes = [rut]
    sin_guion = rut.replace("-", "")
    if sin_guion not in variantes:
        variantes.append(sin_guion)
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
    import time as _time
    for variante in _normalizar_rut(rut):
        url = f"https://charon-staging.herokuapp.com/api/businesses/{variante}"
        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, timeout=20)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                data = resp.json()
                if data:
                    return data[0]
                break
            except ValueError:
                break
            except Exception as e:
                if attempt < retries:
                    _time.sleep(1.5 * attempt)
    return None
```

---

### `score_rut`

```python
def score_rut(rut: str, signal: str = "N/A") -> dict:
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
        nombre = giro = region = ""
        tramo = num_trabajadores = num_hijos = num_padres = "0"
        charon_ok = False

    resultado = score_lead_adj2(
        company_name=nombre, rut=rut, giro=giro, tramo=tramo,
        region=region, num_hijos=num_hijos,
        num_trabajadores=num_trabajadores, num_padres=num_padres,
        signal=signal,
    )
    resultado["company_name"] = nombre
    resultado["rut"]          = rut
    resultado["giro"]         = giro
    resultado["tramo"]        = tramo
    resultado["region"]       = region
    resultado["charon_ok"]    = charon_ok
    return resultado
```

---

### `score_lead_adj2` (motor de scoring)

```python
def score_lead_adj2(
    company_name: str, rut: str, giro: str, tramo: str, region: str,
    num_hijos: str, num_trabajadores: str, num_padres: str,
    signal: str = "N/A",
) -> dict:
    tamaño_label, tamaño_pts = _clasificar_tamaño(tramo)
    holding_pts     = _puntaje_holding(num_hijos)
    trabajadores_pts = _puntaje_trabajadores(num_trabajadores)
    MAX_RAW_SCORE   = 100

    prompt = f"""
Eres un analista de ICP para Plutto. Siempre responde con el JSON solicitado.

Datos:
- Razón social: {company_name or "(sin nombre)"}
- RUT: {rut}
- Giro: {giro or "(desconocido)"}
- Tramo SII: {tramo} | Región: {region}
- Empresas hijas: {num_hijos} | Trabajadores: {num_trabajadores}
- Señal externa: {signal}

Clasifica en vertical: Financiero | Mining/Energía | Utilities/Infraestructura | Manufactura Retail | Otro
Estima: regulacion (0 o 15), proveedores (0, 15 o 25), señal (0, 5 o 10)

Responde SOLO con JSON:
{{
  "vertical": "Otro",
  "puntos": {{"regulacion": 0, "proveedores": 0, "señal": 0}},
  "pain_point": "texto",
  "reasoning": "texto"
}}""".strip()

    response = client.chat.completions.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content.strip()
    try:
        raw = _extraer_json(text)
    except Exception:
        raw = {
            "vertical": "Otro",
            "puntos": {"regulacion": 0, "proveedores": 0, "señal": 0},
            "pain_point": "", "reasoning": f"[parse error: {text[:120]}]",
        }

    vertical_map = {
        "financiero": "Financiero", "mining/energía": "Mining/Energía",
        "utilities/infraestructura": "Utilities/Infraestructura",
        "manufactura retail": "Manufactura Retail", "otro": "Otro",
    }
    vertical_final = vertical_map.get(raw.get("vertical", "").strip().lower(), "Otro")
    segmento_pts   = _puntaje_segmento(vertical_final)

    llm = raw.get("puntos", {})
    regulacion_pts  = llm.get("regulacion", 0) if llm.get("regulacion") in (0, 15) else 0
    proveedores_pts = llm.get("proveedores", 0) if llm.get("proveedores") in (0, 15, 25) else 0
    señal_pts       = llm.get("señal", 0)       if llm.get("señal")       in (0, 5, 10)  else 0

    puntos = {
        "tamaño": tamaño_pts, "holding": holding_pts,
        "trabajadores": trabajadores_pts, "regulacion": regulacion_pts,
        "proveedores": proveedores_pts, "segmento": segmento_pts, "señal": señal_pts,
    }
    score = round(sum(puntos.values()) / MAX_RAW_SCORE * 100)
    return {"score": score, "vertical": vertical_final, "puntos": puntos,
            "pain_point": raw.get("pain_point", ""), "reasoning": raw.get("reasoning", "")}
```

---

### `score_ruts` (batch en paralelo)

```python
def score_ruts(ruts: list[str], signal: str = "N/A", max_workers: int = 3) -> list[dict]:
    def _score_one(rut):
        try:
            return score_rut(rut.strip(), signal=signal)
        except Exception as e:
            return {"rut": rut, "error": str(e)}

    ruts = [r for r in ruts if r and r.strip()]
    results = [None] * len(ruts)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(_score_one, rut): i for i, rut in enumerate(ruts)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()

    return results
```

---

### `clasificar_vertical`

```python
_VERTICALES_VALIDOS = {
    "FSI-REG", "GAS-COMBUSTIBLE", "MFG-RETAIL-HOLD",
    "MINING-HSE", "POWER-EPC", "UTIL-INFRA",
}

def clasificar_vertical(company_name: str, giro: str, vertical_actual: str = "") -> dict:
    instruccion = (
        f"El vertical actual es '{vertical_actual}'. Confírmalo o corrígelo."
        if vertical_actual and vertical_actual.upper() in _VERTICALES_VALIDOS
        else "Clasifica desde cero."
    )
    prompt = f"""Clasifica en uno de: FSI-REG | GAS-COMBUSTIBLE | MFG-RETAIL-HOLD | MINING-HSE | POWER-EPC | UTIL-INFRA
Empresa: {company_name or "Sin nombre"}
Giro: {giro or "Sin giro"}
{instruccion}
JSON: {{"vertical": "MFG-RETAIL-HOLD", "confianza": "Media", "razon": "texto"}}"""

    try:
        response = client.chat.completions.create(
            model="claude-haiku-4-5-20251001", max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _extraer_json(response.choices[0].message.content.strip())
    except Exception as e:
        return {"vertical": vertical_actual or "MFG-RETAIL-HOLD",
                "confianza": "Baja", "razon": str(e), "cambiado": False}

    vertical_nuevo = str(raw.get("vertical", "")).strip().upper()
    vertical_final = next((v for v in _VERTICALES_VALIDOS if v.upper() == vertical_nuevo),
                          "MFG-RETAIL-HOLD")
    return {
        "vertical":  vertical_final,
        "confianza": raw.get("confianza", "Baja"),
        "razon":     raw.get("razon", ""),
        "cambiado":  bool(vertical_actual and vertical_actual.upper() in _VERTICALES_VALIDOS
                          and vertical_actual.upper() != vertical_nuevo),
    }
```

---

### `lookup_empresa`

```python
def lookup_empresa(rut: str, razon_social: str) -> dict:
    # Intento 1: Gemini con Google Search grounding
    try:
        prompt = (
            f"Busca nombre comercial y sitio web de '{razon_social}' (RUT: {rut}). "
            f'JSON: {{"Rut":"{rut}","Razon_Social":"{razon_social}",'
            f'"Nombre_Fantasia":null,"Sitio_Web":null,"Confianza":"Baja","Fuente":"Gemini Search"}}'
        )
        response = client.chat.completions.create(
            model="gemini-2.5-flash", max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"google_search": {}}],
        )
        text = (response.choices[0].message.content or "").strip()
        if text:
            result = _extraer_json(text)
            if result.get("Nombre_Fantasia"):
                return result
    except Exception:
        pass

    # Intento 2: fallback Claude sin search
    prompt = (
        f"Identifica nombre comercial y sitio web de la empresa chilena "
        f"'{razon_social}' (RUT {rut}). "
        f'JSON: {{"Rut":"{rut}","Razon_Social":"{razon_social}",'
        f'"Nombre_Fantasia":null,"Sitio_Web":null,"Confianza":"Baja","Fuente":"LLM"}}'
    )
    response = client.chat.completions.create(
        model="claude-4-6-sonnet", max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        return _extraer_json(response.choices[0].message.content.strip())
    except Exception:
        return {"Rut": rut, "Razon_Social": razon_social,
                "Nombre_Fantasia": None, "Sitio_Web": None,
                "Confianza": "Baja", "Fuente": "error"}
```

---

## Consideraciones técnicas para la API

1. **Timeouts:** Las llamadas al LLM pueden tardar 5–15s por empresa. Para `score_ruts` con muchos RUTs, considerar respuesta asíncrona (job ID + polling).

2. **Rate limits Charon:** El código usa `DELAY = 0.3s` entre requests y `max_workers = 3` para no saturar Charon.

3. **RUT normalización:** `_charon_fetch` internamente prueba 3 variantes del RUT (`76596744-9`, `765967449`, `76596744`) con 3 reintentos cada una. No es necesario normalizar el RUT antes de llamar.

4. **`charon_ok: false`:** Si Charon no tiene el RUT, el scoring igual se ejecuta pero solo con los datos que el LLM puede inferir por nombre/giro. El score será más bajo e impreciso.

5. **Dependencias Python:**
```
openai
requests
beautifulsoup4
```
