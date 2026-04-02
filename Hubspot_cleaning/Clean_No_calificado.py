"""
Script: Clasificar Lifecycle según etapas de deal — Plutoneta AE
================================================================
Aplica tres reglas en orden de prioridad:

  REGLA 1 — No calificado  (lifecyclestage → "1151001700")
    Empresa tiene deal en Plutoneta AE en etapa:
      · Z - Closed Lost       (49655667)
      · Deal No Calificado    (1044035701)

  REGLA 2 — Nurturing       (lifecyclestage → "50005795")
    Empresa tiene deal en Plutoneta AE en etapa:
      · AA Freeze             (233748848)
      · Nurturing             (1035269638)

  REGLA 3 — Prospect        (lifecyclestage → "subscriber")
    Empresa ya tiene lifecyclestage = Nurturing  Y
    NO tiene ningún deal activo en Plutoneta AE.
    ("Activo" = etapa que NO sea closed/terminal — ver PLUTTONETA_TERMINAL)

Prioridad: Regla 1 > Regla 2 > Regla 3
(si una empresa califica para varias reglas, gana la de mayor prioridad)

─────────────────────────────────────────────────────────────
INSTRUCCIONES:
  1. Pega tu token en HUBSPOT_TOKEN  (o usa variable de entorno)
  2. Corre con  DRY_RUN = True  para ver qué cambiaría
  3. Cambia    DRY_RUN = False  para aplicar los cambios reales
─────────────────────────────────────────────────────────────
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
DELAY         = 0.3      # segundos entre requests
# ─────────────────────────────────────────────────────────────

BASE_URL = "https://api.hubapi.com"

# ── Pipeline ─────────────────────────────────────────────────
PIPELINE_PLUTONETA = "20361325"

# ── Etapas que activan cada regla ────────────────────────────
ETAPAS_NO_CALIFICADO = [
    "49655667",    # Z - Closed Lost
    "1044035701",  # Deal No Calificado (No SQO)
]

ETAPAS_NURTURING = [
    "233748848",   # AA Freeze
    "1035269638",  # Nurturing
]

# Etapas terminales/cerradas (no cuentan como "activo" para Regla 3)
PLUTTONETA_TERMINAL = set(ETAPAS_NO_CALIFICADO + ETAPAS_NURTURING + [
    "49655666",    # Won
    "1166145656",  # X - Listo para facturar
    "1007516181",  # One Shoot Cerrado
])

# ── Valores de lifecyclestage ─────────────────────────────────
LC_NO_CALIFICADO = "1151001700"
LC_NURTURING     = "50005795"
LC_PROSPECT      = "subscriber"

# Nombres legibles para mostrar en pantalla
LC_LABELS = {
    "1151001700": "No calificado",
    "50005795":   "Nurturing",
    "subscriber": "Prospect",
    "lead":       "Lead",
    "opportunity":"Qualified Opportunity",
    "customer":   "Live Customer",
    "other":      "Churned",
    "sin valor":  "sin valor",
}


# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────

def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }


def search_all_deals(pipeline):
    """
    Retorna lista de (deal_id, stage_id, fecha) para TODOS los deals
    del pipeline. Fecha = hs_lastmodifieddate (siempre existe).
    """
    deals, after = [], None
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline", "operator": "EQ", "value": pipeline},
            ]}],
            "properties": ["dealstage", "hs_lastmodifieddate"],
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
        for d in data["results"]:
            p = d["properties"]
            deals.append((int(d["id"]), p["dealstage"], p.get("hs_lastmodifieddate") or ""))

        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)

    return deals


def latest_deal_per_company(all_deals, co_map):
    """
    Dado todos los deals (id, stage, fecha) y el mapa company→deals,
    retorna {company_id: stage_del_deal_más_reciente}.
    """
    # deal_id → (stage, fecha)
    deal_info = {did: (stage, fecha) for did, stage, fecha in all_deals}

    result = {}
    for cid, deal_ids in co_map.items():
        # Ordenar por fecha desc y tomar el primero
        deals_con_fecha = [
            (did, deal_info[did][0], deal_info[did][1])
            for did in deal_ids if did in deal_info
        ]
        if not deals_con_fecha:
            continue
        deals_con_fecha.sort(key=lambda x: x[2] or "", reverse=True)
        result[cid] = deals_con_fecha[0][1]   # stage del más reciente

    return result


def deals_to_companies(deal_ids):
    """
    Dado un iterable de deal IDs, retorna {company_id: set(deal_ids)}
    usando el endpoint de asociaciones batch v4.
    """
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
            print(f"  ⚠️  Error asociaciones ({r.status_code}): {r.text[:150]}")
        time.sleep(DELAY)
    return co_map


def search_companies_by_lifecycle(lifecycle_value):
    """Retorna set de company IDs con el lifecyclestage dado."""
    ids, after = set(), None
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "lifecyclestage", "operator": "EQ", "value": lifecycle_value},
            ]}],
            "properties": ["lifecyclestage"],
            "limit": 200,
        }
        if after:
            body["after"] = after

        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/search",
            headers=hdrs(), json=body,
        )
        if r.status_code not in (200, 201):
            print(f"  ⚠️  Error buscando empresas ({r.status_code}): {r.text[:200]}")
            break

        data = r.json()
        for c in data["results"]:
            ids.add(int(c["id"]))

        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)

    return ids


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
    """Retorna {company_id: [contact_ids]} para un batch de empresas."""
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


def batch_update_contacts(ids, new_stage):
    """Actualiza lifecyclestage de contactos en batches."""
    updated = errors = 0
    total = len(ids)
    for i in range(0, total, BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/contacts/batch/update",
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
            print(f"\n  ⚠️  Error contactos ({r.status_code}): {r.text[:200]}")
        time.sleep(DELAY)
    print(f"  ✅ Contactos actualizados: {updated} | Errores: {errors}")
    return updated, errors


def update_contacts_for_companies(company_ids, new_stage):
    """Obtiene contactos de las empresas y los actualiza al mismo lifecycle."""
    if not company_ids:
        return
    print(f"  → Obteniendo contactos de {len(company_ids)} empresas...")
    co_contacts = get_contacts_of_companies(company_ids)
    contact_ids = list({cid for contacts in co_contacts.values() for cid in contacts})
    print(f"  → {len(contact_ids)} contactos a actualizar → {lc_name(new_stage)}")
    if contact_ids:
        batch_update_contacts(contact_ids, new_stage)


def batch_update_companies(ids, new_stage):
    """Actualiza lifecyclestage en batches, muestra progreso."""
    updated = errors = 0
    total = len(ids)
    for i in range(0, total, BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
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
        print(f"  Progreso: {pct:.0f}%  |  ok: {updated}  |  err: {errors}", end="\r")
        time.sleep(DELAY)
    print(f"\n  ✅ Actualizadas: {updated}  |  Errores: {errors}")
    return updated, errors


def lc_name(code):
    return LC_LABELS.get(code, code)


def print_preview(label, companies_info, id_list, new_stage, limit=20):
    print(f"\n── {label} → '{lc_name(new_stage)}'  ({len(id_list)} empresas) ──────────")
    for cid in id_list[:limit]:
        info   = companies_info.get(cid, {})
        actual = info.get("lifecyclestage", "?")
        print(f"  [{cid}]  {info.get('name','?'):<45}  {lc_name(actual)} → {lc_name(new_stage)}")
    if len(id_list) > limit:
        print(f"  ... y {len(id_list) - limit} más")


def save_csv(current, to_no_cal, to_nurture, to_prospect):
    """Guarda CSV con todas las empresas clasificadas."""
    stage_map = {}
    for cid in to_no_cal:
        stage_map[cid] = lc_name(LC_NO_CALIFICADO)
    for cid in to_nurture:
        stage_map[cid] = lc_name(LC_NURTURING)
    for cid in to_prospect:
        stage_map[cid] = lc_name(LC_PROSPECT)

    csv_path = f"reporte_lifecycle_plutoneta.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Nombre", "Estado actual", "Estado nuevo"])
        for cid, nuevo in stage_map.items():
            info = current.get(cid, {})
            writer.writerow([
                cid,
                info.get("name", ""),
                lc_name(info.get("lifecyclestage", "")),
                nuevo,
            ])
    print(f"\n📄 CSV guardado: {csv_path} ({len(stage_map)} empresas)")


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  LIFECYCLE UPDATE — Plutoneta AE")
    print(f"  Modo: {'👁️  DRY RUN (sin cambios)' if DRY_RUN else '✏️  APLICANDO CAMBIOS'}")
    print("=" * 65)

    if not HUBSPOT_TOKEN or HUBSPOT_TOKEN == "TU_TOKEN_AQUI":
        print("❌  Configura HUBSPOT_TOKEN antes de ejecutar.")
        return

    # ── TRAER TODOS LOS DEALS DEL PIPELINE ───────────────────
    print("\n🔍 Trayendo todos los deals de Plutoneta AE...")
    all_deals = search_all_deals(PIPELINE_PLUTONETA)
    print(f"   → {len(all_deals)} deals encontrados")

    all_deal_ids = [d for d, _, _ in all_deals]
    co_map = deals_to_companies(all_deal_ids)
    print(f"   → {len(co_map)} empresas con deals en el pipeline")

    # Stage del deal más reciente por empresa
    latest_stage = latest_deal_per_company(all_deals, co_map)

    # ── CLASIFICAR POR STAGE MÁS RECIENTE ────────────────────
    co_r1 = {cid for cid, stage in latest_stage.items()
             if stage in set(ETAPAS_NO_CALIFICADO)}

    co_r2 = {cid for cid, stage in latest_stage.items()
             if stage in set(ETAPAS_NURTURING)}

    # Empresas con deal activo (último deal NO es terminal)
    co_con_activos = {cid for cid, stage in latest_stage.items()
                      if stage not in PLUTTONETA_TERMINAL}

    print(f"\n   → Regla 1 (último deal = No calificado): {len(co_r1)}")
    print(f"   → Regla 2 (último deal = Nurturing):      {len(co_r2)}")
    print(f"   → Con deal activo:                        {len(co_con_activos)}")

    # ── REGLA 3: Nurturing lifecycle sin deal activo ──────────
    print("\n🔍 [Regla 3] Empresas con lifecycle Nurturing sin deal activo...")
    co_nurturing_lc = search_companies_by_lifecycle(LC_NURTURING)
    print(f"   → {len(co_nurturing_lc)} empresas con lifecycle Nurturing")
    co_r3_candidatos = co_nurturing_lc - co_con_activos
    print(f"   → {len(co_r3_candidatos)} sin deal activo")

    # ── APLICAR PRIORIDADES ───────────────────────────────────
    to_no_cal   = co_r1
    to_nurture  = co_r2 - co_r1
    to_prospect = co_r3_candidatos - co_r1 - co_r2

    print(f"\n🧮 Clasificación final:")
    print(f"   → No calificado (Regla 1):  {len(to_no_cal)}")
    print(f"   → Nurturing     (Regla 2):  {len(to_nurture)}")
    print(f"   → Prospect      (Regla 3):  {len(to_prospect)}")

    # ── LEER ESTADO ACTUAL ────────────────────────────────────
    all_ids = list(to_no_cal | to_nurture | to_prospect)
    print(f"\n🔍 Leyendo lifecycle actual de {len(all_ids)} empresas...")
    current = batch_read_companies(all_ids)

    # Filtrar solo las que realmente necesitan cambio
    to_no_cal_upd   = [c for c in to_no_cal   if current.get(c, {}).get("lifecyclestage") != LC_NO_CALIFICADO]
    to_nurture_upd  = [c for c in to_nurture  if current.get(c, {}).get("lifecyclestage") != LC_NURTURING]
    to_prospect_upd = [c for c in to_prospect if current.get(c, {}).get("lifecyclestage") != LC_PROSPECT]

    total_cambios = len(to_no_cal_upd) + len(to_nurture_upd) + len(to_prospect_upd)

    # ── PREVIEW ───────────────────────────────────────────────
    print_preview("No calificado (Regla 1)", current, to_no_cal_upd,   LC_NO_CALIFICADO)
    print_preview("Nurturing     (Regla 2)", current, to_nurture_upd,  LC_NURTURING)
    print_preview("Prospect      (Regla 3)", current, to_prospect_upd, LC_PROSPECT)

    print(f"\n{'─'*65}")
    print(f"  Total empresas a actualizar: {total_cambios}")

    save_csv(current, to_no_cal_upd, to_nurture_upd, to_prospect_upd)

    if DRY_RUN:
        print(f"\n✅ DRY RUN completado. Cambia DRY_RUN = False para aplicar.")
        return

    if total_cambios == 0:
        print("\n✅ Todo está al día. Nada que actualizar.")
        return

    confirm = input(
        f"\n⚠️  ¿Confirmas actualizar {total_cambios} empresas? (escribe 'SI'): "
    )
    if confirm.strip().upper() != "SI":
        print("❌ Cancelado.")
        return

    # ── APLICAR CAMBIOS ───────────────────────────────────────
    log = {"fecha": datetime.now().isoformat(), "reglas": {}}

    if to_no_cal_upd:
        print(f"\n✏️  Actualizando {len(to_no_cal_upd)} empresas → No calificado...")
        batch_update_companies(to_no_cal_upd, LC_NO_CALIFICADO)
        update_contacts_for_companies(to_no_cal_upd, LC_NO_CALIFICADO)
        log["reglas"]["no_calificado"] = [
            {"id": c, "nombre": current.get(c, {}).get("name"),
             "anterior": current.get(c, {}).get("lifecyclestage")}
            for c in to_no_cal_upd
        ]

    if to_nurture_upd:
        print(f"\n✏️  Actualizando {len(to_nurture_upd)} empresas → Nurturing...")
        batch_update_companies(to_nurture_upd, LC_NURTURING)
        update_contacts_for_companies(to_nurture_upd, LC_NURTURING)
        log["reglas"]["nurturing"] = [
            {"id": c, "nombre": current.get(c, {}).get("name"),
             "anterior": current.get(c, {}).get("lifecyclestage")}
            for c in to_nurture_upd
        ]

    if to_prospect_upd:
        print(f"\n✏️  Actualizando {len(to_prospect_upd)} empresas → Prospect...")
        batch_update_companies(to_prospect_upd, LC_PROSPECT)
        update_contacts_for_companies(to_prospect_upd, LC_PROSPECT)
        log["reglas"]["prospect"] = [
            {"id": c, "nombre": current.get(c, {}).get("name"),
             "anterior": current.get(c, {}).get("lifecyclestage")}
            for c in to_prospect_upd
        ]

    log_path = f"log_lifecycle_plutoneta_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Proceso completado.")
    print(f"   Log guardado en: {log_path}")


if __name__ == "__main__":
    main()