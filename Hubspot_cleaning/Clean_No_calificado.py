"""
Script: Empresas No Calificadas — Plutoneta AE
===============================================
Busca todas las empresas con deal en Plutoneta AE en etapas:
  · Z - Closed Lost       (49655667)
  · Deal No Calificado    (1044035701)

Y actualiza su lifecyclestage → No calificado (1151001700).
También actualiza los contactos asociados.

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

BASE_URL           = "https://api.hubapi.com"
PIPELINE_PLUTONETA = "20361325"

ETAPAS_NO_CALIFICADO = [
    "1044035701",  # Deal No Calificado
]

LC_NO_CALIFICADO = "1151001700"


def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }


def search_deals_no_calificados():
    """Retorna lista de deal_ids en etapas No Calificado dentro de Plutoneta AE."""
    deals, after = [], None
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline",  "operator": "EQ",  "value": PIPELINE_PLUTONETA},
                {"propertyName": "dealstage", "operator": "IN",  "values": ETAPAS_NO_CALIFICADO},
            ]}],
            "properties": ["dealstage"],
            "limit": 200,
        }
        if after:
            body["after"] = after

        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/deals/search",
            headers=hdrs(), json=body,
        )
        if r.status_code not in (200, 201):
            print(f"  ⚠️  Error buscando deals ({r.status_code}): {r.text[:200]}")
            break

        data = r.json()
        deals.extend(int(d["id"]) for d in data["results"])

        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)

    return deals


def deals_to_companies(deal_ids):
    """Retorna {company_id: set(deal_ids)} usando asociaciones batch v4."""
    co_map = {}
    for i in range(0, len(deal_ids), BATCH_SIZE):
        batch = deal_ids[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v4/associations/deals/companies/batch/read",
            headers=hdrs(),
            json={"inputs": [{"id": str(d)} for d in batch]},
        )
        if r.status_code in (200, 201, 207):
            for item in r.json().get("results", []):
                did = int(item["from"]["id"])
                for a in item.get("to", []):
                    cid = int(a["toObjectId"])
                    co_map.setdefault(cid, set()).add(did)
        else:
            print(f"  ⚠️  Error asociaciones ({r.status_code}): {r.text[:150]}")
        time.sleep(DELAY)
    return co_map


def batch_read_companies(ids):
    """Lee nombre y lifecyclestage actual de un batch de empresas."""
    result = {}
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/read",
            headers=hdrs(),
            json={
                "inputs":     [{"id": str(c)} for c in batch],
                "properties": ["name", "lifecyclestage"],
            },
        )
        if r.status_code in (200, 201):
            for obj in r.json().get("results", []):
                result[int(obj["id"])] = {
                    "name":           obj["properties"].get("name") or "Sin nombre",
                    "lifecyclestage": obj["properties"].get("lifecyclestage") or "sin valor",
                }
        time.sleep(DELAY)
    return result


def get_contacts_of_companies(company_ids):
    """Retorna {company_id: [contact_ids]}."""
    co_contacts = {}
    for i in range(0, len(company_ids), BATCH_SIZE):
        batch = company_ids[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v4/associations/companies/contacts/batch/read",
            headers=hdrs(),
            json={"inputs": [{"id": str(cid)} for cid in batch]},
        )
        if r.status_code in (200, 201, 207):
            for item in r.json().get("results", []):
                cid = item["from"]["id"]
                co_contacts[cid] = [a["toObjectId"] for a in item.get("to", [])]
        time.sleep(DELAY)
    return co_contacts


def get_total_deals_per_company(company_ids):
    """Retorna {company_id: total_deals} contando TODOS los deals de cada empresa."""
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
            print(f"  ⚠️  Error contando deals ({r.status_code}): {r.text[:150]}")
        time.sleep(DELAY)
    return result


def batch_update(object_type, ids, new_stage):
    """Actualiza lifecyclestage en batches.
    Primero resetea a '' para poder mover a cualquier stage
    (HubSpot solo permite avanzar, no retroceder, si no se limpia antes).
    """
    updated = errors = 0
    total = len(ids)
    for i in range(0, total, BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]

        # Paso 1: limpiar lifecycle stage
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
    print("  NO CALIFICADO — Plutoneta AE → lifecycle update")
    print(f"  Modo: {'👁️  DRY RUN (sin cambios)' if DRY_RUN else '✏️  APLICANDO CAMBIOS'}")
    print("=" * 62)

    if not HUBSPOT_TOKEN:
        print("❌  Configura HUBSPOT_TOKEN antes de ejecutar.")
        print("    export HUBSPOT_TOKEN=pat-na1-...")
        return

    # 1. Buscar deals en etapas No Calificado
    print("\n🔍 Buscando deals No Calificado en Plutoneta AE...")
    deal_ids = search_deals_no_calificados()
    print(f"   → {len(deal_ids)} deals encontrados")

    # 2. Obtener empresas asociadas al deal No Calificado
    print("\n🏢 Obteniendo empresas asociadas...")
    co_map = deals_to_companies(deal_ids)
    company_ids_raw = list(co_map.keys())
    print(f"   → {len(company_ids_raw)} empresas con deal No Calificado")

    # 3. Contar TODOS los deals de cada empresa (para informar en el CSV)
    print(f"\n🔢 Contando todos los deals por empresa...")
    total_deals = get_total_deals_per_company(company_ids_raw)
    company_ids = company_ids_raw
    print(f"   → {len(company_ids)} empresas en total")

    # 4. Leer estado actual
    print(f"\n🔍 Leyendo lifecycle actual...")
    current = batch_read_companies(company_ids)

    # 5. Filtrar las que ya tienen el stage correcto
    to_update = [c for c in company_ids
                 if current.get(c, {}).get("lifecyclestage") != LC_NO_CALIFICADO]
    ya_ok     = len(company_ids) - len(to_update)

    # 6. Preview
    print(f"\n── Preview ({len(to_update)} a actualizar, {ya_ok} ya correctas) ──")
    for cid in to_update[:25]:
        info = current.get(cid, {})
        print(f"  {info.get('name','?'):<50}  deals={total_deals.get(cid,0)}  {info.get('lifecyclestage','?')} → No calificado")
    if len(to_update) > 25:
        print(f"  ... y {len(to_update) - 25} más")

    # 7. Guardar CSV
    csv_path = "reporte_no_calificado.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Nombre", "Num Deals", "Lifecycle actual", "Lifecycle nuevo"])
        for cid in to_update:
            info = current.get(cid, {})
            writer.writerow([cid, info.get("name", ""), total_deals.get(cid, 0), info.get("lifecyclestage", ""), "No calificado"])
    print(f"\n📄 CSV guardado: {csv_path}")

    if DRY_RUN:
        print(f"\n✅ DRY RUN completado — {len(to_update)} empresas se actualizarían.")
        print(f"   Cambia DRY_RUN = False para aplicar.")
        return

    if not to_update:
        print("\n✅ Todo está al día. Nada que actualizar.")
        return

    confirm = input(f"\n⚠️  ¿Confirmas actualizar {len(to_update)} empresas y sus contactos? (escribe 'SI'): ")
    if confirm.strip().upper() != "SI":
        print("❌ Cancelado.")
        return

    # 8. Actualizar empresas
    print(f"\n✏️  Actualizando {len(to_update)} empresas → No calificado...")
    batch_update("companies", to_update, LC_NO_CALIFICADO)

    # 9. Actualizar contactos asociados
    print(f"\n✏️  Actualizando contactos asociados...")
    co_contacts = get_contacts_of_companies(to_update)
    contact_ids = list({cid for contacts in co_contacts.values() for cid in contacts})
    print(f"   → {len(contact_ids)} contactos encontrados")
    if contact_ids:
        batch_update("contacts", contact_ids, LC_NO_CALIFICADO)

    # 10. Log
    log_path = f"log_no_calificado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(log_path, "w") as f:
        json.dump([
            {"id": c, "nombre": current.get(c, {}).get("name"),
             "anterior": current.get(c, {}).get("lifecyclestage")}
            for c in to_update
        ], f, indent=2, ensure_ascii=False)

    print(f"\n✅ Proceso completado. Log: {log_path}")


if __name__ == "__main__":
    main()
