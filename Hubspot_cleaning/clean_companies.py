"""
Script: Limpieza de empresas en HubSpot
========================================
Elimina empresas que cumplan alguno de estos criterios:
  1. Sin nombre (name vacío o null)
  2. Nombre tipo test ("test", "prueba", "demo", "fake", "n/a", etc.)
  3. País africano o Venezuela

Exclusiones (nunca se tocan):
  - Empresas con al menos 1 negocio asociado
  - Empresas de Chile con al menos 1 negocio

Contactos: si un contacto asociado a la empresa candidata
  - está vinculado SOLO a esa empresa, Y
  - tiene 0 actividad (emails, calls, meetings, notas)
  → se elimina junto con la empresa.

Configura DRY_RUN abajo:
  True  → solo lista candidatas y guarda CSV, no borra nada
  False → borra empresas y contactos elegibles

INSTRUCCIONES:
  export HUBSPOT_TOKEN=pat-na1-...
  python3 clean_companies.py
"""

import os
import time
import csv
import requests

# ─────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")
DRY_RUN       = True    # True = solo listar | False = borrar
BATCH_SIZE    = 100
DELAY         = 0.3
OUTPUT_CSV    = "clean_companies_preview.csv"
# ─────────────────────────────────────────────────────────────

BASE_URL = "https://api.hubapi.com"

TEST_KEYWORDS = [
    "test", "prueba", "demo", "fake", "example",
    "n/a", "sin nombre", "xxx", "asdf", "qwerty",
    "empresa de prueba", "company test",
]

PAISES_EXCLUIR = {
    "venezuela",
    "algeria", "angola", "benin", "botswana", "burkina faso", "burundi",
    "cabo verde", "cape verde", "cameroon", "central african republic", "chad",
    "comoros", "congo", "democratic republic of the congo", "djibouti", "egypt",
    "equatorial guinea", "eritrea", "eswatini", "ethiopia", "gabon", "gambia",
    "ghana", "guinea", "guinea-bissau", "ivory coast", "côte d'ivoire", "kenya",
    "lesotho", "liberia", "libya", "madagascar", "malawi", "mali", "mauritania",
    "mauritius", "morocco", "mozambique", "namibia", "niger", "nigeria",
    "rwanda", "são tomé and príncipe", "sao tome and principe", "senegal",
    "seychelles", "sierra leone", "somalia", "south africa", "south sudan",
    "sudan", "tanzania", "togo", "tunisia", "uganda", "zambia", "zimbabwe",
}


def hdrs():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type":  "application/json",
    }


def es_test(name: str) -> bool:
    n = name.strip().lower()
    return any(kw in n for kw in TEST_KEYWORDS)


def es_pais_excluido(country: str) -> bool:
    return country.strip().lower() in PAISES_EXCLUIR


def scroll_all_companies():
    empresas, after, pagina = [], None, 0
    print("  Descargando empresas desde HubSpot...")
    while True:
        pagina += 1
        params = {"limit": 100, "properties": "name,country,num_associated_deals"}
        if after:
            params["after"] = after
        r = requests.get(f"{BASE_URL}/crm/v3/objects/companies", headers=hdrs(), params=params)
        if r.status_code != 200:
            print(f"  ⚠️  Error paginando empresas ({r.status_code}): {r.text[:300]}")
            break
        data = r.json()
        for obj in data.get("results", []):
            props = obj.get("properties", {})
            empresas.append({
                "id":        obj["id"],
                "name":      props.get("name") or "",
                "country":   props.get("country") or "",
                "num_deals": int(props.get("num_associated_deals") or 0),
            })
        print(f"    página {pagina} — {len(empresas):,} empresas descargadas...")
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(DELAY)
    return empresas


def clasificar(empresa: dict) -> str | None:
    name      = empresa["name"].strip()
    country   = empresa["country"].strip()
    num_deals = empresa.get("num_deals", 0)

    if country.lower() in ("chile", "cl"):
        return None
    if num_deals >= 1:
        return None
    if not name:
        return "sin_nombre"
    if country and es_pais_excluido(country):
        return "pais_excluido"
    return None


def get_contacts_of_company(company_id: str) -> list[str]:
    """Retorna lista de contact_ids asociados a la empresa."""
    r = requests.get(
        f"{BASE_URL}/crm/v4/objects/companies/{company_id}/associations/contacts",
        headers=hdrs(),
    )
    if r.status_code != 200:
        return []
    return [str(item["toObjectId"]) for item in r.json().get("results", [])]


def get_contact_company_count(contact_id: str) -> int:
    """Cuántas empresas tiene asociadas el contacto."""
    r = requests.get(
        f"{BASE_URL}/crm/v4/objects/contacts/{contact_id}/associations/companies",
        headers=hdrs(),
    )
    if r.status_code != 200:
        return 99  # asumir que tiene más para no borrarlo
    return len(r.json().get("results", []))


def get_contact_activity(contact_id: str) -> int:
    """
    Cuenta actividades del contacto: emails, calls, meetings, notas.
    Usa engagements v1 (más completo para conteo rápido).
    """
    r = requests.get(
        f"{BASE_URL}/engagements/v1/engagements/associated/contact/{contact_id}/paged",
        headers=hdrs(),
        params={"limit": 1},
    )
    if r.status_code != 200:
        return 99  # asumir que tiene actividad para no borrarlo
    data = r.json()
    return data.get("total", 0)


def evaluar_contactos(company_id: str) -> list[str]:
    """
    Retorna lista de contact_ids elegibles para borrar junto con la empresa:
    - asociado a solo 1 empresa (esta), Y
    - 0 actividad registrada
    """
    elegibles = []
    contact_ids = get_contacts_of_company(company_id)
    for cid in contact_ids:
        num_empresas = get_contact_company_count(cid)
        if num_empresas > 1:
            continue  # está en otra empresa, no tocar
        actividad = get_contact_activity(cid)
        if actividad == 0:
            elegibles.append(cid)
        time.sleep(DELAY)
    return elegibles


def delete_object(object_type: str, object_id: str) -> bool:
    r = requests.delete(
        f"{BASE_URL}/crm/v3/objects/{object_type}/{object_id}",
        headers=hdrs(),
    )
    return r.status_code == 204


def main():
    if not HUBSPOT_TOKEN:
        print("❌ Configura HUBSPOT_TOKEN antes de ejecutar.")
        print("   export HUBSPOT_TOKEN=pat-na1-...")
        return

    print("=" * 60)
    print("  LIMPIEZA DE EMPRESAS — HUBSPOT")
    print(f"  Modo: {'DRY-RUN (solo listar)' if DRY_RUN else '⚠️  BORRADO REAL'}")
    print("=" * 60)

    empresas = scroll_all_companies()
    print(f"  Total empresas descargadas: {len(empresas):,}\n")

    print("  Clasificando empresas...")
    candidatas = []
    tests = []
    for e in empresas:
        if e["num_deals"] < 1 and not e["country"].lower() in ("chile", "cl") and es_test(e["name"]):
            tests.append(e)
        razon = clasificar(e)
        if razon:
            candidatas.append({**e, "razon": razon})
    print(f"  Candidatas a borrar: {len(candidatas):,}  |  Tests para revisar: {len(tests):,}\n")

    # Evaluar contactos elegibles para cada empresa candidata
    print(f"  Revisando contactos de {len(candidatas):,} empresas candidatas...")
    for i, c in enumerate(candidatas, 1):
        print(f"  [{i}/{len(candidatas)}] {c['name'] or '(sin nombre)'} ({c['razon']})...")
        c["contactos_a_borrar"] = evaluar_contactos(c["id"])

    # Resumen
    conteo = {}
    for c in candidatas:
        conteo[c["razon"]] = conteo.get(c["razon"], 0) + 1
    total_contactos = sum(len(c["contactos_a_borrar"]) for c in candidatas)

    print(f"\n── Candidatas a eliminar: {len(candidatas):,} empresas ── {total_contactos:,} contactos ──")
    for razon, n in sorted(conteo.items()):
        print(f"   {razon:<20} {n:>5}")
    print()

    print(f"{'ID':<14}  {'Nombre':<40}  {'País':<20}  {'Razón':<14}  Contactos a borrar")
    print("-" * 110)
    for c in sorted(candidatas, key=lambda x: x["razon"]):
        ctcts = ", ".join(c["contactos_a_borrar"]) if c["contactos_a_borrar"] else "—"
        print(f"  {c['id']:<12}  {c['name']:<38}  {c['country']:<18}  {c['razon']:<14}  {ctcts}")

    # CSV preview
    filas_csv = []
    for c in candidatas:
        filas_csv.append({
            "company_id":   c["id"],
            "name":         c["name"],
            "country":      c["country"],
            "razon":        c["razon"],
            "contactos_borrar": "|".join(c["contactos_a_borrar"]),
        })
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["company_id", "name", "country", "razon", "contactos_borrar"])
        writer.writeheader()
        writer.writerows(filas_csv)
    print(f"\n📄 Preview guardado: {OUTPUT_CSV}")

    # Empresas tipo test — solo para revisión, no se borran
    if tests:
        print(f"\n── Empresas tipo TEST para revisión manual: {len(tests):,} ──")
        print(f"{'ID':<14}  {'Nombre':<45}  País")
        print("-" * 75)
        for t in sorted(tests, key=lambda x: x["name"].lower()):
            print(f"  {t['id']:<12}  {t['name']:<43}  {t['country']}")

    if DRY_RUN:
        print(f"\n⚠️  DRY_RUN = True — no se borró nada.")
        print(f"   Cambia DRY_RUN = False para ejecutar el borrado.")
        return

    # Borrado real
    emp_ok, emp_fail, ct_ok, ct_fail = 0, 0, 0, 0
    print(f"\n🗑️  Eliminando empresas y contactos...")

    for c in candidatas:
        # Primero contactos elegibles
        for cid in c["contactos_a_borrar"]:
            if delete_object("contacts", cid):
                ct_ok += 1
            else:
                ct_fail += 1
                print(f"  ⚠️  No se pudo eliminar contacto {cid} de empresa {c['name']}")
            time.sleep(DELAY)

        # Luego la empresa
        if delete_object("companies", c["id"]):
            emp_ok += 1
        else:
            emp_fail += 1
            print(f"  ⚠️  No se pudo eliminar empresa: {c['id']} — {c['name']}")
        time.sleep(DELAY)

    print(f"\n✅ Empresas eliminadas:  {emp_ok:,}  |  ⚠️  Errores: {emp_fail:,}")
    print(f"✅ Contactos eliminados: {ct_ok:,}  |  ⚠️  Errores: {ct_fail:,}")


if __name__ == "__main__":
    main()
