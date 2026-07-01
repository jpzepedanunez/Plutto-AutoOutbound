# HubSpot Cleaning — Contexto de referencia

## Autenticación
```bash
export HUBSPOT_TOKEN=pat-na1-...
```
Todos los scripts leen `os.getenv("HUBSPOT_TOKEN")`. El token nunca se hardcodea.

---

## Pipelines

| Nombre | ID |
|---|---|
| Pluttoneta AE | `20361325` |
| CS Renewal | `33728953` |

---

## Lifecycle Stages (companies y contacts)

| Label | Value (ID) |
|---|---|
| Lead | `lead` |
| Prospect | `subscriber` |
| Qualified Opportunity | `opportunity` |
| Implementation | `52531545` |
| New Customer | `customer` |
| Live Customer | `52560399` |
| Happy Customer | `49991712` |
| Sad/No use Customer | `50000917` |
| Evangelist | `evangelist` |
| Churned | `50020179` |
| Other | `other` |
| Nurturing | `50005795` |
| Nurturing No Contact | `50020180` |
| No calificado | `1151001700` |

### Stages protegidos (nunca pisar)
```python
LC_NO_TOCAR = {
    "52560399",    # Live Customer
    "52531545",    # Implementación
    "50020179",    # Churned
    "customer",    # New Customer (legacy)
    "opportunity", # Qualified Opportunity
    "1151001700",  # No calificado
}
```

---

## Etapas de Pluttoneta AE (pipeline `20361325`)

| Stage ID | Nombre |
|---|---|
| `144033658` | Appointment Scheduled |
| `172924342` | BDR Precalificación |
| `996295554` | Consulting Discovery AE |
| `1028970392` | DEAL CALIFICADO (SQO) |
| `49686858` | Demostración |
| `49686857` | Economica Propuesta |
| `98993471` | Interest Confirmed |
| `49686859` | Negotiation |
| `74323563` | Pilot |
| `49963372` | Verbal Yes |
| `1163283618` | W - Iterando Contratos |
| `49655666` | Won |
| `1007516181` | One Shoot Cerrado |
| `49655667` | Z - Closed Lost |
| `1044035701` | Deal No Calificado |

---

## Etapas de CS Renewal (pipeline `33728953`)

### Implementación (etapas tempranas)
| Stage ID | Nombre |
|---|---|
| `994511463` | Implementación |
| `1164813898` | Setup |
| `1164813897` | Kickoff |
| `1164813842` | Documentation & Advanced Setup |

### Live Customer (etapas activas)
| Stage ID | Nombre |
|---|---|
| `1164813841` | Go-live & Handoff CS |
| `74579363` | 12-6 month |
| `78817538` | 3-6 month |
| `78817539` | 2-3 month |
| `78817540` | 1 month |
| `85334091` | Pending |
| `78817541` | Closed Won |

### Closed Lost
| Stage ID | Nombre |
|---|---|
| `78817542` | Closed Lost |

---

## Reglas de clasificación (Actualizar_lifecycle_20.py)

Prioridad de mayor a menor:

1. **CHURNED** (`50020179`) — último deal en CS Renewal está en Closed Lost
2. **LIVE CUSTOMER** (`52560399`) — tiene deal en CS Renewal en etapa activa (CS_LIVE)
3. **IMPLEMENTACIÓN** (`52531545`) — tiene deal en CS Renewal en etapa temprana (CS_EARLY), sin Live
4. **PROSPECT** (`opportunity`) — tiene deal activo en Pluttoneta AE, sin ningún deal en CS Renewal

---

## Scripts disponibles

### `Actualizar_lifecycle_20.py`
Clasifica y actualiza lifecycle según deals en Pluttoneta AE y CS Renewal.
Reglas: Churned > Live Customer > Implementación > Prospect.
Genera `reporte_lifecycle.csv` y `log_lifecycle_update.json`.

### `Clean_No_calificado.py`
Busca empresas con deal en etapa "Deal No Calificado" (`1044035701`) en Pluttoneta AE.
Las mueve a lifecycle "No calificado" (`1151001700`). También actualiza sus contactos.
Genera `reporte_no_calificado.csv`.

### `nurturing_noncontact.py`
Mueve a "Nurturing No Contact" (`50020180`) todas las empresas con al menos 1 deal Closed-Lost en Pluttoneta AE.
Excluye empresas protegidas (Live Customer, Implementación, Churned, Opportunity).
Genera `nurturing_noncontact_preview.csv`.

### `prospecto_a_lead.py`
Busca empresas con lifecycle = "Prospecto" (`subscriber`).
Las mueve a "Lead" (`lead`) si no tienen ningún deal O si no tienen ningún contacto.
También actualiza sus contactos. Genera `reporte_prospecto_a_lead.csv`.

### `actualizar_lifecycle_opportunity.py`
Actualiza lifecycle a "Qualified Opportunity" (`opportunity`) para empresas específicas.

### `limpiar_evangelistas.py`
Procesa empresas con lifecycle = `evangelist` en tres grupos:
- Eliminar (sin país ICP, sin deals)
- → Lead (país ICP + sin deals)
- → Mantener (con deals activos)

### `sincronizar_contactos_lifecycle.py`
Para cada contacto:
- Si tiene empresa → le asigna el mismo lifecycle que la empresa (respeta LC_NO_TOCAR)
- Si NO tiene empresa → fetcha sus actividades (calls, emails, meetings, notes) y usa LLM (`claude-haiku`) para decidir en qué etapa debería estar
- Genera `reporte_sincronizar_contactos.csv` y `log_sincronizar_contactos_FECHA.json`
- Config: `MAX_SIN_EMPRESA = 300` (límite de contactos sin empresa a analizar por ejecución)

### `revisar_evangelizadores.py`
Lista todas las empresas con lifecycle = `evangelist` y sus deals asociados (solo lectura / reporte).

### `clean_companies.py`
Limpieza general de empresas (ver archivo para reglas específicas).

### `loss_detail_no_calificado.py`
Extrae `closed_lost_reason` de deals en etapas No Calificado.
Filtra por "low volume" y "price". Excluye empresas con deals en CS Renewal.
Genera `loss_detail_no_calificado.csv`.

---

## Patrón común de actualización (batch_update)

HubSpot **solo permite avanzar** en lifecycle stages. Para mover hacia atrás hay que resetear primero:

```python
# Paso 1: limpiar (permite mover en cualquier dirección)
requests.post(".../batch/update", json={"inputs": [
    {"id": str(c), "properties": {"lifecyclestage": ""}} for c in batch
]})

# Paso 2: asignar el nuevo stage
requests.post(".../batch/update", json={"inputs": [
    {"id": str(c), "properties": {"lifecyclestage": nuevo_stage}} for c in batch
]})
```

Aplica tanto para `companies` como para `contacts`.

---

## Endpoints principales

```
# Buscar objetos
POST /crm/v3/objects/{type}/search

# Leer en batch
POST /crm/v3/objects/{type}/batch/read

# Actualizar en batch
POST /crm/v3/objects/{type}/batch/update

# Asociaciones batch v4
POST /crm/v4/associations/{from}/{to}/batch/read
# Ej: deals/companies, companies/contacts, companies/deals

# Lifecycle stages del portal
GET /crm/v3/properties/companies/lifecyclestage
```

---

## Notas importantes

- Siempre correr con `DRY_RUN = True` primero para revisar qué cambiaría
- Al actualizar empresas, siempre actualizar también sus **contactos asociados** al mismo lifecycle
- El campo `lifecyclestage` en contacts y companies usa los mismos IDs/values
- `BATCH_SIZE = 100`, `DELAY = 0.3s` entre requests para no superar rate limits de HubSpot
