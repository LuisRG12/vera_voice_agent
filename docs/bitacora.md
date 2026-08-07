# Bitácora

> Qué se encontró, qué se decidió y con qué número. El plan de partida está en
> [`arquitectura.md`](arquitectura.md).
>
> Formato: **hallazgo → evidencia → decisión**. La evidencia es lo que importa; una decisión
> sin medición es una opinión.
>
> **Cómo leer esta historia.** El repositorio se construye por etapas, cada una con su
> código, sus pruebas y su entrada aquí. El orden de los commits es un orden de
> construcción —de lo que no depende de nada a lo que depende de todo—, no una
> reconstrucción cronológica del descubrimiento. Las mediciones que se citan son reales y
> reproducibles con los arneses del repositorio; cuando una de ellas obligó a cambiar una
> decisión anterior, queda dicho en la etapa donde se midió.

---

## Etapa 1 · Andamiaje

**Punto de partida.** Un proceso, una dependencia de sistema (Ollama), sin base de datos
externa y sin claves. La decisión de arrancar así no es minimalismo: la solución tiene que
quedar corriendo en 15 minutos siguiendo solo el README, y cada servicio que se añade es un
punto de fallo en la máquina de otro.

**`/api/health` desde el primer día.** No para monitorización, sino porque durante todo el
desarrollo la pregunta «¿está bien lo que tengo montado?» se responde por HTTP y no
levantando la interfaz. Reporta el modelo configurado, si el runtime local responde y
cuántos documentos hay cargados.

**Decisiones abiertas** al cerrar la etapa: las seis de `arquitectura.md`. Ninguna se puede
resolver sin medir, y medir requiere que exista algo que ejecutar.

---

## Etapa 2 · Conocimiento

**El conocimiento vivo no es una característica, es la estructura.** El requisito —subir un
documento y que se use, borrarlo y que se olvide— se puede resolver de dos formas: con un
proceso de reindexado que hay que disparar y vigilar, o haciendo que el borrado sea un
*tombstone* y que la recuperación relea los activos en cada consulta. Se eligió lo segundo.

El costo es releer el índice por consulta; a la escala de este corpus son milisegundos. Lo
que se compra es que **olvidar surta efecto en el turno siguiente, incluso a mitad de
llamada**, y que no exista un estado intermedio en el que el sistema dice haber olvidado
algo que todavía puede citar.

**Versionado en lugar de sobrescritura.** Subir un documento con un nombre existente crea
una versión nueva y tombstonea la anterior. Sin esto, la trazabilidad de una llamada pasada
apuntaría a un texto que ya no es el que se citó entonces.

**Recuperación híbrida, fusionada por rango.** Denso (embeddings) y disperso (BM25) se
fusionan con RRF sobre los rangos y no sobre los puntajes, porque las dos señales viven en
escalas distintas y no comparables. La razón clínica de conservar BM25: los signos de
alarma son **palabras exactas** —«pus», «fiebre», «39»— que un modelo denso pequeño diluye
y que BM25 clava.

**La evidencia también es híbrida.** `has_evidence` se cumple por semejanza semántica **o**
por solapamiento de términos clínicos. Con un solo criterio denso, «sale pus con mal olor»
se quedaba por debajo del umbral pese a ser textualmente lo que dice el protocolo.

**El troceado respeta las palabras.** Cortar el solape como «los últimos N caracteres» hace
que los fragmentos empiecen a media palabra, y el embedding se calcula sobre ese texto. La
cola de solape arranca en frase, o en su defecto en la siguiente palabra completa.

**`/api/knowledge/query` existe para auditar.** Si una respuesta sale mal, lo primero que
hay que poder responder es si falló lo que se recuperó o lo que se generó con ello. Sin ese
endpoint, las dos causas se investigan a ciegas.

**Verificado:** `evals/conocimiento_vivo.py`, 10/10, sin invocar ningún modelo de lenguaje.
Y el ciclo completo por HTTP: subir → citar → borrar → dejar de citar.

**Sigue abierto:** D2 (qué modelo de embeddings) y D3 (dónde va el umbral). El valor actual
de `min_evidence` es provisional y está declarado como tal: se calibra en la etapa 11,
contra el corpus real y con preguntas de respuesta conocida.

---

## Etapa 3 · Corpus del reto y pertinencia por procedimiento

### El hallazgo que cambió el diseño

La carpeta `breast_cancer/` del corpus **no contiene un solo documento de mama**. Sus 19
archivos son de cáncer de cuello uterino: se verificó documento por documento contando
términos, y ninguno menciona mama, mastectomía, axila ni linfedema.

Mientras tanto, 8 de los 40 pacientes del dataset —**el 20 % de los casos**— están operados
de mastectomía.

Sin filtro, la pregunta *«me duele la herida del seno»* recupera material de otra cirugía:
misma familia clínica, mismo vocabulario postoperatorio, y el coseno no distingue. Es una
trampa de alucinación por construcción.

**Dos decisiones separadas:**

1. **Los documentos se etiquetan por lo que tratan, no por el nombre de su carpeta.**
   Marcarlos como `mastectomia` habría sido construir la trampa nosotros mismos.
2. **Compuerta de pertinencia**: un paciente ve los documentos de su cirugía y los
   generales (`procedure IS NULL`, que son los que sube el evaluador y por eso nunca se
   filtran). Nada más.

Para los pacientes de mastectomía, el agente declarará que no tiene material de su
procedimiento y escalará. No es una carencia: es la conducta correcta, y hay que contarla
como decisión consciente para que no se lea como un defecto.

**La compuerta no sirve solo para ese caso.** Los protocolos postoperatorios comparten
vocabulario casi por completo —«Signos de alarma» se repite entre cirugías— y las guías de
paciente más largas y coloquiales copan el top-k de cualquier pregunta, venga del
procedimiento que venga. El filtro corrige la recuperación de **todos** los procedimientos.

### Dos PDFs que se estaban perdiendo en silencio

El material del reto declara 107 documentos; al clonarlo en Windows aparecían 105. No era
un error de su documentación: **dos archivos de `colorectal cancer/` tienen títulos de
artículo como nombre y superan el límite clásico de ruta de Windows.**

El síntoma engaña: `git` los deja en disco y `glob` los encuentra, pero `open()` responde
`FileNotFoundError`. Se perdía material clínico real sin que nada avisara —**38.987 y
46.345 caracteres**—, y colorectal quedaba con 23 documentos en vez de 25.

Se resolvió en el lector con el prefijo de ruta extendida, no en el script: la consola
recibe documentos del evaluador que no hemos visto, y un nombre largo es perfectamente
normal.

### Lo que el corpus real trae, y que la ingesta reporta a la vista

```
107 PDFs revisados · 105 ingeridos
  Apendicectomía 23 · Colecistectomía 17 · Colectomía 25 · Reemplazo articular 21
  Mastectomía     0  <-- SIN CORPUS PROPIO
  (cancer_cuello_uterino) 19  <-- venían en breast_cancer/
  1 sin capa de texto (requiere OCR) · 1 casi-duplicado (Jaccard 0.96)
```

- **Un PDF escaneado.** El lector no falla con un escaneo: devuelve cadena vacía. Indexarlo
  metería un documento fantasma que nunca se puede citar y que infla el conteo de la
  consola. Se reporta como no procesable.
- **Un PDF cifrado.** Requiere `cryptography`, que se añadió: importa más allá de ese
  archivo, porque un PDF protegido es un formato normal y el evaluador puede subir uno.
- **Un casi-duplicado.** No hay duplicados exactos por hash, pero sí el mismo artículo con
  dos nombres. Con huella exacta no se ve; con solapamiento de n-gramas sí (0.96, frente a
  0.2 del siguiente par más parecido). Importa porque compiten entre ellos en el top-k.

### Qué se entrega y qué no

**Los PDFs no se redistribuyen en este repositorio.** Son obra de sus autores y el material
del reto los incluye solo como referencia. Se entrega el índice ya construido —que es lo
que hace falta para levantar la solución— y el script que lo reproduce desde el material
oficial.

**El índice todavía no se versiona.** Depende del modelo de embeddings, que es la decisión
D2 y sigue abierta: comprometerlo ahora dejaría un binario grande en la historia que habría
que reemplazar por otro en la etapa 11. Se versiona una sola vez, cuando sus entradas estén
decididas.

**Verificado:** `evals/pertinencia_procedimiento.py`, 13/13.

---

## Etapa 4 · El modelo local

### D1 — resuelta: `llama3.2:3b`

La lista de modelos permitidos del reto tiene cuatro entradas y **dos están retiradas por
sus proveedores**: el modelo de nube con ventana grande ya no existe en su API, y el de
baja latencia fue retirado por su plataforma, cuyo reemplazo no está en la lista. Quedan
los dos locales, y entre esos se decide midiendo.

**Se mide en CPU pura**, no en la máquina de desarrollo. El despliegue se cronometra en el
equipo de quien evalúa, y el reto vende explícitamente viabilidad en hardware común:
publicar números de GPU sería reportar métricas que no se sostienen en la sesión.

`uv run python -m evals.spike_modelo`

| modelo | juez de riesgo | falsos negativos | voz limpia | TTFA | fallos |
|---|---:|---:|---:|---:|---|
| **llama3.2:3b** | **9/10** | **1** | **6/6** | 2.822 ms | — |
| phi3.5:3.8b | 7/10 | 3 | 3/6 | 2.847 ms | tuteo×2, largo |

Gana en lo que más pesa: **menos falsos negativos del juez de riesgo** —no escalar cuando
había que escalar es la falla clínica más grave— y la única respuesta limpia en los seis
casos de habla colombiana. La latencia es prácticamente igual, así que no compensa nada.

### El esquema como gramática, no como sugerencia

Con un runtime local el JSON Schema se traduce a una gramática y el modelo **no puede
emitir tokens fuera de ella**. Con salida estructurada por API el esquema era una
instrucción que el modelo podía desobedecer; aquí es el espacio de lo emitible.

Eso convierte al esquema en el sitio correcto para imponer invariantes, y se aprovecha en
dos lugares:

- **`grounded_response_for()`** construye el esquema de cada turno con los fragmentos
  realmente recuperados: `list[Literal[1, 2]]` cuando hay dos, y `maxItems: 0` cuando el
  retrieval no devolvió nada. **Citar un documento que no se le ofreció al modelo pasó de
  estar prohibido a ser imposible de generar.**
- **`RiskAssessment` va acotado por longitud.** Sin ese límite el modelo copia el fragmento
  entero en `evidence` y agota su presupuesto de tokens a mitad del JSON: salida truncada y
  turno degradado, justo en el momento más delicado de la llamada. Y `risk` va primero en
  el esquema, porque la gramática obliga a respetar ese orden y así llega aunque la
  generación se corte.

### Dónde corre

**No se fuerza CPU.** El runtime usa GPU si la hay y CPU si no, que es lo que permite que
esto levante en cualquier equipo. `LLM_NUM_GPU=0` existe para **medir** el escenario sin
tarjeta —que es el que hay que reportar—, no para degradar a quien la tenga.

### Detalles que cuestan un turno si se ignoran

- **`num_ctx` explícito.** El valor por defecto del runtime es pequeño y el prompt de un
  turno lo desborda. Al desbordarse trunca **por el principio**, que es donde va el prompt
  de sistema con las reglas de seguridad, y no avisa.
- **`keep_alive`.** Sin él el modelo se descarga tras unos minutos ociosos y el primer
  turno después de una pausa paga la recarga entera. En voz eso es un silencio inaceptable.
- **`/api/health` reporta estado real**, no configuración: si el runtime está arriba y si el
  modelo está descargado. Lo que puede fallar no es una credencial.

**Todavía no está bien, y es esperable.** El modelo tutea y `citation_ids` sale vacío
aunque use el contexto. Las reglas de conversación son la etapa 6 y la derivación de citas
la etapa 7; esta etapa entrega el cliente y las garantías de formato.
