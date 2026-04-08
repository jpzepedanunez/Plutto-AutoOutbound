"""
Script: Loss Detail — Deals No Calificados en Pluttoneta AE
============================================================
Busca todos los deals en Pluttoneta AE en etapas:
  · Z - Closed Lost       (49655667)
  · Deal No Calificado    (1044035701)

Extrae el atributo `hs_deal_stage_probability_shadow_roll_up` (loss reason)
y filtra por los contextos:
  · Mal Calificado
  · Volumen Muy pequeño
  · No necesitaban la herramienta

Genera CSV: empresa | loss_detail

INSTRUCCIONES:
  export HUBSPOT_TOKEN=pat-na1-...
  python3 loss_detail_no_calificado.py
"""

import os
import csv
import time
import requests

# ─────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
BATCH_SIZE    = 100
DELAY         = 0.3
OUTPUT_CSV    = "loss_detail_no_calificado.csv"
# ─────────────────────────────────────────────────────────────

BASE_URL           = "https://api.hubapi.com"
PIPELINE_PLUTONETA = "20361325"
PIPELINE_CS_RENEWAL = "33728953"

ETAPAS_NO_CALIFICADO = {
    "49655667",    # Z - Closed Lost
    "1044035701",  # Deal No Calificado
}

LOSS_DETAIL_FILTROS = [
    "low volume",
    "price",
]


def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }


def search_deals_no_calificados():
    """Retorna lista de (deal_id, loss_detail) para deals en etapas no calificado."""
    deals, after = [], None

    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline",   "operator": "EQ", "value": PIPELINE_PLUTONETA},
                {"propertyName": "dealstage",  "operator": "IN", "values": list(ETAPAS_NO_CALIFICADO)},
            ]}],
            "properties": ["dealname", "dealstage", "hs_analytics_latest_source"],
            "limit": 200,
        }
        if after:
            body["after"] = after

        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/deals/search",
            headers=hdrs(), json=body,
        )
        if r.status_code not in (200, 201):
            print(f"  ⚠️  Error buscando deals ({r.status_code}): {r.text[:300]}")
            break

        data = r.json()
        for d in data["results"]:
            deals.append(int(d["id"]))

        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)

    return deals


def get_deal_loss_details(deal_ids):
    """
    Lee loss_reason (closed_lost_reason) de cada deal en batch.
    Retorna {deal_id: loss_detail}
    """
    result = {}
    for i in range(0, len(deal_ids), BATCH_SIZE):
        batch = deal_ids[i : i + BATCH_SIZE]
        payload = {
            "inputs":     [{"id": str(d)} for d in batch],
            "properties": ["dealname", "dealstage", "closed_lost_reason", "plutto_use_case"],
        }
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/deals/batch/read",
            headers=hdrs(), json=payload,
        )
        if r.status_code in (200, 201):
            for obj in r.json().get("results", []):
                did        = int(obj["id"])
                loss_detail  = obj["properties"].get("closed_lost_reason") or ""
                use_case     = obj["properties"].get("plutto_use_case") or ""
                result[did]  = (loss_detail, use_case)
        else:
            print(f"  ⚠️  Error batch/read deals ({r.status_code}): {r.text[:200]}")
        time.sleep(DELAY)

    return result


def get_companies_in_cs_renewal():
    """Retorna set de company_ids que tienen al menos un deal en CS Renewal."""
    company_ids, after = set(), None

    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_CS_RENEWAL},
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
            print(f"  ⚠️  Error buscando CS Renewal ({r.status_code}): {r.text[:200]}")
            break

        deal_ids_renewal = [int(d["id"]) for d in r.json().get("results", [])]

        # Obtener empresas asociadas a estos deals
        for i in range(0, len(deal_ids_renewal), BATCH_SIZE):
            batch = deal_ids_renewal[i : i + BATCH_SIZE]
            assoc = requests.post(
                f"{BASE_URL}/crm/v4/associations/deals/companies/batch/read",
                headers=hdrs(),
                json={"inputs": [{"id": str(d)} for d in batch]},
            )
            if assoc.status_code in (200, 201, 207):
                for item in assoc.json().get("results", []):
                    for a in item.get("to", []):
                        company_ids.add(int(a["toObjectId"]))
            time.sleep(DELAY)

        after = (r.json().get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)

    return company_ids


def deals_to_companies(deal_ids):
    """Retorna {deal_id: (company_id, company_name)} usando asociaciones v4."""
    deal_company = {}
    ids = list(deal_ids)

    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        r = requests.post(
            f"{BASE_URL}/crm/v4/associations/deals/companies/batch/read",
            headers=hdrs(),
            json={"inputs": [{"id": str(d)} for d in batch]},
        )
        if r.status_code in (200, 201, 207):
            company_ids_needed = []
            deal_to_cid = {}
            for item in r.json().get("results", []):
                did  = int(item["from"]["id"])
                tos  = item.get("to", [])
                if tos:
                    cid = int(tos[0]["toObjectId"])
                    deal_to_cid[did] = cid
                    company_ids_needed.append(cid)

            if company_ids_needed:
                names = get_company_names(company_ids_needed)
                for did, cid in deal_to_cid.items():
                    deal_company[did] = (cid, names.get(cid, f"ID {cid}"))
        else:
            print(f"  ⚠️  Error asociaciones ({r.status_code}): {r.text[:200]}")
        time.sleep(DELAY)

    return deal_company


def get_company_names(company_ids):
    """Retorna {company_id: name} para una lista de IDs."""
    result = {}
    for i in range(0, len(company_ids), BATCH_SIZE):
        batch = company_ids[i : i + BATCH_SIZE]
        payload = {
            "inputs":     [{"id": str(c)} for c in batch],
            "properties": ["name"],
        }
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/read",
            headers=hdrs(), json=payload,
        )
        if r.status_code in (200, 201):
            for obj in r.json().get("results", []):
                result[int(obj["id"])] = obj["properties"].get("name", "Sin nombre")
        else:
            print(f"  ⚠️  Error leyendo empresas ({r.status_code}): {r.text[:200]}")
        time.sleep(DELAY)
    return result


LOSS_DETAIL_EXCLUIR = [
    "not an actual priority",
]

def matches_filtro(loss_detail: str) -> bool:
    """Retorna True si el loss_detail contiene alguno de los contextos buscados
    y NO contiene ninguna razón de exclusión."""
    text = loss_detail.lower()
    if any(e in text for e in LOSS_DETAIL_EXCLUIR):
        return False
    return any(f in text for f in LOSS_DETAIL_FILTROS)


def main():
    if not HUBSPOT_TOKEN:
        print("❌ Configura HUBSPOT_TOKEN antes de ejecutar.")
        print("   export HUBSPOT_TOKEN=pat-na1-...")
        return

    print("=" * 60)
    print("  LOSS DETAIL — DEALS NO CALIFICADOS")
    print("=" * 60)

    print("\n🔍 Buscando deals en etapas No Calificado...")
    deal_ids = search_deals_no_calificados()
    print(f"   Encontrados: {len(deal_ids):,} deals")

    print("\n📋 Leyendo loss detail de cada deal...")
    loss_map = get_deal_loss_details(deal_ids)

    print("\n🔎 Obteniendo empresas con deals en CS Renewal (clientes activos)...")
    cs_renewal_companies = get_companies_in_cs_renewal()
    print(f"   Empresas con CS Renewal: {len(cs_renewal_companies):,} — serán excluidas")

    print("\n🏢 Obteniendo empresas asociadas...")
    company_map = deals_to_companies(deal_ids)

    # Filtrar por contextos relevantes y excluir clientes CS Renewal
    filas = []
    sin_loss_detail = 0
    excluidas_cs    = 0

    for did in deal_ids:
        loss_tuple    = loss_map.get(did, ("", ""))
        loss_detail, use_case = loss_tuple
        company_tuple = company_map.get(did)
        if not company_tuple:
            continue
        cid, empresa = company_tuple

        # Excluir empresas que tienen deal en CS Renewal
        if cid in cs_renewal_companies:
            excluidas_cs += 1
            continue

        if not loss_detail:
            sin_loss_detail += 1
            continue

        if matches_filtro(loss_detail):
            filas.append({
                "empresa":      empresa,
                "loss_detail":  loss_detail,
                "plutto_use_case": use_case,
            })

    # Ordenar por empresa
    filas.sort(key=lambda x: x["empresa"].lower())

    print(f"\n── Resultados ──────────────────────────────────────")
    print(f"   Total deals analizados:       {len(deal_ids):,}")
    print(f"   Excluidas (tienen CS Renewal):{excluidas_cs:,}")
    print(f"   Sin loss detail:              {sin_loss_detail:,}")
    print(f"   Con contexto relevante:       {len(filas):,}")

    if filas:
        print(f"\n{'Empresa':<50}  {'Loss Detail':<35}  Plutto Use Case")
        print("-" * 110)
        for f in filas:
            print(f"  {f['empresa']:<48}  {f['loss_detail']:<33}  {f['plutto_use_case']}")

    # Guardar CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["empresa", "loss_detail", "plutto_use_case"])
        writer.writeheader()
        writer.writerows(filas)

    print(f"\n✅ CSV guardado: {OUTPUT_CSV}  ({len(filas)} filas)")


if __name__ == "__main__":
    main()
