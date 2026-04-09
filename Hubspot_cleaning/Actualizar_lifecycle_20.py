"""
Script: Actualizar Lifecycle Stage
====================================
Clasifica y actualiza el lifecyclestage de empresas en HubSpot
según sus deals en los pipelines Pluttoneta AE y CS Renewal.

Prioridad (de mayor a menor):
  1. CHURNED > 2. LIVE CUSTOMER > 3. IMPLEMENTACIÓN > 4. PROSPECT

─────────────────────────────────────────────────────────────
REGLAS (se evalúan en orden de prioridad)
─────────────────────────────────────────────────────────────

1. CHURNED  (lifecyclestage → 50020179)
   · El deal más reciente en CS Renewal está en "Closed Lost"

2. LIVE CUSTOMER  (lifecyclestage → 52560399)
   · Tiene al menos un deal en CS Renewal en etapa activa:
     Go Live & Hand Off / 12-6 meses / 2-3 meses / 1 month / Closed Won
   · (Se aplica aunque también tenga un Closed Lost en CS Renewal,
     siempre que el deal más reciente NO sea Closed Lost)

3. IMPLEMENTACIÓN  (lifecyclestage → 52531545)
   · Tiene deal en CS Renewal en etapa temprana:
     Implementación / Setup / Kick Off / Documentation & Advance
   · Y NO tiene ningún deal en etapa Live Customer

4. PROSPECT  (lifecyclestage → "opportunity")
   · Tiene deal activo en Pluttoneta AE
   · Y NO tiene ningún deal en CS Renewal (ni temprano ni activo)
   · Excepción: si ya es Live Customer (52560399), se imprime
     para revisión manual pero NO se mueve

─────────────────────────────────────────────────────────────
PIPELINES:
  Pluttoneta AE  → 20361325
  CS Renewal     → 33728953

INSTRUCCIONES:
  1. export HUBSPOT_TOKEN=pat-na1-...
  2. Corre con DRY_RUN = True para revisar qué empresas se actualizarán
  3. Cambia DRY_RUN = False para aplicar los cambios
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
HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")   # export HUBSPOT_TOKEN=pat-na1-...
DRY_RUN       = True
BATCH_SIZE    = 100
DELAY         = 0.3   # segundos entre requests
# ─────────────────────────────────────────────────────────────

PIPELINE_PLUTTONETA = "20361325"
PIPELINE_CS_RENEWAL = "33728953"

# Pluttoneta AE — etapas activas (prob > 0.01)
PLUTTONETA_ACTIVE = [
    "144033658",   # Appointment Scheduled
    "172924342",   # BDR Precalificación
    "996295554",   # Consulting Discovery AE
    "1028970392",  # DEAL CALIFICADO (SQO)
    "49686858",    # Demostración
    "49686857",    # Economica Propuesta
    "98993471",    # Interest Confirmed
    "49686859",    # Negotiation
    "74323563",    # Pilot
    "49963372",    # Verbal Yes
    "1163283618",  # W - Iterando Contratos
    "49655666",    # Won
    "1007516181",  # One Shoot Cerrado
]

# CS Renewal — etapas Implementación
CS_EARLY = [
    "994511463",   # Implementación
    "1164813898",  # Setup
    "1164813897",  # Kickoff
    "1164813842",  # Documentation & Advanced Setup
]

# CS Renewal — etapas Live Customer
CS_LIVE = [
    "1164813841",  # Go-live & Handoff CS
    "74579363",    # 12-6 month
    "78817538",    # 3-6 month
    "78817539",    # 2-3 month
    "78817540",    # 1 month
    "85334091",    # Pending
    "78817541",    # Closed Won
]

CS_CLOSED_LOST = "78817542"

# Lifecycle stage IDs del portal
LC_LIVE_CUSTOMER  = "52560399"   # Live Customer
LC_IMPLEMENTACION = "52531545"   # Implementación
LC_CHURNED        = "50020179"   # Churned

BASE_URL = "https://api.hubapi.com"


def print_pipeline_stages(pipeline_id, label):
    """Imprime todas las etapas de un pipeline con sus IDs reales."""
    r = requests.get(
        f"{BASE_URL}/crm/v3/pipelines/deals/{pipeline_id}/stages",
        headers={"Authorization": f"Bearer {HUBSPOT_TOKEN}"},
    )
    if r.status_code != 200:
        print(f"  ⚠️  Error leyendo etapas ({r.status_code}): {r.text[:200]}")
        return
    print(f"\n── Etapas de {label} (pipeline {pipeline_id}) ──")
    for s in r.json().get("results", []):
        print(f"  {s['id']:<15}  {s['label']}")


def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────────────────────
#  FUNCIONES DE BÚSQUEDA
# ─────────────────────────────────────────────────────────────

def search_deals(pipeline, stages):
    """
    Retorna lista de (deal_id, stage, closedate) para todos los deals
    en el pipeline y etapas dados.
    """
    deals, after = [], None
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline",  "operator": "EQ",  "value": pipeline},
                {"propertyName": "dealstage", "operator": "IN",  "values": stages},
            ]}],
            "properties": ["dealstage", "closedate"],
            "limit": 200,
        }
        if after:
            body["after"] = after
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/deals/search",
            headers=hdrs(), json=body
        )
        if r.status_code not in (200, 201):
            print(f"  ⚠️  Error buscando deals ({r.status_code}): {r.text[:200]}")
            break
        data = r.json()
        for d in data["results"]:
            deals.append((
                int(d["id"]),
                d["properties"]["dealstage"],
                d["properties"].get("closedate") or "",
            ))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)
    return deals


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
            print(f"  ⚠️  Error asociaciones ({r.status_code}): {r.text[:200]}")
        time.sleep(DELAY)
    return co_map


# ─────────────────────────────────────────────────────────────
#  FUNCIONES DE LECTURA / ESCRITURA
# ─────────────────────────────────────────────────────────────

def batch_read_companies(ids):
    """Lee nombre y lifecyclestage actual de un batch de empresas."""
    result = {}
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/read",
            headers=hdrs(),
            json={"inputs": [{"id": str(c)} for c in batch],
                  "properties": ["name", "lifecyclestage"]},
        )
        if r.status_code in (200, 201):
            for obj in r.json().get("results", []):
                result[int(obj["id"])] = {
                    "name":           obj["properties"].get("name", "Sin nombre"),
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
    """Actualiza lifecyclestage de contactos en batches.
    Resetea primero para poder mover a cualquier stage.
    """
    updated = errors = 0
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]

        # Paso 1: limpiar
        requests.post(
            f"{BASE_URL}/crm/v3/objects/contacts/batch/update",
            headers=hdrs(),
            json={"inputs": [
                {"id": str(c), "properties": {"lifecyclestage": ""}}
                for c in batch
            ]},
        )
        time.sleep(DELAY)

        # Paso 2: asignar
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


def update_contacts_for_companies(company_ids, new_stage):
    """Obtiene contactos de las empresas y los actualiza al mismo lifecycle."""
    if not company_ids:
        return
    print(f"  → Obteniendo contactos de {len(company_ids)} empresas...")
    co_contacts = get_contacts_of_companies(company_ids)
    contact_ids = list({cid for contacts in co_contacts.values() for cid in contacts})
    print(f"  → {len(contact_ids)} contactos a actualizar → {new_stage}")
    if contact_ids:
        batch_update_contacts(contact_ids, new_stage)


def batch_update_companies(ids, new_stage):
    """Actualiza lifecyclestage en batches.
    Primero resetea a '' para poder mover a cualquier stage (HubSpot solo
    permite avanzar, no retroceder, si no se limpia primero).
    """
    updated = errors = 0
    total = len(ids)
    for i in range(0, total, BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]

        # Paso 1: limpiar lifecycle stage
        requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/update",
            headers=hdrs(),
            json={"inputs": [
                {"id": str(c), "properties": {"lifecyclestage": ""}}
                for c in batch
            ]},
        )
        time.sleep(DELAY)

        # Paso 2: asignar el nuevo stage
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


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 62)
    print("  LIFECYCLE UPDATE: PROSPECT & LIVE CUSTOMER")
    print(f"  Modo: {'👁️  DRY RUN (sin cambios)' if DRY_RUN else '✏️  APLICANDO CAMBIOS'}")
    print("=" * 62)

    if HUBSPOT_TOKEN == "TU_TOKEN_AQUI":
        print("❌  Configura HUBSPOT_TOKEN antes de ejecutar.")
        return

    # ── 0. VERIFICAR IDs DE ETAPAS ────────────────────────────
    print_pipeline_stages(PIPELINE_CS_RENEWAL, "CS Renewal")
    print_pipeline_stages(PIPELINE_PLUTTONETA, "Pluttoneta AE")

    # Lifecycle stages del portal
    r_lc = requests.get(
        f"{BASE_URL}/crm/v3/properties/companies/lifecyclestage",
        headers={"Authorization": f"Bearer {HUBSPOT_TOKEN}"},
    )
    if r_lc.status_code == 200:
        print("\n── Lifecycle stages del portal ──")
        for opt in r_lc.json().get("options", []):
            print(f"  {opt['value']:<20}  {opt['label']}")
    print()

    # ── 1. PLUTTONETA AE ──────────────────────────────────────
    print("\n🔍 Buscando deals activos en Pluttoneta AE...")
    plt_deals = search_deals(PIPELINE_PLUTTONETA, PLUTTONETA_ACTIVE)
    print(f"   → {len(plt_deals)} deals encontrados")

    plt_deal_ids = [d for d, _, _ in plt_deals]
    plt_co_map   = deals_to_company_map_from_deals(plt_deals)
    plt_companies = set(plt_co_map.keys())
    print(f"   → {len(plt_companies)} empresas únicas")

    # ── 2. CS RENEWAL ─────────────────────────────────────────
    all_cs_stages = CS_EARLY + CS_LIVE + [CS_CLOSED_LOST]
    print("\n🔍 Buscando deals en CS Renewal (todas las etapas relevantes)...")
    cs_deals = search_deals(PIPELINE_CS_RENEWAL, all_cs_stages)
    print(f"   → {len(cs_deals)} deals encontrados")

    # Mapa deal_id → (stage, closedate)
    cs_deal_info = {did: (stage, closedate) for did, stage, closedate in cs_deals}

    # Mapa company_id → deal_ids en CS Renewal
    cs_co_map = deals_to_companies([d for d, _, _ in cs_deals])

    # Mapa company_id → set de stages en CS Renewal
    cs_co_stages = {
        cid: {cs_deal_info[did][0] for did in dids if did in cs_deal_info}
        for cid, dids in cs_co_map.items()
    }
    print(f"   → {len(cs_co_stages)} empresas con deals en CS Renewal")

    # ── 3. CLASIFICACIÓN ──────────────────────────────────────
    print("\n🧮 Clasificando empresas...")
    prospects        = []
    implementaciones = []
    live_customers   = []
    churned          = []

    all_companies = plt_companies | set(cs_co_stages.keys())

    for cid in all_companies:
        stages = cs_co_stages.get(cid, set())

        has_live   = bool(stages & set(CS_LIVE))
        has_early  = bool(stages & set(CS_EARLY))
        no_renewal = len(stages) == 0

        # Último deal de esta empresa en CS Renewal
        deals_de_empresa = cs_co_map.get(cid, set())
        ultimo_deal_stage = None
        if deals_de_empresa:
            ultimo_did = max(
                deals_de_empresa,
                key=lambda did: cs_deal_info.get(did, ("", ""))[1] or ""
            )
            ultimo_deal_stage = cs_deal_info.get(ultimo_did, ("", ""))[0]

        if ultimo_deal_stage == CS_CLOSED_LOST:
            # ✅ Churned: el último deal en CS Renewal está en Closed Lost
            churned.append(cid)
        elif has_live:
            # ✅ Live Customer (con o sin Closed Lost, si hay deal activo gana)
            live_customers.append(cid)
        elif has_early:
            # ✅ Implementación: tiene deal en CS Renewal en etapas tempranas, sin Live
            implementaciones.append(cid)
        elif cid in plt_companies and no_renewal:
            # ✅ Prospect: en Pluttoneta AE sin ningún deal en CS Renewal
            prospects.append(cid)

    print(f"   → Prospects:        {len(prospects)}")
    print(f"   → Implementaciones: {len(implementaciones)}")
    print(f"   → Live Customers:   {len(live_customers)}")
    print(f"   → Churned:          {len(churned)}")

    # ── 4. LEER ESTADO ACTUAL ─────────────────────────────────
    all_ids = list(set(prospects + implementaciones + live_customers + churned))
    print(f"\n🔍 Leyendo lifecycle actual de {len(all_ids)} empresas...")
    current = batch_read_companies(all_ids)

    # Debug: stages en CS Renewal para empresas en Implementación
    print("\n  🔍 DEBUG — stages en CS Renewal para empresas clasificadas como Implementación:")
    for cid in implementaciones:
        name = current.get(cid, {}).get("name", f"ID {cid}")
        stages = cs_co_stages.get(cid, set())
        print(f"     {name:<45}  stages={stages}")

    # Valores que equivalen a "ya es cliente" (valor viejo "customer" o nuevo LC_LIVE_CUSTOMER)
    ES_CLIENTE = (LC_LIVE_CUSTOMER, "customer")

    # Filtrar los que realmente necesitan cambio
    # Empresas que ya son Live Customer y clasificarían como Prospect → NO mover, solo reportar
    prospects_era_customer = [c for c in prospects
                              if current.get(c, {}).get("lifecyclestage") in ES_CLIENTE]
    to_prospect       = [c for c in prospects
                         if current.get(c, {}).get("lifecyclestage") not in ("opportunity",) + ES_CLIENTE]
    # No bajar a Implementación si ya es cliente (LC_LIVE_CUSTOMER o "customer")
    to_implementacion = [c for c in implementaciones
                         if current.get(c, {}).get("lifecyclestage") not in (LC_IMPLEMENTACION,) + ES_CLIENTE]
    to_customer       = [c for c in live_customers
                         if current.get(c, {}).get("lifecyclestage") != LC_LIVE_CUSTOMER]
    to_churned        = [c for c in churned
                         if current.get(c, {}).get("lifecyclestage") != LC_CHURNED]

    # ── 5. REPORTE ────────────────────────────────────────────
    if prospects_era_customer:
        print(f"\n── ⚠️  Revisar manualmente ({len(prospects_era_customer)}) — clasifican como Prospect pero ya son Live Customer ──")
        for cid in prospects_era_customer:
            p = current.get(cid, {})
            print(f"  {p.get('name','?'):<45}  Live Customer → (sin cambio, revisar)")

    print(f"\n── Prospects a actualizar ({len(to_prospect)}) ─────────────────")
    for cid in to_prospect[:25]:
        p = current.get(cid, {})
        print(f"  {p.get('name','?'):<45}  {p.get('lifecyclestage','?')} → opportunity")
    if len(to_prospect) > 25:
        print(f"  ... y {len(to_prospect) - 25} más")

    print(f"\n── Implementación a actualizar ({len(to_implementacion)}) ──────")
    for cid in to_implementacion[:25]:
        p = current.get(cid, {})
        print(f"  {p.get('name','?'):<45}  {p.get('lifecyclestage','?')} → Implementación")
    if len(to_implementacion) > 25:
        print(f"  ... y {len(to_implementacion) - 25} más")

    print(f"\n── Live Customers a actualizar ({len(to_customer)}) ────────────")
    for cid in to_customer[:25]:
        p = current.get(cid, {})
        print(f"  {p.get('name','?'):<45}  {p.get('lifecyclestage','?')} → Live Customer")
    if len(to_customer) > 25:
        print(f"  ... y {len(to_customer) - 25} más")

    print(f"\n── Churned a actualizar ({len(to_churned)}) ─────────────────────")
    for cid in to_churned[:25]:
        p = current.get(cid, {})
        print(f"  {p.get('name','?'):<45}  {p.get('lifecyclestage','?')} → churned")
    if len(to_churned) > 25:
        print(f"  ... y {len(to_churned) - 25} más")

    total_cambios = len(to_prospect) + len(to_implementacion) + len(to_customer) + len(to_churned)

    # ── CSV con todas las empresas ────────────────────────────
    nuevo_stage = {}
    for c in to_prospect:
        nuevo_stage[c] = "opportunity"
    for c in to_implementacion:
        nuevo_stage[c] = "Implementación"
    for c in to_customer:
        nuevo_stage[c] = "Live Customer"
    for c in to_churned:
        nuevo_stage[c] = "other (churned)"

    csv_path = "reporte_lifecycle.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Empresa", "Estado actual", "Estado a cambiar"])
        for cid in all_ids:
            p = current.get(cid, {})
            writer.writerow([
                p.get("name", f"ID {cid}"),
                p.get("lifecyclestage", "sin valor"),
                nuevo_stage.get(cid, "sin cambio"),
            ])
    print(f"\n📄 CSV guardado: {csv_path} ({len(all_ids)} empresas)")

    if DRY_RUN:
        print(f"\n✅ DRY RUN completado.")
        print(f"   {total_cambios} empresas se actualizarían.")
        print(f"   Cambia DRY_RUN = False para aplicar los cambios.")
        return

    if total_cambios == 0:
        print("\n✅ Todo está al día. Nada que actualizar.")
        return

    confirm = input(
        f"\n⚠️  ¿Confirmas actualizar {len(to_prospect)} Prospects, "
        f"{len(to_implementacion)} Implementación, "
        f"{len(to_customer)} Live Customers y {len(to_churned)} Churned? (escribe 'SI'): "
    )
    if confirm.strip().upper() != "SI":
        print("❌ Cancelado.")
        return

    log = {
        "fecha":           datetime.now().isoformat(),
        "prospects":       [],
        "implementaciones":[],
        "live_customers":  [],
        "churned":         [],
    }

    if to_prospect:
        print(f"\n✏️  Actualizando {len(to_prospect)} Prospects → opportunity...")
        batch_update_companies(to_prospect, "opportunity")
        update_contacts_for_companies(to_prospect, "opportunity")
        log["prospects"] = [
            {"id": c, "nombre": current.get(c, {}).get("name"),
             "anterior": current.get(c, {}).get("lifecyclestage")}
            for c in to_prospect
        ]

    if to_implementacion:
        print(f"\n✏️  Actualizando {len(to_implementacion)} Implementación → {LC_IMPLEMENTACION}...")
        batch_update_companies(to_implementacion, LC_IMPLEMENTACION)
        update_contacts_for_companies(to_implementacion, LC_IMPLEMENTACION)
        log["implementaciones"] = [
            {"id": c, "nombre": current.get(c, {}).get("name"),
             "anterior": current.get(c, {}).get("lifecyclestage")}
            for c in to_implementacion
        ]

    if to_customer:
        print(f"\n✏️  Actualizando {len(to_customer)} Live Customers → {LC_LIVE_CUSTOMER}...")
        batch_update_companies(to_customer, LC_LIVE_CUSTOMER)
        update_contacts_for_companies(to_customer, LC_LIVE_CUSTOMER)
        log["live_customers"] = [
            {"id": c, "nombre": current.get(c, {}).get("name"),
             "anterior": current.get(c, {}).get("lifecyclestage")}
            for c in to_customer
        ]

    if to_churned:
        print(f"\n✏️  Actualizando {len(to_churned)} Churned → other...")
        batch_update_companies(to_churned, LC_CHURNED)
        update_contacts_for_companies(to_churned, LC_CHURNED)
        log["churned"] = [
            {"id": c, "nombre": current.get(c, {}).get("name"),
             "anterior": current.get(c, {}).get("lifecyclestage")}
            for c in to_churned
        ]

    with open("log_lifecycle_update.json", "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Proceso completado.")
    print(f"   Log guardado en: log_lifecycle_update.json")


def deals_to_company_map_from_deals(deals):
    """Dado lista de (deal_id, stage), retorna {company_id: set(deal_ids)}."""
    deal_ids = [d for d, _, _ in deals]
    return deals_to_companies(deal_ids)


if __name__ == "__main__":
    main()