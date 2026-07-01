"""
Script: Revisar empresas en lifecycle "Evangelist"
====================================================
Lista todas las empresas con lifecyclestage = "evangelist" y muestra
sus deals en Pluttoneta AE y CS Renewal para decidir a qué stage moverlas.

INSTRUCCIONES:
  export HUBSPOT_TOKEN=pat-na1-...
  python3 revisar_evangelizadores.py
"""

import os
import requests
import time
import csv

HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
BATCH_SIZE    = 100
DELAY         = 0.3

PIPELINE_PLUTTONETA = "20361325"
PIPELINE_CS_RENEWAL = "33728953"

BASE_URL = "https://api.hubapi.com"

STAGE_NAMES = {
    # Pluttoneta AE
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
    # CS Renewal
    "994511463":  "Implementación",
    "1164813898": "Setup",
    "1164813897": "Kickoff",
    "1164813842": "Documentation & Advanced Setup",
    "1164813841": "Go-live & Handoff CS",
    "74579363":   "12-6 month",
    "78817538":   "3-6 month",
    "78817539":   "2-3 month",
    "78817540":   "1 month",
    "85334091":   "Pending",
    "78817541":   "Closed Won",
    "78817542":   "Closed Lost (CS)",
}

def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type": "application/json",
    }

def stage_name(sid):
    return STAGE_NAMES.get(str(sid), str(sid))

def search_evangelist_companies():
    """Busca todas las empresas con lifecyclestage = evangelist."""
    companies, after = [], None
    print("  Buscando empresas con lifecycle = evangelist...")
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "lifecyclestage", "operator": "EQ", "value": "evangelist"},
            ]}],
            "properties": ["name", "lifecyclestage", "country", "notes_last_contacted"],
            "limit": 200,
        }
        if after:
            body["after"] = after
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/search",
            headers=hdrs(), json=body
        )
        if r.status_code not in (200, 201):
            print(f"  ⚠️  Error ({r.status_code}): {r.text[:200]}")
            break
        data = r.json()
        for obj in data["results"]:
            props = obj["properties"]
            last_contact = props.get("notes_last_contacted") or ""
            if last_contact:
                last_contact = last_contact[:10]  # solo fecha YYYY-MM-DD
            companies.append({
                "id":           int(obj["id"]),
                "name":         props.get("name") or f"ID {obj['id']}",
                "country":      props.get("country") or "—",
                "last_contact": last_contact or "—",
            })
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)
    print(f"  → {len(companies)} empresas encontradas")
    return companies


def get_deals_for_companies(company_ids, pipeline_id, label):
    """
    Para una lista de company_ids, busca todos sus deals en el pipeline dado.
    Retorna {company_id: [(deal_id, stage, dealname, closedate)]}
    """
    # Primero obtenemos todos los deals del pipeline
    deals_pipeline, after = {}, None
    print(f"  Descargando deals de {label}...")
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
            ]}],
            "properties": ["dealstage", "dealname", "closedate"],
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
            deals_pipeline[int(d["id"])] = (
                d["properties"].get("dealstage", ""),
                d["properties"].get("dealname", ""),
                d["properties"].get("closedate", "") or "",
            )
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)

    if not deals_pipeline:
        return {}

    # Mapeamos deal → empresa
    co_deals = {}
    deal_ids = list(deals_pipeline.keys())
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
                    if cid in set(company_ids):
                        stage, name, closedate = deals_pipeline[did]
                        co_deals.setdefault(cid, []).append((did, stage, name, closedate))
        time.sleep(DELAY)

    return co_deals


def main():
    if not HUBSPOT_TOKEN:
        print("❌ Configura HUBSPOT_TOKEN antes de ejecutar.")
        print("   export HUBSPOT_TOKEN=pat-na1-...")
        return

    print("=" * 62)
    print("  REVISIÓN DE EMPRESAS — LIFECYCLE EVANGELIST")
    print("=" * 62)

    companies = search_evangelist_companies()
    if not companies:
        print("✅ No hay empresas con lifecycle evangelist.")
        return

    company_ids   = [c["id"] for c in companies]
    co_names      = {c["id"]: c["name"]         for c in companies}
    co_country    = {c["id"]: c["country"]      for c in companies}
    co_last_cont  = {c["id"]: c["last_contact"] for c in companies}

    # Deals en ambos pipelines
    ae_deals  = get_deals_for_companies(company_ids, PIPELINE_PLUTTONETA, "Pluttoneta AE")
    cs_deals  = get_deals_for_companies(company_ids, PIPELINE_CS_RENEWAL, "CS Renewal")

    def fmt_deals(deals_list):
        if not deals_list:
            return "—"
        return "  |  ".join(
            f"{stage_name(stage)}" + (f" ({name})" if name else "")
            for _, stage, name, _ in sorted(deals_list, key=lambda x: x[3], reverse=True)
        )

    def total_deals(cid):
        return len(ae_deals.get(cid, [])) + len(cs_deals.get(cid, []))

    # Reporte en consola
    print(f"\n{'Empresa':<45}  {'País':<15}  {'Último contacto':<16}  {'Deals':>5}  {'AE Deals':<55}  CS Renewal Deals")
    print("-" * 175)
    for cid in sorted(company_ids, key=lambda x: co_names[x].lower()):
        nombre   = co_names[cid]
        pais     = co_country[cid]
        last_c   = co_last_cont[cid]
        n_deals  = total_deals(cid)
        ae_str   = fmt_deals(ae_deals.get(cid))
        cs_str   = fmt_deals(cs_deals.get(cid))
        print(f"  {nombre:<43}  {pais:<15}  {last_c:<16}  {n_deals:>5}  {ae_str:<55}  {cs_str}")

    # CSV
    csv_path = "revisar_evangelizadores.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company_id", "nombre", "pais", "ultimo_contacto", "total_deals", "ae_deals", "cs_deals"
        ])
        writer.writeheader()
        for cid in sorted(company_ids, key=lambda x: co_names[x].lower()):
            writer.writerow({
                "company_id":      cid,
                "nombre":          co_names[cid],
                "pais":            co_country[cid],
                "ultimo_contacto": co_last_cont[cid],
                "total_deals":     total_deals(cid),
                "ae_deals":        fmt_deals(ae_deals.get(cid)),
                "cs_deals":        fmt_deals(cs_deals.get(cid)),
            })
    print(f"\n📄 CSV guardado: {csv_path} ({len(companies)} empresas)")


if __name__ == "__main__":
    main()
