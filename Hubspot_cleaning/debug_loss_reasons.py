"""
Script diagnóstico: ver todas las propiedades disponibles en deals
y los valores reales de loss reason en Pluttoneta AE.
"""
import os
import time
import requests

HUBSPOT_TOKEN      = os.getenv("HUBSPOT_TOKEN", "")
BASE_URL           = "https://api.hubapi.com"
PIPELINE_PLUTONETA = "20361325"
ETAPAS_NO_CALIFICADO = ["49655667", "1044035701"]

def hdrs():
    return {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}


def get_loss_properties():
    """Lista todas las propiedades de deals que contienen 'use case' o 'plutto' en el nombre."""
    r = requests.get(f"{BASE_URL}/crm/v3/properties/deals", headers=hdrs())
    if r.status_code != 200:
        print(f"❌ Error: {r.status_code} — {r.text[:200]}")
        return
    props = r.json().get("results", [])
    relevantes = [p for p in props if any(k in p["name"].lower() or k in p.get("label","").lower() for k in ["use_case", "use case", "plutto", "caso"])]
    print("\n── Propiedades relacionadas con use case / plutto ───")
    for p in relevantes:
        print(f"  name:  {p['name']}")
        print(f"  label: {p.get('label','')}")
        if p.get("options"):
            for opt in p["options"]:
                print(f"         → [{opt['value']}] {opt['label']}")
        print()


def sample_deals_with_all_props():
    """Toma 5 deals de etapas no calificado e imprime TODAS sus propiedades con valor."""
    body = {
        "filterGroups": [{"filters": [
            {"propertyName": "pipeline",  "operator": "EQ", "value": PIPELINE_PLUTONETA},
            {"propertyName": "dealstage", "operator": "IN", "values": ETAPAS_NO_CALIFICADO},
        ]}],
        "properties": [],  # vacío = devuelve todas las propiedades por defecto
        "limit": 5,
    }
    r = requests.post(f"{BASE_URL}/crm/v3/objects/deals/search", headers=hdrs(), json=body)
    if r.status_code not in (200, 201):
        print(f"❌ Error buscando deals: {r.status_code} — {r.text[:300]}")
        return

    deals = r.json().get("results", [])
    print(f"\n── Propiedades con valor en {len(deals)} deals de muestra ──")
    for d in deals:
        print(f"\n  Deal ID: {d['id']}  |  Nombre: {d['properties'].get('dealname','?')}")
        for k, v in sorted(d["properties"].items()):
            if v:
                print(f"    {k}: {v}")


if __name__ == "__main__":
    if not HUBSPOT_TOKEN:
        print("❌ Falta HUBSPOT_TOKEN — export HUBSPOT_TOKEN=pat-na1-...")
    else:
        get_loss_properties()
        sample_deals_with_all_props()
