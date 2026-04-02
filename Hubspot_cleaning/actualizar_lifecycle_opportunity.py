"""
Script: Actualizar Lifecycle Stage → Qualified Opportunity
===========================================================
Actualiza el lifecyclestage a "opportunity" (Qualified Opportunity) en HubSpot
para todas las empresas asociadas a deals activos en el pipeline Pluttoneta AE.

EMPRESAS IDENTIFICADAS: 242 empresas únicas
PIPELINE ORIGEN:        Pluttoneta AE (20361325)
ETAPAS CONSIDERADAS:    Appointment Scheduled, Consulting Discovery AE,
                        DEAL CALIFICADO (SQO), Demostración, Economica Propuesta,
                        Interest Confirmed, Negotiation, Pilot, Verbal Yes,
                        W - Iterando Contratos, Won

INSTRUCCIONES:
1. Pega tu token en HUBSPOT_TOKEN
2. Corre con DRY_RUN = True primero para ver qué empresas se actualizarán
3. Cambia DRY_RUN = False y vuelve a correr para aplicar los cambios

SCOPES NECESARIOS:
  crm.objects.companies.write
"""

import os
import requests
import time
import json
from datetime import datetime

# ============================================================
#  CONFIGURACIÓN
# ============================================================
HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN", "")   # export HUBSPOT_TOKEN=pat-na1-...
DRY_RUN       = True               # False = aplica cambios reales
BATCH_SIZE    = 100
DELAY_SECONDS = 0.5
# ============================================================

# Empresas a actualizar (242 IDs únicos)
COMPANY_IDS = [
    9827092481, 17251146754, 52258637827, 9166296441, 19252285450, 17823288334,
    9213882383, 8940016656, 14755911185, 20201773587, 18992212500, 9826685973,
    29505955862, 27757790231, 19634849303, 53194464793, 9205672468, 8941208600,
    9176892958, 10231162917, 9827122214, 30147887657, 37018274859, 9827247149,
    22861743662, 9034661933, 22108296752, 9827247155, 52252305972, 18740637235,
    9854196281, 21492885563, 21489760828, 20201687106, 15970627140, 21704838214,
    17654678602, 16999114314, 20201547853, 17955944528, 18334819920, 19527567959,
    9826928730, 18997570139, 22582261852, 9826788956, 9144533084, 33122482272,
    17081566305, 38974890085, 36663499878, 38991908967, 39027736680, 36780047976,
    9827127914, 18467882599, 9826928745, 20461652070, 18182927975, 20201665647,
    9826788978, 9827127923, 18467661428, 9905793653, 30227355764, 19253432949,
    9826788984, 19166675063, 17251109494, 20160305780, 9827174010, 52870533245,
    8940018294, 18194997375, 23409439871, 19073702015, 18726814850, 9827245186,
    22511422084, 10104801925, 13527795844, 19252314247, 20446159495, 8940016777,
    9827101834, 29267676810, 22162802316, 9492199049, 8941378194, 10246918804,
    46581994646, 8940505716, 27592446618, 14758985882, 17955787933, 35533711518,
    10272700574, 9910648992, 35194664099, 18172525219, 10092105893, 28815005350,
    18997543075, 9729486504, 20200368809, 9826921741, 9018668198, 19252082860,
    27072438956, 19074228398, 22458187951, 17823308464, 8939964072, 9826815666,
    35131286195, 52232851124, 9827072692, 9232492724, 20174488246, 34069178549,
    9826738872, 9513651841, 18844363008, 20135924994, 19633472260, 17700155140,
    30625122566, 18771326726, 9826921735, 23222290698, 29001842957, 9238277902,
    10394238223, 9827216656, 19733370640, 9827072786, 18841235219, 46326718739,
    25151930645, 15645584148, 19253201687, 9826921749, 8952843030, 9980527898,
    35584239900, 23819784988, 18472643357, 18992224028, 8952840480, 28916790566,
    32450788647, 21965930792, 18726796582, 14928999207, 20683700011, 18394517803,
    22479475502, 50593130803, 9827265844, 18992659765, 19527357238, 19253551927,
    9827265847, 9166240053, 9827258170, 22637913914, 8939964222, 13800821058,
    25676252485, 9827265861, 9826756426, 21311636300, 22615590733, 21359875922,
    15993286482, 20201519956, 27209840980, 17655462742, 9826756439, 43761642840,
    15329707355, 20468224859, 22161447774, 20159813470, 10211832670, 15450622305,
    22128229731, 15001223013, 17545015654, 19416937322, 16195439466, 21544875370,
    13524502381, 9826957676, 18992636273, 19074252657, 20135962996, 18997628277,
    18841283959, 9827197304, 18394746233, 50927461754, 9827113339, 9826630522,
    9827260797, 28212132734, 17700012415, 15718995324, 9316793215, 21544844674,
    8952843650, 20461463942, 19467882887, 15120667019, 9826919308, 9166167952,
    9826630545, 9029094801, 9178793362, 9826630548, 19728462744, 21853916057,
    9166258075, 31901856157, 17511252894, 22458339233, 8939963297, 20201620899,
    14925534630, 32796618151, 25447849897, 18992799147, 9165660075, 18992756142,
    10627125166, 25247311280, 20135909296, 14928520622, 21579645366, 33123627448,
    15261114808, 9017143736
]

BASE_URL = "https://api.hubapi.com"

def headers():
    return {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type": "application/json"
    }

def batch_read(ids):
    """Lee nombre y lifecyclestage actual de un batch de empresas."""
    results = {}
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        payload = {
            "inputs": [{"id": str(cid)} for cid in batch],
            "properties": ["name", "lifecyclestage"],
        }
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/read",
            headers=headers(),
            json=payload,
        )
        if resp.status_code in (200, 201):
            for obj in resp.json().get("results", []):
                results[int(obj["id"])] = {
                    "name":           obj["properties"].get("name", "Sin nombre"),
                    "lifecyclestage": obj["properties"].get("lifecyclestage") or "sin valor",
                }
        else:
            print(f"  ⚠️  Error batch/read {i}–{i+len(batch)}: {resp.status_code} — {resp.text[:300]}")
        time.sleep(DELAY_SECONDS)
    return results


def batch_update(ids_to_update):
    """Actualiza el lifecyclestage a 'opportunity' en batches."""
    updated = 0
    errors  = 0
    total   = len(ids_to_update)

    for i in range(0, total, BATCH_SIZE):
        batch = ids_to_update[i : i + BATCH_SIZE]
        payload = {
            "inputs": [
                {"id": str(cid), "properties": {"lifecyclestage": "opportunity"}}
                for cid in batch
            ]
        }
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/companies/batch/update",
            headers=headers(),
            json=payload,
        )
        if resp.status_code in (200, 201):
            updated += len(batch)
        else:
            errors += len(batch)
            print(f"\n  ⚠️  Error batch {i}–{i+len(batch)}: {resp.status_code} — {resp.text[:200]}")

        pct = (i + len(batch)) / total * 100
        print(f"  Progreso: {pct:.1f}% | Actualizadas: {updated:,} | Errores: {errors}", end="\r")
        time.sleep(DELAY_SECONDS)

    print(f"\n  ✅ Actualizadas: {updated:,} | Errores: {errors}")
    return updated, errors


def main():
    print("=" * 60)
    print("  ACTUALIZAR LIFECYCLE → QUALIFIED OPPORTUNITY")
    print(f"  Modo: {'👁️  DRY RUN (no cambia nada)' if DRY_RUN else '✏️  APLICANDO CAMBIOS'}")
    print(f"  Empresas: {len(COMPANY_IDS):,}")
    print("=" * 60)

    if HUBSPOT_TOKEN == "TU_TOKEN_AQUI":
        print("❌ Configura HUBSPOT_TOKEN antes de ejecutar.")
        return

    print(f"\n🔍 Leyendo estado actual de {len(COMPANY_IDS):,} empresas...")
    current = batch_read(COMPANY_IDS)

    needs_update = []
    already_ok   = []
    for cid in COMPANY_IDS:
        info = current.get(cid, {"name": f"ID {cid}", "lifecyclestage": "desconocido"})
        if info["lifecyclestage"] == "opportunity":
            already_ok.append(info)
        else:
            needs_update.append((cid, info))

    print(f"\n── Sin cambio ({len(already_ok)}) ──────────────────────────────")
    for info in already_ok:
        print(f"  {info['name']:<45}  ya es → opportunity")

    print(f"\n── Requieren actualización ({len(needs_update)}) ────────────────")
    for _, info in needs_update:
        print(f"  {info['name']:<45}  {info['lifecyclestage']} → opportunity")

    if DRY_RUN:
        print(f"\n✅ DRY RUN completado. Cambia DRY_RUN = False para aplicar los cambios.")
        return

    if not needs_update:
        print("\n✅ Todas las empresas ya tienen el lifecycle correcto. Nada que actualizar.")
        return

    confirm = input(f"\n⚠️  ¿Confirmas actualizar {len(needs_update):,} empresas a 'Qualified Opportunity'? (escribe 'SI'): ")
    if confirm.strip().upper() != "SI":
        print("❌ Cancelado.")
        return

    print(f"\n✏️  Actualizando {len(needs_update):,} empresas...")
    updated, errors = batch_update([cid for cid, _ in needs_update])

    log = {
        "fecha":              datetime.now().isoformat(),
        "total_actualizadas": updated,
        "total_errores":      errors,
        "sin_cambio":         len(already_ok),
        "actualizadas":       [
            {"id": cid, "nombre": info["name"], "estado_anterior": info["lifecyclestage"]}
            for cid, info in needs_update
        ],
    }
    with open("log_lifecycle_opportunity.json", "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Proceso completado.")
    print(f"   Log guardado: log_lifecycle_opportunity.json")


if __name__ == "__main__":
    main()
