# Modelo de Scoring ICP — Plutto
**Versión recomendada: Score v2**

---

## ¿Para qué sirve?

Automatiza la priorización de prospectos según su fit con el ICP de Plutto. Recibe datos de una empresa y devuelve un **score de 0 a 100**, donde 100 es fit perfecto.

---

## Evolución del modelo: v0 → v1 → v2

Cada versión reduce la dependencia en la IA y aumenta la precisión y consistencia:

| | Score v0 | Score v1 | **Score v2** |
|---|---|---|---|
| **¿Cómo funciona?** | La IA asigna el score total directamente | La IA asigna puntos por criterio; Python normaliza por segmento | Python calcula lo objetivo; la IA solo estima lo subjetivo |
| **Problema principal** | Score arbitrario, no explicable | Puntos máximos varían por segmento, difícil de comparar | — |
| **Ventaja** | Rápido | Más estructurado | **Consistente, trazable y auditabe** |
| **Escala común** | 0–100 | 0–100 | **0–100 fijo** |

> La v2 es más predecible: el mismo tipo de empresa siempre puntúa similar, sin importar cómo responda la IA ese día.

---

## Cómo funciona el Score v2

El score se construye sumando puntos en **7 dimensiones**. El máximo posible es **100 puntos**.

### Dimensiones calculadas en Python (objetivas)

Basadas directamente en datos del SII. **No dependen de la IA.**

| Dimensión | Datos usados | Puntaje |
|---|---|---|
| **Tamaño empresa** | Tramo SII | Gran empresa=20, Mediana=12, Pequeña=5, Micro=2 |
| **Estructura holding** | N° de empresas hijas | 3+ hijas=15, 2 hijas=8, 1 hija=3, sin hijas=0 |
| **Trabajadores** | N° de trabajadores | >1.000=10, 500–1.000=8, 200–500=5, 50–200=3, <50=0 |

### Dimensiones estimadas por la IA (subjetivas)

La IA interpreta el giro y contexto para estimar estos puntos.

| Dimensión | Criterio | Puntaje |
|---|---|---|
| **Regulación** | ¿Está regulada por CMF, Sernageomin, SEC o CNE? | Sí=15, No=0 |
| **Volumen de proveedores** | Estimación según giro y tamaño | >200=25, 50–200=15, <50=0 |
| **Segmento/Vertical** | Clasificación de industria | Mining/Energía/Utilities/Manufactura=5, Financiero=3, Otro=0 |
| **Señal externa** *(opcional)* | Evento que genera urgencia de compra | Fuerte (multa, licitación, M&A, nuevo directorio)=10, Débil=5, Sin señal=0 |

### Fórmula

```
Score = (suma de los 7 componentes) / 100 × 100
```

---

## Ejemplo real: BCI vs. Metro S.A.

Dos empresas de segmentos distintos, para mostrar el rango del modelo:

### Banco de Crédito e Inversiones (BCI)

| Dimensión | Puntos |
|---|---|
| Tamaño (gran empresa, 8.924 trabajadores) | +20 |
| Holding (22 empresas hijas) | +15 |
| Trabajadores | +10 |
| Regulación (CMF) | +15 |
| Proveedores (>200) | +25 |
| Segmento (Financiero) | +3 |
| Señal externa | +0 |
| **Total** | **88 / 100** |

> Pain point: *Gestión de riesgo en la debida diligencia de miles de proveedores, clientes y contrapartes financieras para cumplir con regulaciones de la CMF.*

---

### Metro S.A.

| Dimensión | Puntos |
|---|---|
| Tamaño (gran empresa, 4.987 trabajadores) | +20 |
| Holding (3 empresas hijas) | +15 |
| Trabajadores | +10 |
| Regulación (CNE / Ministerio de Transportes) | +15 |
| Proveedores (>200) | +25 |
| Segmento (Utilities/Infraestructura) | +5 |
| Señal externa | +0 |
| **Total** | **90 / 100** |

> Pain point: *Gestión y evaluación de riesgos en cadena de suministro compleja con cientos de proveedores de servicios críticos, mantenimiento y operaciones para transporte masivo.*

---

### Comparación de versiones para Metro S.A.

| Versión | Score | Vertical detectado | Observación |
|---|---|---|---|
| v0 | **42** | manufactura | La IA no identificó que es infraestructura regulada |
| v1 | **89** | otro | Puntos correctos, pero clasificó el segmento como "otro" |
| **v2** | **90** | Utilities/Infraestructura | Python ancló tamaño y holding; IA clasificó correctamente |

> El salto de v0 a v1/v2 en Metro muestra el principal problema de la v0: sin estructura, la IA subestimó sistemáticamente empresas de infraestructura que no tienen regulación "obvia" como la financiera.

---

## Datos que necesita el modelo

| Campo | Fuente |
|---|---|
| Razón social y RUT | SII / HubSpot |
| Tramo de ventas SII | SII |
| Giro económico | SII |
| N° de empresas hijas | SII (estructura societaria) |
| N° de trabajadores | SII |
| Región | SII |
| Señal externa *(opcional)* | Manual — ej: "Multa SEC Q1 2026" |

---

## Distribución de scores en la base actual

Los resultados del CSV de comparación muestran una distribución coherente con el ICP:

| Rango | Perfil típico |
|---|---|
| **80–100** | Bancos, utilities reguladas, mineras grandes |
| **60–79** | Empresas financieras medianas, manufactura industrial grande |
| **40–59** | Manufactura sin regulación, empresas medianas sin holding |
| **0–39** | Microempresas, empresas sin giro definido, sectores fuera de ICP |

---

## Cómo se usa

1. Se exporta la base de empresas desde el SII
2. El script corre el scoring sobre cada empresa
3. El output es un CSV con score v0, v1, v2 + desglose de puntos + pain point
4. Se ordena de mayor a menor score v2 para priorizar outreach comercial

---

## Limitaciones

- Las señales externas son manuales. Sin señal, el score máximo es **90/100**
- El giro SII no siempre refleja la operación real. Casos ambiguos se clasifican como "Otro"
- Los datos SII tienen lag de un año tributario
- El modelo prioriza, no reemplaza al ejecutivo comercial
