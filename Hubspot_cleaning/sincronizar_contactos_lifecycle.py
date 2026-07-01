"""
Script: Sincronizar Lifecycle de Contactos con su Empresa
=========================================================
Para cada contacto en HubSpot:
  · Si tiene empresa asociada → le pone el mismo lifecycle que la empresa
    (respetando LC_NO_TOCAR: nunca pisa etapas protegidas)
  · Si NO tiene empresa → analiza sus actividades y usa LLM para decidir
    en qué etapa debería estar

INSTRUCCIONES:
  1. export HUBSPOT_TOKEN=pat-na1-...
  2. Corre con  DRY_RUN = True  para ver qué cambiaría
  3. Cambia    DRY_RUN = False  para aplicar los cambios reales
"""

import os
import csv
import json
import time
import requests
from datetime import datetime
from openai import OpenAI

# ─────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
HUBSPOT_TOKEN        = os.getenv("HUBSPOT_TOKEN", "")
DRY_RUN              = True      # ← cambiar a False para aplicar
BATCH_SIZE           = 100
DELAY                = 0.3       # segundos entre requests HubSpot
MAX_SIN_EMPRESA      = 300       # máximo de contactos sin empresa a analizar con LLM
MAX_ENGAGEMENTS      = 10        # actividades recientes a leer por contacto sin empresa

LITELLM_BASE_URL     = "https://hydra-portal-dev.fly.dev"
LITELLM_API_KEY      = os.getenv("LITELLM_API_KEY", "sk-VOoWAq-wV6TDvr6ZsSuBOQ")
# ─────────────────────────────────────────────────────────────

BASE_URL = "https://api.hubapi.com"

# Stages que nunca se deben pisar en contactos
LC_NO_TOCAR = {
    "52560399",    # Live Customer
    "52531545",    # Implementación
    "50020179",    # Churned
    "customer",    # New Customer (legacy)
    "opportunity", # Qualified Opportunity
    "1151001700",  # No calificado
}

LC_LABELS = {
    "lead":        "Lead",
    "subscriber":  "Prospect",
    "opportunity": "Qualified Opportunity",
    "52531545":    "Implementation",
    "customer":    "New Customer",
    "52560399":    "Live Customer",
    "49991712":    "Happy Customer",
    "50000917":    "Sad/No use Customer",
    "evangelist":  "Evangelist",
    "50020179":    "Churned",
    "other":       "Other",
    "50005795":    "Nurturing",
    "50020180":    "Nurturing No Contact",
    "1151001700":  "No calificado",
}

llm_client = OpenAI(base_url=f"{LITELLM_BASE_URL}/v1", api_key=LITELLM_API_KEY)


def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }


# ─────────────────────────────────────────────────────────────
#  FETCH CONTACTOS
# ─────────────────────────────────────────────────────────────

def fetch_all_contacts() -> list[dict]:
    """Retorna todos los contactos con propiedades básicas."""
    props = [
        "firstname", "lastname", "email", "jobtitle",
        "lifecyclestage",
        "hs_last_contacted", "hs_email_last_open_date",
        "num_contacted_notes", "hs_sales_email_last_replied",
        "recent_conversion_event_name",
    ]
    contacts, after = [], None
    print("  Paginando contactos", end="", flush=True)

    while True:
        body = {
            "properties": props,
            "limit": 200,
        }
        if after:
            body["after"] = after

        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/contacts/search",
            headers=hdrs(),
            json={**body, "filterGroups": []},
        )
        if r.status_code not in (200, 201):
            print(f"\n  ⚠️  Error fetch contacts ({r.status_code}): {r.text[:200]}")
            break

        data = r.json()
        for c in data["results"]:
            p = c["properties"]
            contacts.append({
                "id":            int(c["id"]),
                "nombre":        f"{p.get('firstname') or ''} {p.get('lastname') or ''}".strip() or "Sin nombre",
                "email":         p.get("email") or "",
                "jobtitle":      p.get("jobtitle") or "",
                "lifecycle":     p.get("lifecyclestage") or "",
                "last_contacted": p.get("hs_last_contacted") or "",
                "email_opened":  p.get("hs_email_last_open_date") or "",
                "email_replied": p.get("hs_sales_email_last_replied") or "",
                "notes":         p.get("num_contacted_notes") or "0",
                "conversion":    p.get("recent_conversion_event_name") or "",
            })

        after = (data.get("paging") or {}).get("next", {}).get("after")
        print(".", end="", flush=True)
        if not after:
            break
        time.sleep(DELAY)

    print(f" → {len(contacts)} contactos")
    return contacts


# ─────────────────────────────────────────────────────────────
#  ASOCIACIONES CONTACTO → EMPRESA
# ─────────────────────────────────────────────────────────────

def get_company_per_contact(contact_ids: list[int]) -> dict[int, int | None]:
    """Retorna {contact_id: company_id | None}."""
    result = {}
    for i in range(0, len(contact_ids), BATCH_SIZE):
        batch = contact_ids[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v4/associations/contacts/companies/batch/read",
            headers=hdrs(),
            json={"inputs": [{"id": str(cid)} for cid in batch]},
        )
        if r.status_code in (200, 201, 207):
            for item in r.json().get("results", []):
                cid = int(item["from"]["id"])
                tos = item.get("to", [])
                result[cid] = int(tos[0]["toObjectId"]) if tos else None
        else:
            print(f"\n  ⚠️  Error asociaciones contacto→empresa ({r.status_code}): {r.text[:150]}")
        time.sleep(DELAY)

    for cid in contact_ids:
        result.setdefault(cid, None)
    return result


# ─────────────────────────────────────────────────────────────
#  LIFECYCLE DE EMPRESAS
# ─────────────────────────────────────────────────────────────

def get_company_lifecycles(company_ids: list[int]) -> dict[int, str]:
    """Retorna {company_id: lifecyclestage}."""
    result = {}
    uniq = list(set(company_ids))
    for i in range(0, len(uniq), BATCH_SIZE):
        batch = uniq[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/read",
            headers=hdrs(),
            json={
                "inputs":     [{"id": str(cid)} for cid in batch],
                "properties": ["lifecyclestage", "name"],
            },
        )
        if r.status_code in (200, 201):
            for c in r.json().get("results", []):
                result[int(c["id"])] = c["properties"].get("lifecyclestage") or ""
        else:
            print(f"\n  ⚠️  Error leyendo lifecycle empresas ({r.status_code}): {r.text[:150]}")
        time.sleep(DELAY)
    return result


# ─────────────────────────────────────────────────────────────
#  ACTIVIDADES DE CONTACTO (para los sin empresa)
# ─────────────────────────────────────────────────────────────

def get_engagements_for_contact(contact_id: int) -> list[dict]:
    """Obtiene las últimas actividades (calls, emails, meetings, notes) de un contacto."""
    try:
        r = requests.get(
            f"{BASE_URL}/engagements/v1/engagements/associated/CONTACT/{contact_id}/paged",
            headers=hdrs(),
            params={"limit": MAX_ENGAGEMENTS},
            timeout=8,
        )
        if r.status_code != 200:
            return []
        items = r.json().get("results", [])
        activities = []
        for item in items:
            eng  = item.get("engagement", {})
            meta = item.get("metadata", {})
            activities.append({
                "type":    eng.get("type", ""),
                "date":    eng.get("lastUpdated") or eng.get("timestamp") or "",
                "subject": meta.get("subject") or meta.get("title") or "",
                "body":    (meta.get("body") or meta.get("text") or "")[:300],
                "status":  meta.get("status") or "",
                "duration": meta.get("durationMilliseconds") or "",
            })
        return activities
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
#  LLM: decidir etapa para contacto sin empresa
# ─────────────────────────────────────────────────────────────

def decidir_etapa_llm(contacto: dict, actividades: list[dict]) -> tuple[str, str]:
    """
    Usa LLM para decidir el lifecycle stage de un contacto sin empresa.
    Retorna (stage_value, razon).
    """
    act_str = ""
    if actividades:
        lineas = []
        for a in actividades:
            fecha = a["date"][:10] if len(a["date"]) >= 10 else a["date"]
            lineas.append(
                f"  [{a['type']}] {fecha}  asunto: {a['subject'][:60]}  "
                f"estado: {a['status']}  cuerpo: {a['body'][:100]}"
            )
        act_str = "\n".join(lineas)
    else:
        act_str = "  (sin actividades registradas)"

    etapas_disponibles = "\n".join(
        f"  - \"{v}\": {label}"
        for v, label in LC_LABELS.items()
        if v not in LC_NO_TOCAR
    )

    prompt = f"""Eres un analista de CRM de Plutto, plataforma B2B de compliance en Chile.
Debes clasificar a este contacto en su lifecycle stage correcto según su historial de actividades.

Contacto:
  Nombre: {contacto['nombre']}
  Email:  {contacto['email']}
  Cargo:  {contacto['jobtitle']}
  Stage actual: {contacto['lifecycle']}
  Último contacto: {contacto['last_contacted'][:10] if contacto['last_contacted'] else 'N/A'}
  Email abierto:   {contacto['email_opened'][:10] if contacto['email_opened'] else 'N/A'}
  Email respondido:{contacto['email_replied'][:10] if contacto['email_replied'] else 'N/A'}
  Notas registradas: {contacto['notes']}
  Última conversión: {contacto['conversion']}

Actividades recientes ({len(actividades)}):
{act_str}

Etapas disponibles:
{etapas_disponibles}

Reglas de clasificación:
- "lead": contacto que conocemos, alguna interacción básica (email abierto, formulario, nota), sin avance comercial claro
- "subscriber": solo nos dieron el correo, sin interacción real
- "50005795" (Nurturing): tenemos conversaciones pero sin señal de compra próxima
- "50020180" (Nurturing No Contact): no ha respondido en meses
- "49991712" / "50000917": solo para clientes activos — NO usar aquí
- "evangelist": solo si recomienda activamente Plutto — muy raro sin empresa

Responde SOLO con JSON:
{{"stage": "<value>", "razon": "<explicación breve en español max 20 palabras>"}}"""

    try:
        resp = llm_client.chat.completions.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content.strip()
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        data  = json.loads(match.group(0)) if match else {}
        stage = data.get("stage", "lead")
        razon = data.get("razon", "LLM sin razon")
        # Safety: no pisar etapas protegidas
        if stage in LC_NO_TOCAR:
            stage = "lead"
            razon = "stage protegido → reasignado a lead"
        return stage, razon
    except Exception as e:
        return "lead", f"error LLM: {str(e)[:60]}"


# ─────────────────────────────────────────────────────────────
#  ACTUALIZACIÓN BATCH
# ─────────────────────────────────────────────────────────────

def batch_update(object_type: str, updates: list[tuple[int, str]]):
    """
    updates = [(id, new_stage), ...]
    Aplica reset → nuevo stage en batches de BATCH_SIZE.
    """
    updated = errors = 0
    total   = len(updates)

    for i in range(0, total, BATCH_SIZE):
        batch = updates[i : i + BATCH_SIZE]

        # Paso 1: reset
        requests.post(
            f"{BASE_URL}/crm/v3/objects/{object_type}/batch/update",
            headers=hdrs(),
            json={"inputs": [
                {"id": str(uid), "properties": {"lifecyclestage": ""}}
                for uid, _ in batch
            ]},
        )
        time.sleep(DELAY)

        # Paso 2: asignar nuevo stage
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/{object_type}/batch/update",
            headers=hdrs(),
            json={"inputs": [
                {"id": str(uid), "properties": {"lifecyclestage": stage}}
                for uid, stage in batch
            ]},
        )
        if r.status_code in (200, 201):
            updated += len(batch)
        else:
            errors += len(batch)
            print(f"\n  ⚠️  Error actualizando {object_type} ({r.status_code}): {r.text[:200]}")

        pct = (i + len(batch)) / total * 100
        print(f"  Progreso: {pct:.0f}%  ok={updated}  err={errors}", end="\r")
        time.sleep(DELAY)

    print(f"\n  ✅ {object_type}: {updated} actualizados | {errors} errores")
    return updated, errors


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 68)
    print("  SINCRONIZAR LIFECYCLE CONTACTOS ↔ EMPRESA  (+ LLM sin empresa)")
    print(f"  Modo: {'👁️  DRY RUN (sin cambios)' if DRY_RUN else '✏️  APLICANDO CAMBIOS'}")
    print("=" * 68)

    if not HUBSPOT_TOKEN:
        print("❌  Configura HUBSPOT_TOKEN antes de ejecutar.")
        return

    # ── 1. Traer todos los contactos ──────────────────────────────────────────
    print("\n🔍 Fetching contactos...")
    contactos = fetch_all_contacts()
    if not contactos:
        print("❌ No se obtuvieron contactos.")
        return

    contact_ids = [c["id"] for c in contactos]
    co_map      = {c["id"]: c for c in contactos}

    # ── 2. Asociaciones contacto → empresa ───────────────────────────────────
    print(f"\n🔗 Leyendo asociaciones contacto → empresa ({len(contactos)} contactos)...")
    empresa_de_contacto = get_company_per_contact(contact_ids)

    con_empresa    = [cid for cid, eid in empresa_de_contacto.items() if eid]
    sin_empresa    = [cid for cid, eid in empresa_de_contacto.items() if not eid]
    print(f"   Con empresa: {len(con_empresa)}  |  Sin empresa: {len(sin_empresa)}")

    # ── 3. Lifecycle de las empresas ─────────────────────────────────────────
    company_ids_uniq = list({empresa_de_contacto[cid] for cid in con_empresa})
    print(f"\n🏢 Leyendo lifecycle de {len(company_ids_uniq)} empresas...")
    company_lc = get_company_lifecycles(company_ids_uniq)

    # ── 4. Determinar cambios para contactos CON empresa ─────────────────────
    updates_con_empresa   = []   # (contact_id, new_stage)
    reporte_con_empresa   = []

    for cid in con_empresa:
        contacto   = co_map[cid]
        eid        = empresa_de_contacto[cid]
        nuevo_lc   = company_lc.get(eid, "")
        actual_lc  = contacto["lifecycle"]

        # Skip si el contacto ya está en la etapa correcta
        if nuevo_lc == actual_lc:
            continue
        # Skip si el contacto tiene un stage protegido
        if actual_lc in LC_NO_TOCAR:
            continue
        # Skip si la empresa no tiene stage asignado
        if not nuevo_lc:
            continue
        # Skip si el nuevo stage es protegido (no queremos pisar)
        if nuevo_lc in LC_NO_TOCAR:
            continue

        updates_con_empresa.append((cid, nuevo_lc))
        reporte_con_empresa.append({
            "contact_id":   cid,
            "nombre":       contacto["nombre"],
            "email":        contacto["email"],
            "empresa_id":   eid,
            "lc_anterior":  actual_lc,
            "lc_nuevo":     nuevo_lc,
            "razon":        "sinc. con empresa",
            "metodo":       "empresa",
        })

    print(f"\n   Contactos CON empresa a actualizar: {len(updates_con_empresa)}")
    print(f"   (ya correctos o protegidos: {len(con_empresa) - len(updates_con_empresa)})")

    # ── 5. Determinar etapa para contactos SIN empresa (LLM) ─────────────────
    updates_sin_empresa = []
    reporte_sin_empresa = []

    sin_empresa_a_procesar = sin_empresa[:MAX_SIN_EMPRESA]
    print(f"\n🤖 Analizando {len(sin_empresa_a_procesar)} contactos sin empresa con LLM...")
    if len(sin_empresa) > MAX_SIN_EMPRESA:
        print(f"   (limitado a {MAX_SIN_EMPRESA} de {len(sin_empresa)} totales — ajusta MAX_SIN_EMPRESA)")

    for i, cid in enumerate(sin_empresa_a_procesar, 1):
        contacto  = co_map[cid]
        actual_lc = contacto["lifecycle"]

        # Skip si stage protegido
        if actual_lc in LC_NO_TOCAR:
            print(f"  [{i}/{len(sin_empresa_a_procesar)}] {contacto['nombre'][:35]:<37}  ⛔ stage protegido ({actual_lc})")
            continue

        actividades = get_engagements_for_contact(cid)
        nuevo_lc, razon = decidir_etapa_llm(contacto, actividades)

        if nuevo_lc == actual_lc:
            print(f"  [{i}/{len(sin_empresa_a_procesar)}] {contacto['nombre'][:35]:<37}  ✓ ya correcto ({actual_lc})")
            continue

        updates_sin_empresa.append((cid, nuevo_lc))
        reporte_sin_empresa.append({
            "contact_id":   cid,
            "nombre":       contacto["nombre"],
            "email":        contacto["email"],
            "empresa_id":   None,
            "lc_anterior":  actual_lc,
            "lc_nuevo":     nuevo_lc,
            "razon":        razon,
            "metodo":       "LLM",
        })
        lbl_ant = LC_LABELS.get(actual_lc, actual_lc) or "—"
        lbl_new = LC_LABELS.get(nuevo_lc, nuevo_lc)
        print(f"  [{i}/{len(sin_empresa_a_procesar)}] {contacto['nombre'][:35]:<37}  {lbl_ant:<22} → {lbl_new}  ({razon[:35]})")
        time.sleep(DELAY)

    # ── 6. Resumen ────────────────────────────────────────────────────────────
    todos_updates = updates_con_empresa + updates_sin_empresa
    todos_reporte = reporte_con_empresa + reporte_sin_empresa

    print(f"\n── Resumen ────────────────────────────────────────────────────")
    print(f"   Total contactos:               {len(contactos):>6}")
    print(f"   Con empresa → a actualizar:    {len(updates_con_empresa):>6}")
    print(f"   Sin empresa → a actualizar:    {len(updates_sin_empresa):>6}")
    print(f"   Total cambios pendientes:      {len(todos_updates):>6}")

    # ── 7. Guardar CSV reporte ────────────────────────────────────────────────
    csv_path = "reporte_sincronizar_contactos.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "contact_id", "nombre", "email", "empresa_id",
            "lc_anterior", "lc_nuevo", "razon", "metodo",
        ])
        writer.writeheader()
        writer.writerows(todos_reporte)
    print(f"\n📄 CSV guardado: {csv_path}  ({len(todos_reporte)} filas)")

    if DRY_RUN:
        print(f"\n✅ DRY RUN completado — {len(todos_updates)} contactos se actualizarían.")
        print(f"   Cambia DRY_RUN = False para aplicar.")
        return

    if not todos_updates:
        print("\n✅ Nada que actualizar.")
        return

    confirm = input(f"\n⚠️  ¿Confirmas actualizar {len(todos_updates)} contactos? (escribe 'SI'): ")
    if confirm.strip().upper() != "SI":
        print("❌ Cancelado.")
        return

    # ── 8. Aplicar cambios ────────────────────────────────────────────────────
    if updates_con_empresa:
        print(f"\n✏️  Actualizando {len(updates_con_empresa)} contactos (con empresa)...")
        batch_update("contacts", updates_con_empresa)

    if updates_sin_empresa:
        print(f"\n✏️  Actualizando {len(updates_sin_empresa)} contactos (sin empresa / LLM)...")
        batch_update("contacts", updates_sin_empresa)

    # ── 9. Log JSON ───────────────────────────────────────────────────────────
    log_path = f"log_sincronizar_contactos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(log_path, "w") as f:
        json.dump(todos_reporte, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Proceso completado. Log: {log_path}")


if __name__ == "__main__":
    main()
