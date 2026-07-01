"""
Script: Limpiar Evangelistas
==============================
Procesa todas las empresas con lifecycle = "evangelist" en tres grupos:

  1a. ELIMINAR  — país fuera del ICP (no Chile/México/Perú) + sin deals
  1b. → LEAD    — país ICP (Chile/México/Perú) + sin deals
  1c. REVISAR   — tienen al menos 1 deal (AE o CS), sin importar país

INSTRUCCIONES:
  1. export HUBSPOT_TOKEN=pat-na1-...
  2. Corre con DRY_RUN = True para ver el reporte completo y el CSV de respaldo
  3. Cambia DRY_RUN = False para ejecutar
"""

import os
import requests
import time
import csv
import json
from datetime import datetime

# ─────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
DRY_RUN       = True   # False = aplica cambios reales

BATCH_SIZE = 100
DELAY      = 0.3
# ─────────────────────────────────────────────────────────────

PIPELINE_PLUTTONETA = "20361325"
PIPELINE_CS_RENEWAL = "33728953"

PAISES_ICP = {"chile", "méxico", "mexico", "perú", "peru"}

BASE_URL = "https://api.hubapi.com"


def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────────────────────
#  BÚSQUEDA Y MAPEO
# ─────────────────────────────────────────────────────────────

def search_evangelist_companies():
    companies, after = [], None
    print("  Buscando empresas con lifecycle = evangelist...")
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "lifecyclestage", "operator": "EQ", "value": "evangelist"},
            ]}],
            "properties": [
                "name", "domain", "country", "industry",
                "num_associated_contacts", "hs_lastmodifieddate", "notes_last_contacted",
            ],
            "limit": 200,
        }
        if after:
            body["after"] = after
        r = requests.post(f"{BASE_URL}/crm/v3/objects/companies/search", headers=hdrs(), json=body)
        if r.status_code not in (200, 201):
            print(f"  ⚠️  Error ({r.status_code}): {r.text[:200]}")
            break
        data = r.json()
        for obj in data["results"]:
            p = obj["properties"]
            last_mod = (p.get("hs_lastmodifieddate") or "")[:10]
            companies.append({
                "id":           int(obj["id"]),
                "name":         p.get("name") or "",
                "domain":       p.get("domain") or "",
                "country":      p.get("country") or "",
                "industry":     p.get("industry") or "",
                "n_contacts":   int(p.get("num_associated_contacts") or 0),
                "last_mod":     last_mod,
            })
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)
    print(f"  → {len(companies)} empresas encontradas")
    return companies


def get_deal_count_per_company(company_ids, pipeline_id):
    """Retorna {company_id: n_deals} para el pipeline dado."""
    # Obtener todos los deals del pipeline
    deal_ids, after = [], None
    while True:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
            ]}],
            "properties": ["dealstage", "dealname"],
            "limit": 200,
        }
        if after:
            body["after"] = after
        r = requests.post(f"{BASE_URL}/crm/v3/objects/deals/search", headers=hdrs(), json=body)
        if r.status_code not in (200, 201):
            break
        data = r.json()
        for d in data["results"]:
            deal_ids.append((int(d["id"]), d["properties"].get("dealstage", ""), d["properties"].get("dealname", "")))
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)

    if not deal_ids:
        return {}

    # Mapear deal → empresa, solo para nuestras empresas
    co_set = set(company_ids)
    co_deals = {}
    ids = [did for did, _, _ in deal_ids]
    deal_meta = {did: (stage, name) for did, stage, name in deal_ids}

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
                    if cid in co_set:
                        stage, name = deal_meta.get(did, ("", ""))
                        co_deals.setdefault(cid, []).append(f"{stage}|{name}")
        time.sleep(DELAY)

    return {cid: len(v) for cid, v in co_deals.items()}


def get_contacts_of_company(company_id):
    """Retorna lista de contact_ids asociados a la empresa."""
    r = requests.get(
        f"{BASE_URL}/crm/v4/objects/companies/{company_id}/associations/contacts",
        headers=hdrs(),
    )
    if r.status_code != 200:
        return []
    return [int(item["toObjectId"]) for item in r.json().get("results", [])]


def get_contact_company_count(contact_id):
    """Cuántas empresas tiene asociadas el contacto."""
    r = requests.get(
        f"{BASE_URL}/crm/v4/objects/contacts/{contact_id}/associations/companies",
        headers=hdrs(),
    )
    if r.status_code != 200:
        return 99  # asumir que tiene más para no borrarlo
    return len(r.json().get("results", []))


def delete_companies(ids):
    ok = err = 0
    ct_ok = ct_err = 0
    for i, cid in enumerate(ids, 1):
        # Primero borrar contactos que solo pertenecen a esta empresa
        contact_ids = get_contacts_of_company(cid)
        for ctid in contact_ids:
            if get_contact_company_count(ctid) == 1:
                r = requests.delete(f"{BASE_URL}/crm/v3/objects/contacts/{ctid}", headers=hdrs())
                if r.status_code == 204:
                    ct_ok += 1
                else:
                    ct_err += 1
                time.sleep(DELAY)

        # Luego borrar la empresa
        r = requests.delete(f"{BASE_URL}/crm/v3/objects/companies/{cid}", headers=hdrs())
        if r.status_code == 204:
            ok += 1
        else:
            err += 1
            print(f"  ⚠️  No se pudo eliminar empresa {cid}: {r.status_code}")
        time.sleep(DELAY)

        pct = i / len(ids) * 100
        print(f"  Progreso: {pct:.0f}% | empresas: {ok} ok / {err} err | contactos: {ct_ok} ok / {ct_err} err", end="\r")

    print(f"\n  ✅ Empresas eliminadas: {ok} | Errores: {err}")
    print(f"  ✅ Contactos eliminados: {ct_ok} | Errores: {ct_err}")
    return ok, err


def batch_update_companies(ids, new_stage):
    updated = errors = 0
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        # Reset primero
        requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/update",
            headers=hdrs(),
            json={"inputs": [{"id": str(c), "properties": {"lifecyclestage": ""}} for c in batch]},
        )
        time.sleep(DELAY)
        r = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/update",
            headers=hdrs(),
            json={"inputs": [{"id": str(c), "properties": {"lifecyclestage": new_stage}} for c in batch]},
        )
        if r.status_code in (200, 201):
            updated += len(batch)
        else:
            errors += len(batch)
            print(f"\n  ⚠️  Error ({r.status_code}): {r.text[:200]}")
        pct = (i + len(batch)) / len(ids) * 100
        print(f"  Progreso: {pct:.0f}% | ok: {updated} | err: {errors}", end="\r")
        time.sleep(DELAY)
    print(f"\n  ✅ Actualizadas: {updated} | Errores: {errors}")
    return updated, errors


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 62)
    print("  LIMPIAR EVANGELISTAS")
    print(f"  Modo: {'👁️  DRY RUN (sin cambios)' if DRY_RUN else '✏️  APLICANDO CAMBIOS'}")
    print("=" * 62)

    if not HUBSPOT_TOKEN:
        print("❌ Configura HUBSPOT_TOKEN antes de ejecutar.")
        return

    # ── 1. Obtener empresas ───────────────────────────────────
    companies   = search_evangelist_companies()
    company_ids = [c["id"] for c in companies]
    co_map      = {c["id"]: c for c in companies}

    # ── 2. Contar deals en ambos pipelines ────────────────────
    print("\n  Contando deals en Pluttoneta AE...")
    ae_counts = get_deal_count_per_company(company_ids, PIPELINE_PLUTTONETA)
    print(f"  → {len(ae_counts)} empresas con deals en AE")

    print("  Contando deals en CS Renewal...")
    cs_counts = get_deal_count_per_company(company_ids, PIPELINE_CS_RENEWAL)
    print(f"  → {len(cs_counts)} empresas con deals en CS")

    def total_deals(cid):
        return ae_counts.get(cid, 0) + cs_counts.get(cid, 0)

    def es_icp(country):
        return country.strip().lower() in PAISES_ICP

    # ── 3. Clasificar ─────────────────────────────────────────
    a_eliminar = []   # 1a: fuera ICP + sin deals
    a_lead     = []   # 1b: ICP + sin deals
    a_revisar  = []   # 1c: tienen deals

    for cid in company_ids:
        c     = co_map[cid]
        n     = total_deals(cid)
        icp   = es_icp(c["country"])

        if n > 0:
            a_revisar.append(cid)
        elif icp:
            a_lead.append(cid)
        else:
            a_eliminar.append(cid)

    print(f"\n  1a. Eliminar (fuera ICP, sin deals): {len(a_eliminar)}")
    print(f"  1b. → Lead (ICP, sin deals):         {len(a_lead)}")
    print(f"  1c. Revisar (tienen deals):           {len(a_revisar)}")

    # ── 4. Reporte consola ────────────────────────────────────
    print(f"\n── 1a. A ELIMINAR — {len(a_eliminar)} ──")
    print(f"  {'Empresa':<45}  {'País':<20}  {'Última mod.'}")
    print("  " + "-" * 85)
    for cid in sorted(a_eliminar, key=lambda x: (co_map[x]["country"] or "").lower()):
        c = co_map[cid]
        print(f"  {(c['name'] or 'sin nombre'):<45}  {c['country']:<20}  {c['last_mod']}")

    print(f"\n── 1b. → LEAD — {len(a_lead)} ──")
    print(f"  {'Empresa':<45}  {'País':<20}  {'Última mod.'}")
    print("  " + "-" * 85)
    for cid in sorted(a_lead, key=lambda x: (co_map[x]["country"] or "").lower()):
        c = co_map[cid]
        print(f"  {(c['name'] or 'sin nombre'):<45}  {c['country']:<20}  {c['last_mod']}")

    print(f"\n── 1c. REVISAR MANUALMENTE (tienen deals) — {len(a_revisar)} ──")
    print(f"  {'Empresa':<45}  {'País':<20}  {'Deals':>5}  {'AE':>4}  {'CS':>4}  {'Última mod.'}")
    print("  " + "-" * 100)
    for cid in sorted(a_revisar, key=lambda x: co_map[x]["name"] or ""):
        c = co_map[cid]
        print(
            f"  {(c['name'] or 'sin nombre'):<45}  {c['country']:<20}"
            f"  {total_deals(cid):>5}  {ae_counts.get(cid,0):>4}  {cs_counts.get(cid,0):>4}"
            f"  {c['last_mod']}"
        )

    # ── 5. CSV de respaldo ────────────────────────────────────
    csv_path = "evangelistas_respaldo.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company_id", "nombre", "dominio", "pais", "industria",
            "n_contactos", "n_deals_ae", "n_deals_cs", "ultima_mod", "accion",
        ])
        writer.writeheader()
        for cid in company_ids:
            c = co_map[cid]
            if cid in a_eliminar:
                accion = "ELIMINAR"
            elif cid in a_lead:
                accion = "→ lead"
            else:
                accion = "revisar manualmente"
            writer.writerow({
                "company_id":  cid,
                "nombre":      c["name"],
                "dominio":     c["domain"],
                "pais":        c["country"],
                "industria":   c["industry"],
                "n_contactos": c["n_contacts"],
                "n_deals_ae":  ae_counts.get(cid, 0),
                "n_deals_cs":  cs_counts.get(cid, 0),
                "ultima_mod":  c["last_mod"],
                "accion":      accion,
            })
    print(f"\n📄 CSV de respaldo guardado: {csv_path}")

    if DRY_RUN:
        print(f"\n✅ DRY RUN completado.")
        print(f"   {len(a_eliminar)} empresas se eliminarían.")
        print(f"   {len(a_lead)} empresas pasarían a Lead.")
        print(f"   {len(a_revisar)} empresas requieren revisión manual.")
        print(f"   Cambia DRY_RUN = False para aplicar los cambios.")
        return

    if not a_eliminar and not a_lead:
        print("\n✅ Nada que hacer.")
        return

    confirm = input(
        f"\n⚠️  ¿Confirmas ELIMINAR {len(a_eliminar)} empresas y mover {len(a_lead)} a Lead? (escribe 'SI'): "
    )
    if confirm.strip().upper() != "SI":
        print("❌ Cancelado.")
        return

    log = {"fecha": datetime.now().isoformat(), "eliminadas": [], "lead": []}

    if a_eliminar:
        print(f"\n🗑️  Eliminando {len(a_eliminar)} empresas...")
        delete_companies(a_eliminar)
        log["eliminadas"] = [
            {"id": cid, "nombre": co_map[cid]["name"], "pais": co_map[cid]["country"]}
            for cid in a_eliminar
        ]

    if a_lead:
        print(f"\n✏️  Moviendo {len(a_lead)} empresas → lead...")
        batch_update_companies(a_lead, "lead")
        log["lead"] = [
            {"id": cid, "nombre": co_map[cid]["name"], "pais": co_map[cid]["country"]}
            for cid in a_lead
        ]

    with open("log_evangelistas.json", "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Proceso completado.")
    print(f"   Log guardado: log_evangelistas.json")


if __name__ == "__main__":
    main()
