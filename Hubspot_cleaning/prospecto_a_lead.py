"""
Script: Prospecto → Lead
========================
Busca todas las empresas con lifecyclestage = "subscriber" (Prospecto)
y las mueve a "lead" si cumplen alguna de estas condiciones:
  · No tienen ningún negocio (deal) asociado
  · No tienen ningún contacto asociado

También actualiza los contactos de las empresas afectadas.

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

# ─────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
DRY_RUN       = True     # ← cambiar a False para aplicar
BATCH_SIZE    = 100
DELAY         = 0.3
# ─────────────────────────────────────────────────────────────

BASE_URL      = "https://api.hubapi.com"
LC_PROSPECTO  = "subscriber"   # Prospect
LC_LEAD       = "lead"         # Lead

# Stages que nunca se deben pisar
LC_NO_TOCAR = {
    "52560399",    # Live Customer
    "52531545",    # Implementación
    "50020179",    # Churned
    "customer",    # New Customer (legacy)
    "opportunity", # Qualified Opportunity
    "1151001700",  # No calificado
}


def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }


def search_prospectos():
    """Retorna lista de company_ids con lifecyclestage = subscriber (Prospecto)."""
    companies, after = [], None
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "lifecyclestage", "operator": "EQ", "value": LC_PROSPECTO},
            ]}],
            "properties": ["name", "lifecyclestage"],
            "limit": 200,
        }
        if after:
            body["after"] = after

        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/search",
            headers=hdrs(), json=body,
        )
        if r.status_code not in (200, 201):
            print(f"  ⚠️  Error buscando prospectos ({r.status_code}): {r.text[:200]}")
            break

        data = r.json()
        for c in data["results"]:
            companies.append({
                "id":   int(c["id"]),
                "name": c["properties"].get("name") or "Sin nombre",
                "lifecyclestage": c["properties"].get("lifecyclestage") or "",
            })

        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)

    return companies


def get_deals_per_company(company_ids):
    """Retorna {company_id: cantidad_deals}. Si no tiene deals → 0."""
    result = {}
    for i in range(0, len(company_ids), BATCH_SIZE):
        batch = company_ids[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v4/associations/companies/deals/batch/read",
            headers=hdrs(),
            json={"inputs": [{"id": str(cid)} for cid in batch]},
        )
        if r.status_code in (200, 201, 207):
            for item in r.json().get("results", []):
                cid = int(item["from"]["id"])
                result[cid] = len(item.get("to", []))
        else:
            print(f"  ⚠️  Error leyendo deals ({r.status_code}): {r.text[:150]}")
        time.sleep(DELAY)

    # Las que no aparecen en la respuesta no tienen deals
    for cid in company_ids:
        result.setdefault(cid, 0)

    return result


def get_contacts_per_company(company_ids):
    """Retorna {company_id: [contact_ids]}."""
    result = {}
    for i in range(0, len(company_ids), BATCH_SIZE):
        batch = company_ids[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v4/associations/companies/contacts/batch/read",
            headers=hdrs(),
            json={"inputs": [{"id": str(cid)} for cid in batch]},
        )
        if r.status_code in (200, 201, 207):
            for item in r.json().get("results", []):
                cid = int(item["from"]["id"])
                result[cid] = [int(a["toObjectId"]) for a in item.get("to", [])]
        else:
            print(f"  ⚠️  Error leyendo contactos ({r.status_code}): {r.text[:150]}")
        time.sleep(DELAY)

    for cid in company_ids:
        result.setdefault(cid, [])

    return result


def batch_update(object_type, ids, new_stage):
    """Actualiza lifecyclestage en batches (reset → asignar nuevo)."""
    updated = errors = 0
    total = len(ids)
    for i in range(0, total, BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]

        # Paso 1: limpiar lifecycle stage (permite mover en cualquier dirección)
        requests.post(
            f"{BASE_URL}/crm/v3/objects/{object_type}/batch/update",
            headers=hdrs(),
            json={"inputs": [
                {"id": str(c), "properties": {"lifecyclestage": ""}}
                for c in batch
            ]},
        )
        time.sleep(DELAY)

        # Paso 2: asignar el nuevo stage
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/{object_type}/batch/update",
            headers=hdrs(),
            json={"inputs": [
                {"id": str(c), "properties": {"lifecyclestage": new_stage}}
                for c in batch
            ]},
        )
        if r.status_code in (200, 201):
            updated += len(batch)
        else:
            errors += len(batch)
            print(f"\n  ⚠️  Error {object_type} ({r.status_code}): {r.text[:200]}")

        pct = (i + len(batch)) / total * 100
        print(f"  Progreso: {pct:.0f}%  |  ok: {updated}  |  err: {errors}", end="\r")
        time.sleep(DELAY)

    print(f"\n  ✅ {object_type} actualizados: {updated} | Errores: {errors}")
    return updated, errors


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 62)
    print("  PROSPECTO → LEAD  (sin deals o sin contactos)")
    print(f"  Modo: {'👁️  DRY RUN (sin cambios)' if DRY_RUN else '✏️  APLICANDO CAMBIOS'}")
    print("=" * 62)

    if not HUBSPOT_TOKEN:
        print("❌  Configura HUBSPOT_TOKEN antes de ejecutar.")
        print("    export HUBSPOT_TOKEN=pat-na1-...")
        return

    # 1. Buscar todas las empresas en Prospecto
    print("\n🔍 Buscando empresas con lifecycle = Prospecto...")
    prospectos = search_prospectos()
    print(f"   → {len(prospectos)} empresas encontradas")

    if not prospectos:
        print("\n✅ No hay empresas en Prospecto. Nada que hacer.")
        return

    company_ids = [c["id"] for c in prospectos]
    co_info     = {c["id"]: c for c in prospectos}

    # 2. Obtener deals y contactos de cada empresa
    print(f"\n🔢 Leyendo deals asociados...")
    deals_map = get_deals_per_company(company_ids)

    print(f"\n👤 Leyendo contactos asociados...")
    contacts_map = get_contacts_per_company(company_ids)

    # 3. Filtrar: sin deals O sin contactos
    to_update = []
    razones   = {}
    for cid in company_ids:
        sin_deals    = deals_map.get(cid, 0) == 0
        sin_contactos = len(contacts_map.get(cid, [])) == 0
        if sin_deals or sin_contactos:
            to_update.append(cid)
            partes = []
            if sin_deals:    partes.append("sin deals")
            if sin_contactos: partes.append("sin contactos")
            razones[cid] = " + ".join(partes)

    ya_ok = len(company_ids) - len(to_update)
    print(f"\n── Resumen ──────────────────────────────────────────")
    print(f"   Total prospectos:           {len(company_ids)}")
    print(f"   A mover → Lead:             {len(to_update)}")
    print(f"   Quedan como Prospecto:      {ya_ok}  (tienen deals y contactos)")

    # 4. Preview
    print(f"\n── Preview ({len(to_update)} empresas a actualizar) ──────────────")
    for cid in to_update[:30]:
        info = co_info[cid]
        print(f"  {info['name']:<50}  deals={deals_map.get(cid,0)}  contactos={len(contacts_map.get(cid,[]))}  [{razones[cid]}]")
    if len(to_update) > 30:
        print(f"  ... y {len(to_update) - 30} más")

    # 5. Guardar CSV
    csv_path = "reporte_prospecto_a_lead.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Nombre", "Num Deals", "Num Contactos", "Razón", "Lifecycle actual", "Lifecycle nuevo"])
        for cid in to_update:
            info = co_info[cid]
            writer.writerow([
                cid,
                info["name"],
                deals_map.get(cid, 0),
                len(contacts_map.get(cid, [])),
                razones[cid],
                info["lifecyclestage"],
                "lead",
            ])
    print(f"\n📄 CSV guardado: {csv_path}")

    if DRY_RUN:
        print(f"\n✅ DRY RUN completado — {len(to_update)} empresas se moverían a Lead.")
        print(f"   Cambia DRY_RUN = False para aplicar.")
        return

    if not to_update:
        print("\n✅ Nada que actualizar.")
        return

    confirm = input(f"\n⚠️  ¿Confirmas mover {len(to_update)} empresas y sus contactos a Lead? (escribe 'SI'): ")
    if confirm.strip().upper() != "SI":
        print("❌ Cancelado.")
        return

    # 6. Actualizar empresas
    print(f"\n✏️  Actualizando {len(to_update)} empresas → Lead...")
    batch_update("companies", to_update, LC_LEAD)

    # 7. Actualizar contactos asociados
    # Solo los contactos de las empresas que se actualizaron
    contact_ids = list({
        cid
        for company_id in to_update
        for cid in contacts_map.get(company_id, [])
    })
    print(f"\n✏️  Actualizando {len(contact_ids)} contactos asociados → Lead...")
    if contact_ids:
        batch_update("contacts", contact_ids, LC_LEAD)

    # 8. Log
    log_path = f"log_prospecto_a_lead_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(log_path, "w") as f:
        json.dump([
            {
                "id":       cid,
                "nombre":   co_info[cid]["name"],
                "anterior": LC_PROSPECTO,
                "razon":    razones[cid],
                "deals":    deals_map.get(cid, 0),
                "contactos": len(contacts_map.get(cid, [])),
            }
            for cid in to_update
        ], f, indent=2, ensure_ascii=False)

    print(f"\n✅ Proceso completado. Log: {log_path}")


if __name__ == "__main__":
    main()
