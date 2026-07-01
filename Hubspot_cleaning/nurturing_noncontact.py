"""
Script: Mover a Nurturing Non Contact
======================================
Mueve a lifecycle "Nurturing Non Contact" todas las empresas que:
  - Tienen exactamente 1 deal en Pluttoneta AE
  - Ese deal está en Closed-Lost (es decir, sí pasaron por el funnel AE)

Exclusiones (no se tocan):
  - Empresas que ya son Live Customer, Implementación o Churned

INSTRUCCIONES:
  1. export HUBSPOT_TOKEN=pat-na1-...
  2. Corre con DRY_RUN = True primero para revisar qué empresas se mueven
  3. Si el ID de Nurturing Non Contact no es correcto, corre primero con
     PRINT_LIFECYCLE_STAGES = True para ver todos los stages del portal
  4. Cambia DRY_RUN = False y corre de nuevo para aplicar los cambios
"""

import os
import requests
import time
import json
import csv
from datetime import datetime

# ─────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
DRY_RUN       = False    # False = aplica cambios reales
PRINT_LIFECYCLE_STAGES = False  # True = imprime todos los lifecycle stages al inicio

BATCH_SIZE = 100
DELAY      = 0.3
# ─────────────────────────────────────────────────────────────

PIPELINE_PLUTTONETA = "20361325"

# Etapas Closed-Lost en Pluttoneta AE
CLOSED_LOST_STAGES = ["49655667", "1044035701"]

# Lifecycle stage ID de "Nurturing No Contact"
LC_NURTURING_NON_CONTACT = "50020180"

# Lifecycle stages que NO se deben pisar (protección)
LC_NO_TOCAR = {
    "52560399",   # Live Customer
    "52531545",   # Implementación
    "50020179",   # Churned
    "customer",   # valor legacy
    "opportunity", # Qualified Opportunity
}

BASE_URL = "https://api.hubapi.com"


def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type": "application/json",
    }


def print_lifecycle_stages():
    """Imprime todos los lifecycle stages del portal para identificar el ID correcto."""
    r = requests.get(
        f"{BASE_URL}/crm/v3/properties/companies/lifecyclestage",
        headers={"Authorization": f"Bearer {HUBSPOT_TOKEN}"},
    )
    if r.status_code != 200:
        print(f"  ⚠️  Error ({r.status_code}): {r.text[:200]}")
        return
    print("\n── Lifecycle stages disponibles en el portal ──")
    for opt in r.json().get("options", []):
        print(f"  {opt['value']:<25}  {opt['label']}")
    print()


def search_closed_lost_deals():
    """Retorna lista de (deal_id, stage) de todos los deals Closed-Lost en Pluttoneta AE."""
    deals, after = [], None
    print("  Buscando deals Closed-Lost en Pluttoneta AE...")
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline",  "operator": "EQ", "value": PIPELINE_PLUTTONETA},
                {"propertyName": "dealstage", "operator": "IN", "values": CLOSED_LOST_STAGES},
            ]}],
            "properties": ["dealstage", "dealname"],
            "limit": 200,
        }
        if after:
            body["after"] = after
        r = requests.post(f"{BASE_URL}/crm/v3/objects/deals/search", headers=hdrs(), json=body)
        if r.status_code not in (200, 201):
            print(f"  ⚠️  Error ({r.status_code}): {r.text[:200]}")
            break
        data = r.json()
        for d in data["results"]:
            deals.append((int(d["id"]), d["properties"]["dealstage"]))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)
    print(f"  → {len(deals)} deals Closed-Lost encontrados")
    return deals


def search_all_pluttoneta_deals():
    """
    Retorna todos los deals en Pluttoneta AE (cualquier etapa).
    Retorna lista de (deal_id, stage) y nombre del deal.
    """
    deals, after = [], None
    print("  Buscando TODOS los deals en Pluttoneta AE...")
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_PLUTTONETA},
            ]}],
            "properties": ["dealstage", "dealname"],
            "limit": 200,
        }
        if after:
            body["after"] = after
        r = requests.post(f"{BASE_URL}/crm/v3/objects/deals/search", headers=hdrs(), json=body)
        if r.status_code not in (200, 201):
            print(f"  ⚠️  Error ({r.status_code}): {r.text[:200]}")
            break
        data = r.json()
        for d in data["results"]:
            deals.append((
                int(d["id"]),
                d["properties"].get("dealstage", ""),
                d["properties"].get("dealname", ""),
            ))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)
    print(f"  → {len(deals)} deals totales en Pluttoneta AE")
    return deals


def deals_to_companies(deal_ids):
    """Retorna {company_id: set(deal_ids)} para un listado de deal IDs."""
    co_map = {}
    ids = list(deal_ids)
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
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
            print(f"  ⚠️  Error asociaciones ({r.status_code}): {r.text[:200]}")
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
                "inputs": [{"id": str(c)} for c in batch],
                "properties": ["name", "lifecyclestage"],
            },
        )
        if r.status_code in (200, 201):
            for obj in r.json().get("results", []):
                result[int(obj["id"])] = {
                    "name":           obj["properties"].get("name", "Sin nombre"),
                    "lifecyclestage": obj["properties"].get("lifecyclestage") or "sin valor",
                }
        time.sleep(DELAY)
    return result


def batch_update_companies(ids, new_stage):
    """Actualiza lifecyclestage en batches (reset + asignar)."""
    updated = errors = 0
    total = len(ids)
    for i in range(0, total, BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]

        # Paso 1: reset (necesario para poder "bajar" de stage en HubSpot)
        requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/update",
            headers=hdrs(),
            json={"inputs": [
                {"id": str(c), "properties": {"lifecyclestage": ""}}
                for c in batch
            ]},
        )
        time.sleep(DELAY)

        # Paso 2: asignar nuevo stage
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/update",
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
            print(f"\n  ⚠️  Error ({r.status_code}): {r.text[:200]}")

        pct = (i + len(batch)) / total * 100
        print(f"  Progreso: {pct:.0f}% | ok: {updated} | err: {errors}", end="\r")
        time.sleep(DELAY)
    print(f"\n  ✅ Actualizadas: {updated} | Errores: {errors}")
    return updated, errors


def main():
    print("=" * 62)
    print("  LIFECYCLE → NURTURING NON CONTACT")
    print(f"  Modo: {'👁️  DRY RUN (sin cambios)' if DRY_RUN else '✏️  APLICANDO CAMBIOS'}")
    print("=" * 62)

    if not HUBSPOT_TOKEN:
        print("❌ Configura HUBSPOT_TOKEN antes de ejecutar.")
        print("   export HUBSPOT_TOKEN=pat-na1-...")
        return

    if PRINT_LIFECYCLE_STAGES:
        print_lifecycle_stages()

    # Mapa de stage ID → nombre legible (Pluttoneta AE)
    STAGE_NAMES = {
        "144033658":  "Appointment Scheduled",
        "172924342":  "BDR Precalificación",
        "996295554":  "Consulting Discovery AE",
        "1028970392": "DEAL CALIFICADO (SQO)",
        "49686858":   "Demostración",
        "49686857":   "Economica Propuesta",
        "98993471":   "Interest Confirmed",
        "49686859":   "Negotiation",
        "74323563":   "Pilot",
        "49963372":   "Verbal Yes",
        "1163283618": "W - Iterando Contratos",
        "49655666":   "Won",
        "1007516181": "One Shoot Cerrado",
        "49655667":   "Closed Lost",
        "1044035701": "No Calificado",
    }

    def stage_name(sid):
        return STAGE_NAMES.get(str(sid), str(sid))

    # ── 1. Buscar deals Closed-Lost en Pluttoneta AE ─────────
    cl_deals = search_closed_lost_deals()
    if not cl_deals:
        print("\n✅ No hay deals Closed-Lost en Pluttoneta AE.")
        return

    cl_deal_ids = {did for did, _ in cl_deals}

    # ── 2. Buscar TODOS los deals en Pluttoneta AE ───────────
    all_deals = search_all_pluttoneta_deals()   # lista de (deal_id, stage, dealname)
    all_deal_ids = [did for did, _, _ in all_deals]
    # Mapa deal_id → (stage, dealname)
    deal_info = {did: (stage, name) for did, stage, name in all_deals}

    # ── 3. Mapear deals → empresas ────────────────────────────
    print("\n  Mapeando deals Closed-Lost → empresas...")
    cl_co_map = deals_to_companies(list(cl_deal_ids))
    print(f"  → {len(cl_co_map)} empresas con al menos 1 deal Closed-Lost")

    print("  Mapeando todos los deals → empresas (para contar total)...")
    all_co_map = deals_to_companies(all_deal_ids)
    print(f"  → {len(all_co_map)} empresas con deals en Pluttoneta AE")

    # ── 4. Todas las empresas con al menos 1 Closed-Lost ─────
    todas = [(cid, all_co_map.get(cid, set())) for cid in cl_co_map]
    print(f"\n  Empresas con al menos 1 deal Closed-Lost: {len(todas)}")

    # ── 5. Leer estado actual ─────────────────────────────────
    print(f"\n  Leyendo lifecycle actual de {len(todas)} empresas...")
    current = batch_read_companies([cid for cid, _ in todas])

    # ── 6. Clasificar ─────────────────────────────────────────
    excluidas    = []   # Live Customer, Implementación, Churned → nunca tocar
    ya_en_stage  = []   # ya están en Nurturing No Contact
    a_actualizar = []   # se mueven a Nurturing No Contact

    for cid, deal_ids in todas:
        info = current.get(cid, {"name": f"ID {cid}", "lifecyclestage": "desconocido"})
        lc = info["lifecyclestage"]

        if lc in LC_NO_TOCAR:
            excluidas.append((cid, info, deal_ids))
        elif lc == LC_NURTURING_NON_CONTACT:
            ya_en_stage.append((cid, info, deal_ids))
        else:
            a_actualizar.append((cid, info, deal_ids))

    def deals_label(deal_ids):
        return "  |  ".join(
            f"{stage_name(deal_info[did][0])}" + (f" ({deal_info[did][1]})" if deal_info[did][1] else "")
            for did in deal_ids
            if did in deal_info
        )

    # ── 7. Reporte ────────────────────────────────────────────
    if excluidas:
        print(f"\n── Excluidas (protegidas) — {len(excluidas)} ──")
        print(f"  {'Empresa':<45}  {'Lifecycle':<25}  Deals")
        print("  " + "-" * 110)
        for cid, info, deal_ids in excluidas:
            nombre = info["name"] or f"ID {cid}"
            print(f"  {nombre:<45}  {info['lifecyclestage']:<25}  {deals_label(deal_ids)}")

    if ya_en_stage:
        print(f"\n── Ya están en Nurturing No Contact — {len(ya_en_stage)} ──")
        for cid, info, _ in ya_en_stage:
            nombre = info["name"] or f"ID {cid}"
            print(f"  {nombre:<45}  sin cambio")

    print(f"\n── A mover → Nurturing No Contact — {len(a_actualizar)} ──")
    print(f"  {'Empresa':<45}  {'Lifecycle actual':<25}  Deals")
    print("  " + "-" * 110)
    for cid, info, deal_ids in sorted(a_actualizar, key=lambda x: len(x[2]), reverse=True):
        nombre = info["name"] or f"ID {cid}"
        lc = info["lifecyclestage"] or "sin valor"
        print(f"  {nombre:<45}  {lc:<25}  {deals_label(deal_ids)}")

    # CSV preview
    csv_path = "nurturing_noncontact_preview.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["company_id", "nombre", "lifecycle_actual", "accion", "deals"])
        writer.writeheader()
        for cid, info, deal_ids in a_actualizar:
            writer.writerow({
                "company_id":       cid,
                "nombre":           info["name"] or "",
                "lifecycle_actual": info["lifecyclestage"] or "",
                "accion":           "→ Nurturing No Contact",
                "deals":            " | ".join(stage_name(deal_info[did][0]) for did in deal_ids if did in deal_info),
            })
        for cid, info, deal_ids in excluidas:
            writer.writerow({
                "company_id":       cid,
                "nombre":           info["name"] or "",
                "lifecycle_actual": info["lifecyclestage"] or "",
                "accion":           "excluida (protegida)",
                "deals":            "Closed Lost",
            })
    print(f"\n📄 Preview guardado: {csv_path}")

    if DRY_RUN:
        print(f"\n✅ DRY RUN completado.")
        print(f"   {len(a_actualizar)} empresas se moverían a Nurturing Non Contact.")
        print(f"   Cambia DRY_RUN = False para aplicar los cambios.")
        return

    if not a_actualizar:
        print("\n✅ Nada que actualizar.")
        return

    confirm = input(
        f"\n⚠️  ¿Confirmas mover {len(a_actualizar)} empresas a Nurturing Non Contact? (escribe 'SI'): "
    )
    if confirm.strip().upper() != "SI":
        print("❌ Cancelado.")
        return

    print(f"\n✏️  Actualizando {len(a_actualizar)} empresas...")
    ids_to_update = [cid for cid, _, _d in a_actualizar]
    updated, errors = batch_update_companies(ids_to_update, LC_NURTURING_NON_CONTACT)

    log = {
        "fecha":        datetime.now().isoformat(),
        "total_ok":     updated,
        "total_err":    errors,
        "empresas":     [
            {"id": cid, "nombre": info["name"], "anterior": info["lifecyclestage"]}
            for cid, info, _d in a_actualizar
        ],
    }
    with open("log_nurturing_noncontact.json", "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Proceso completado.")
    print(f"   Log guardado: log_nurturing_noncontact.json")


if __name__ == "__main__":
    main()
