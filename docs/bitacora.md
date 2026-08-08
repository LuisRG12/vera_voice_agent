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

---

## Etapa 5 · Seguridad clínica

### La decisión no puede depender del modelo

Dos capas independientes, y la decisión es **el máximo de las dos**:

- **Determinista** (`safety_rules.py` + `lexicon.py`): léxico clínico colombiano con
  manejo de negación. No invoca al modelo.
- **Juez** (`seguridad.assess_risk`): el modelo clasifica el riesgo con los fragmentos del
  protocolo recuperados.

La consecuencia práctica es la que importa: **si el modelo se cae a mitad de turno, la
capa determinista ya evaluó y la alerta sale igual**. Escalar no queda condicionado a que
haya un runtime disponible. Está probado explícitamente en `evals/decision_seguridad.py`:
sin juez, las reglas deciden solas y la acción sigue siendo escalar.

Y queda constancia de **qué capa lo decidió** (`rules`, `llm`, `both`), porque una decisión
clínica que no se puede explicar no sirve para auditarla después.

### El léxico es datos, no código

`lexicon.py` son 23 conceptos clínicos con las frases que un paciente colombiano usa de
verdad. Está separado del motor por una razón concreta: **cada llamada real descubre dos o
tres formas nuevas de decir lo mismo**, y ampliar la cobertura no debería exigir saber
escribir expresiones regulares. Los términos son frases planas; el motor las compila.

Un clínico puede añadir «me siento aporreado» sin tocar una línea de lógica.

**Y no es conocimiento clínico**, es comprensión del habla. Qué umbral aplica y qué es un
signo de alarma vive en los documentos; aquí solo está el puente entre «botando materia» y
el concepto `infeccion` que el documento sí nombra. Por eso el léxico crece sin tocar el
conocimiento y el conocimiento cambia sin tocar el léxico.

### El alcance de la negación, que es donde estaba el peligro

Una ventana de N caracteres antes del síntoma **no basta**, y los contraejemplos son
clínicamente graves:

| Frase | Qué pasaba |
|---|---|
| *«no aguanto el dolor en el pecho»* | el «no» niega el aguante, no el dolor → se perdía un `critical` |
| *«no me baja la fiebre»* | niega la mejoría; la fiebre **persiste** |
| *«ayer no tenía fiebre, pero hoy tengo fiebre de 40»* | la 1ª mención negada descartaba la regla entera |

Se resolvió exigiendo que entre la negación y el síntoma **solo haya conectores**, y
evaluando **todas** las ocurrencias y no la primera. Ante la duda se considera NO negado.

### Tolerancia a cómo se transcribe el habla

Todo lo que decide el agente sale del transcript, no del audio: un signo de alarma no puede
perderse porque el reconocedor se comió una consonante. La compilación de términos absorbe
lo que pasa de verdad con español colombiano por teléfono —/s/ aspirada, yeísmo, seseo, `h`
muda, `b`/`v`—. Sin esas tolerancias, *«me sale **pu** de la herida»*, *«no puedo
**respira**»*, *«se me abrió la **erida**»* y *«me **desmalle** anoche»* daban `none`, y dos
de ellos eran `critical`.

Se hace en el patrón y no normalizando el texto de entrada, para que los índices de la
coincidencia sigan sirviendo al análisis de la negación.

### Verificado

| Arnés | Resultado |
|---|---|
| `evals/alarmas_base.py` | 10/10 casos base |
| `evals/alarmas_adversariales.py` | **16/16 falsos negativos** detectados |
| `evals/lexico_colombiano.py` | 85/85 términos y trampas |
| `evals/decision_seguridad.py` | 16/16 de la regla de combinación |

Los casos adversariales no verifican lo que las reglas ya cubren: están diseñados para
romperlas. Cada uno nace de una hipótesis de cómo puede fallar el detector con habla real.

**Un dato que se reporta y no se esconde:** los 4 casos de falso positivo siguen generando
ruido —«mi hija tiene fiebre», «el médico me dijo que si hay pus llame»—. Son observación y
no compuerta, a propósito: detectar de más cuesta una alerta revisable; detectar de menos
cuesta un paciente. Distinguir un síntoma reportado de uno citado o hipotético es trabajo
del juez, que sí entiende el contexto.

---

## Etapa 6 · Diálogo

### El estado vive en código, no en la memoria del modelo

Es la pieza que hace fiable a un modelo pequeño. El procedimiento, el día postoperatorio y
los síntomas ya reportados se extraen con reglas y se guardan en slots; al modelo se le
entrega un resumen compacto en cada turno. No puede perder el hilo ni contradecirse entre
turnos **porque no es él quien recuerda**.

Extraer slots con reglas tiene además dos ventajas prácticas: es instantáneo y es
comprobable sin gastar una invocación. `evals/deteccion_procedimiento.py` son 33
comprobaciones con habla de paciente colombiano, varias degradadas como las entrega un
reconocedor de voz.

**La última mención manda**, tanto para el procedimiento como para el día. Congelar la
primera no dejaba forma de corregir: si el paciente se equivocaba —o el reconocedor
transcribía mal el primer turno— la recuperación quedaba sesgada al protocolo equivocado
durante toda la llamada.

### D19 — el prompt, dimensionado para un modelo pequeño

Se midieron cuatro colocaciones de las mismas reglas sobre los mismos casos clínicos,
contando violaciones **comprobables por código** —usted/tuteo, idioma, brevedad, muletilla
inicial— y no juzgadas por otro modelo, que sería medir con la misma vara torcida:

| colocación | respuestas limpias | tokens/turno |
|---|---:|---:|
| reglas extensas en el sistema | 2/5 | 1.205 |
| núcleo en el sistema | 4/5 | 357 |
| **reglas junto a la pregunta** | **5/5** | 376 |

El prompt largo es peor en las tres dimensiones a la vez: obedece menos, cuesta más tokens
y tarda más. En CPU recortar bajó el tiempo hasta el primer token de 4.508 a 1.169 ms.

Dos detalles de formato que costaron medir:

- **Las reglas van sin numerar.** Numeradas chocaban con los fragmentos del contexto, que
  llegan como `[#1 | doc §sección]`: el modelo mezclaba las dos numeraciones y respondía
  «según la regla #1», citando una instrucción en vez de un documento.
- **No se le pide citar.** Un modelo pequeño identifica bien el fragmento y lo escribe en el
  campo equivocado; la cita la derivará el código en la etapa 7. Quitarle ese trabajo libera
  el prompt para lo que sí depende de él.

### Las dos rutas comparten todo lo que decide algo

`handle_turn` (texto) y `stream_turn` (voz) solo se diferencian en cómo entregan el texto.
`_preparar`, `_recuperar`, `_ruta_segura` y `_cerrar` son de las dos.

Es una decisión de estructura contra una clase concreta de defecto: escribir cada ruta por
su lado hace que un control se corrija en una y no en la otra, y esa variante es
especialmente traicionera —la prueba pasa por donde se corrigió y el fallo vive donde nadie
volvió a mirar—. `evals/dialogo.py` prueba las dos con **los mismos casos** y compara sus
salidas.

### Un fallo que solo apareció contra el sistema completo

La compuerta de ausencia de corpus se equivocaba en un caso real. Un paciente de
apendicectomía preguntó cuándo podía ducharse, **un documento subido por la consola lo
respondía**, y aun así recibió *«para su cirugía no tengo cargados documentos de cuidado»*.

La causa: la compuerta miraba solo si existía material **etiquetado** con ese
procedimiento. Los documentos que sube el evaluador no llevan etiqueta —no podemos adivinar
a qué cirugía pertenecen—, así que la condición se cumplía aunque hubiera respuesta.

Ahora exige las **dos** condiciones: sin material etiquetado **y** sin evidencia. Negarle a
un paciente una respuesta que sí estaba es tan defectuoso como inventarla.

Ninguna de las 17 comprobaciones anteriores lo detectaba, porque todas usaban corpus
etiquetado. Se añadió el caso.

### Verificado

| Arnés | Resultado |
|---|---|
| `evals/deteccion_procedimiento.py` | 33/33 |
| `evals/dialogo.py` | 19/19 |

Y de punta a punta contra el modelo real, por `POST /api/llamada/turno`: responde con el
documento cuando lo hay, declara el límite cuando no, y escala el signo de alarma con
`fuente=both` —reglas y juez coincidiendo—.

**Lo que todavía no está bien**, y es el trabajo de la etapa 7: `citation_ids` sigue
saliendo vacío aunque el modelo use el contexto, y en un turno se coló un `}` suelto al
inicio de la respuesta. Lo primero es la trazabilidad —20 puntos de la rúbrica—; lo segundo,
un carácter que un sintetizador de voz leería en voz alta.

---

## Etapa 7 · Trazabilidad

### El modelo sabe qué fragmento usó; se equivoca de campo

Pedirle a un modelo pequeño que rellene `citation_ids` funciona mal. Pero mirando lo que
escribe se ve que el problema **no es de comprensión**:

> *«Debe avisarse al equipo médico **(citation_ids: #1)**, ya que la salida…»*
> *«Su dolor es leve a moderado según **[#2 | plan_casero.md §Dolor]**…»*
> *«En esta semana inicial postoperatoria **(#3)**, se recomienda caminar…»*

El fragmento correcto está identificado, escrito dentro del texto en vez de en su campo. Es
un fallo de enrutamiento.

**La cita la deriva el código**, con tres fuentes en orden de fiabilidad y todas filtradas
contra lo recuperado en ese turno: lo declarado → las marcas que dejó en el texto →
atribución por solapamiento de contenido con la evidencia.

Efecto medido de punta a punta: la pregunta por la ducha pasó de `citas: (ninguna)` a
`citas: ['protocolo_apendicectomia.md']`. Son 20 puntos de la rúbrica que estaban en cero.

### El defecto de voz que esto destapó

Esas marcas iban camino del sintetizador. Sin limpiarlas, el paciente oye *«abre paréntesis
citation ids dos»*. Y se observó además una **llave suelta** al inicio de una respuesta
—resto del JSON que el modelo dejó escapar dentro del propio campo de texto—, que se leería
igual en voz alta.

**En la ruta de voz no basta con limpiar al final**, porque cada frase se sintetiza en
cuanto está completa: la limpieza va frase a frase, antes de emitir.

### Las cifras se auditan contra su fuente

Verificar que las citas existan no basta: el modelo puede citar bien y aun así decir un
número que no está en el fragmento. Comprobar en general que lo dicho se sigue de la fuente
exige juicio semántico, pero **la clase más peligrosa es numérica** —un umbral, un plazo,
una dosis— y esa sí se comprueba sin modelo.

**Un falso positivo que solo apareció contra el modelo real.** El paciente dijo *«tengo
treinta y nueve de fiebre»* y el agente respondió *«fiebre de 39 grados»*: la auditoría lo
marcó como cifra inventada, porque el dígito no aparecía literalmente en lo que dijo el
paciente. Era un falso positivo **en el caso más frecuente de todos**, reportar fiebre.

Los pacientes dicen las cifras en letras y el agente las devuelve en dígitos. Ahora la
auditoría convierte lo que dijo el paciente antes de comparar. Una cifra que nadie mencionó
—«espere 48 horas»— sigue marcándose.

### Verificado

`evals/trazabilidad.py`, 30/30, con respuestas **literales** capturadas del modelo local:
se prueba contra lo que escribe de verdad, no contra lo que convendría que escribiera.

De punta a punta: los dos turnos con contenido clínico citan el protocolo, `grounding=ok`,
y el signo de alarma escala con `fuente=both`.

---

## Etapa 8 · Gobernanza

### La alerta se cierra con una persona, o no se cerró

Una alerta clínica no se «resuelve» borrándola: se acusa recibo, y queda **quién** lo hizo
y **cuándo**. El registro es append-only porque si se pudiera editar no serviría para
reconstruir qué supo el equipo clínico y en qué momento —que es exactamente lo que hay que
poder responder cuando algo sale mal—.

No se puede acusar dos veces: el primer acuse es el que cuenta, y sobrescribirlo borraría
quién la atendió realmente.

**La evidencia viaja con la alerta**: lo que dijo el paciente, qué reglas dispararon, en qué
turno y qué documentos lo sustentan. Una alerta sin su porqué obliga a quien la recibe a
reconstruirla a mano, que es justo lo que no se puede pedir en un contexto clínico.

### Los límites se aplican, no solo se anuncian

Anunciar un tope y seguir aceptando turnos es **peor** que no tenerlo, porque da una falsa
sensación de control. El presupuesto por llamada —turnos y duración— se comprueba antes de
cada turno y devuelve 409 cuando se pasó.

Y lo que se le dice al paciente nunca es «se acabó el presupuesto»: se cierra con cortesía
y con la promesa —cumplida— de que su equipo va a recibir el reporte.

**El interruptor de parada no corta llamadas en curso.** Dejar a un paciente con la palabra
en la boca sería peor que el motivo por el que se activó; lo que impide es abrir nuevas.

### El resumen de cierre lo arma el código, no el modelo

Se construye desde el estado de la llamada y los turnos registrados, así que **no puede
inventar** un síntoma que nadie reportó ni una decisión que no se tomó. Contiene lo que hay
que poder responder después de colgar: procedimiento y día, síntomas, signos de alarma,
riesgo máximo, referencias usadas, observaciones de la auditoría de cifras y próximos pasos.

Es el mismo principio de toda la aplicación: el código produce el dato duro; si el modelo
interviene es para redactarlo, nunca para decidirlo.

### Un comando en vez de nueve

`evals/suite.py` corre todos los arneses y **cuenta las comprobaciones leyendo el resumen de
cada uno**, no sumándolas a mano: un número escrito a mano en un README se queda viejo el
día que alguien añade un caso, y un número viejo es peor que ninguno.

```
268/268 comprobaciones en 10 arneses (21 s, sin invocar al modelo).
```

Quien evalúe esto tiene tiempo limitado; obligarlo a recorrer una lista de comandos es
fricción que no aporta nada.

### Verificado

`evals/gobernanza.py`, 32/32. Y el ciclo completo por HTTP: el signo de alarma levanta la
alerta con su evidencia, una persona acusa recibo, el segundo intento devuelve 409, el
cierre produce el resumen estructurado, y con la parada activa el turno devuelve 503 con el
motivo.

---

## Etapa 9 · Voz

### D4 — dónde ocurren el reconocimiento y la síntesis: en el navegador

No es una simplificación, es la opción que **no cuesta ninguna clave, ninguna descarga ni
ningún servicio de terceros**, y la llamada va por navegador de todos modos.

Con 4,2 GB ya en el reloj del despliegue —modelo del agente y embeddings—, añadir modelos
de voz locales pondría en riesgo la compuerta de 15 minutos a cambio de una ganancia que no
se evalúa: el diseño visual y sonoro no puntúa; lo que puntúa es la **latencia** y el
**comportamiento** de la conversación. Ahí es donde vale la pena gastar el esfuerzo.

Los proveedores server-side —reconocimiento y síntesis de pago— encajan detrás de la misma
interfaz si algún día compensan. Lo que no se hace es entregarlos sin haberlos podido
verificar.

### Lo que sí es ingeniería: frases, no respuestas

El servidor devuelve **frases en cuanto están cerradas**, no el turno completo. El paciente
empieza a oír mientras el modelo todavía genera; esperar al final añadiría segundos de
silencio a cada intervención.

Medido contra el modelo real por WebSocket: **primera frase a 1,2–1,4 s**.

### Interrupción

Si el paciente habla mientras el agente responde, se cancela la generación en curso y se
atiende lo nuevo. Sin esto, corregir a un agente que se equivocó exige esperar a que
termine —que es exactamente lo que hace insoportable hablar con una máquina—.

El arnés lo comprueba de verdad: deja salir un par de frases, cancela, espera de sobra, y
verifica que **no siguieron llegando**.

### Un umbral clínico partido en dos

Contra el modelo real apareció esto:

```
[+1240 ms] Avísele a su equipo clínico de inmediato porque presenta fiebre igual o mayor a 38.
[+1442 ms] 5 grados y salida de material purulento por la incision.
```

El separador de frases cortó **en el punto decimal**. Un sintetizador diría «treinta y ocho
punto», haría una pausa, y seguiría con «cinco grados». Un umbral clínico leído en dos
pedazos es peor que uno leído mal.

Ahora un punto entre dígitos no cierra frase. Y si el carácter siguiente todavía no llegó
se espera, porque cortar ahí sería irreversible.

**Una expectativa mía que estaba mal, no el código.** Al probarlo asumí que dos frases
seguidas debían salir como dos emisiones. No: una frase corta que llega después espera por
`min_chars` y sale al cerrar el turno. Mandar «Puede ducharse.» como emisión propia
produciría un jadeo de tres palabras en el sintetizador. Se corrigió la prueba.

### Verificado

`evals/voz.py`, 15/15. Y de punta a punta por WebSocket contra el modelo real: las frases
llegan escalonadas, la pregunta con respuesta cita el protocolo, y el signo de alarma escala
con `fuente=both` levantando su alerta.

---

## Etapa 10 · Consola

### Contrato funcional, no pieza de diseño

El reto es explícito: la estética no puntúa; las dos superficies son contratos funcionales
mínimos. Así que el esfuerzo va a que **se pueda verificar todo lo que el sistema promete**,
no a que se vea bonito. Una sola página, sin dependencias externas, cuatro superficies:

| Pestaña | Para qué |
|---|---|
| **Llamada** | Contestar, hablar por micrófono, oír al agente. Y escribir, para poder probar sin micrófono |
| **Conocimiento** | Subir, listar y eliminar documentos, con «procesado y disponible» visible |
| **Alertas** | Ver la alerta con su evidencia y **acusar recibo** |
| **Registro** | Cada turno con su decisión, quién la tomó, qué la sustenta y qué costó |

### Lo que la consola hace visible

El **Registro** es la pieza que más aporta y la que no pedía el enunciado. Muestra, por
turno: lo que dijo el paciente, lo que respondió el agente, el riesgo con **qué capa lo
decidió**, el documento que lo sustenta, la marca de grounding y el coste en tiempo y
tokens.

Es lo que convierte «el agente decidió escalar» en algo que alguien puede auditar sin
abrir la base de datos.

### La voz, en el navegador

Reconocimiento con `SpeechRecognition` y síntesis con `speechSynthesis`, ambos en `es-CO`.
Un detalle que importa: **al detectar habla del paciente se corta la síntesis en curso**.
Dejar que el agente termine su frase mientras el paciente ya habló es justo lo que hace
incómoda una llamada con una máquina.

Y hay una alternativa por texto en la misma pestaña, porque no todos los navegadores
reconocen voz y porque probar el cerebro no debería exigir micrófono.

### Verificado en el navegador

Conversación completa, subida por multipart, listado, acuse de recibo —queda «✓ Acusada por
Dra. Gómez»— y el registro con sus turnos, citas y costes.

### Una observación del modelo que se reporta, no se esconde

En el registro se ve que el juez asignó `high` a *«me sacaron el apéndice hace dos días»* —un
saludo—. Es un falso positivo del juez: la capa determinista no vio nada y la decisión salió
`fuente=llm`.

Va en la dirección conservadora, que es la correcta en clínica, pero **genera ruido
operativo**: una alerta por un saludo hace que el equipo empiece a ignorarlas, y una alerta
que se ignora no sirve. Queda anotado como material de la etapa 11: el juez necesita ver que
la capa determinista no vio nada y ser más exigente en ese caso.

---

## Etapa 11 · Calibración

### D2 — `multilingual-e5-large`, elegido por SEPARACIÓN

La métrica correcta no es la similitud media: es cuánto separa las preguntas que el corpus
responde de las que no. Un modelo que puntúa alto en todo no sirve, porque no permite
decidir cuándo abstenerse — y **saber cuándo callar es la mitad del trabajo** en un agente
clínico.

Por eso se mide **AUC**: la probabilidad de que una pregunta respondible puntúe por encima
de una ajena. No depende de dónde se ponga el umbral, que es justo lo que hay que saber
*antes* de elegirlo.

| modelo | AUC | F1 | rechazos falsos | afirmaciones falsas | recuperación |
|---|---:|---:|---:|---:|---:|
| paraphrase-MiniLM *(el que había)* | 0.94 | 0.89 | 0 | 3 | 11/12 |
| paraphrase-mpnet | 0.90 | 0.91 | 2 | 0 | 11/12 |
| **multilingual-e5-large** | **1.00** | **1.00** | **0** | **0** | **12/12** |

La causa es de fondo: los dos primeros son modelos de **paráfrasis** —miden si dos textos
se parecen— y e5 está entrenado para **recuperación** —si uno responde al otro—. De ahí que
`Embedder` distinga `query` de `passage`; sin esos prefijos, e5 rinde por debajo de lo que
puede.

Cuesta 2,24 GB de descarga y 59 ms por consulta, frente a 0,22 GB y 9 ms. Los 59 ms caben
de sobra en un turno de ~1,6 s; la descarga se paraleliza con la del modelo.

### D3 — y un error de método que costó descubrir

Con la muestra de 30 documentos, e5 daba **separación perfecta** y el umbral parecía obvio:
0.81, justo en el hueco entre [0.816..0.842] y [0.772..0.806].

**Contra el corpus completo, no.** Las preguntas ajenas subieron a 0.818–0.832 y el
resultado cayó a 17/21. La razón es simple una vez vista: **cuantos más fragmentos hay, más
oportunidades de que alguno caiga cerca por azar**. El máximo de una distribución crece con
el tamaño de la muestra, y `max_dense` es exactamente eso.

Calibrar sobre una muestra **sobreestima la separación**. Se recalibró sobre los 105
documentos que se entregan:

| umbral | léxico | responde bien | se abstiene bien | rechazos falsos | afirmaciones falsas |
|---:|---:|---:|---:|---:|---:|
| 0.81 | 2 | 12/12 | 5/9 | 0 | 4 |
| **0.82** | **2** | **12/12** | **7/9** | **0** | **2** |
| 0.82 | 3 | 11/12 | 8/9 | 1 | 1 |
| 0.83 | 3 | 9/12 | 9/9 | 3 | 0 |

**Se elige 0.82 con léxico ≥ 2.** Cero rechazos falsos significa que el RAG responde todas
las preguntas legítimas —lo contrario sería no haber construido el RAG—, y las dos fugas
son administrativas: *«¿cuánto cuesta la consulta?»* y *«¿puedo viajar en avión?»*. Ninguna
es clínica, y las dos quedan cubiertas por las capas de abajo: la cita se deriva de la
evidencia y las cifras se auditan contra su fuente.

**El umbral no transfiere.** Ni entre modelos de embeddings —con el anterior el bueno era
0.35— ni entre tamaños de corpus. Está dicho en el código, junto al número.

### El mismo error, en pequeño

Al cambiar el umbral, `evals/dialogo.py` empezó a fallar: *«¿me puedo hacer un tatuaje?»*
puntuaba 0.831 contra su corpus de **un solo documento**, y por debajo del umbral contra los
105 reales. Con un documento no hay contra qué contrastar.

Se cambió el caso por uno inequívocamente ajeno y quedó escrito el porqué. Calibrar es
trabajo de `evals/calibracion.py`, que corre contra el corpus real; lo que prueba el arnés
de diálogo es la **lógica del turno**, no el umbral.

### El falso positivo del juez, corregido

Ahora se le dice al juez **qué vio la capa determinista**. No para ahorrarle trabajo —puede
seguir escalando por su cuenta, que para eso está la capa B— sino para exigirle algo
concreto cuando las reglas no vieron nada. Mencionar la cirugía o saludar no es un síntoma.

### Las métricas, medidas

| Métrica | Valor |
|---|---|
| **Latencia** (fin de habla → primera frase hablable) | **P50 1.217 ms** · P95 2.134 ms |
| Turno completo | P50 2.489 ms |
| Tokens por turno | 2.136 entrada / 126 salida |
| **Invocaciones al modelo por turno** | 2 |
| **Consultas al RAG por llamada** | 5 en 6 turnos |
| **Costo de API por llamada** | **$0** |

Los turnos que toman la ruta segura responden en ~250 ms, porque no generan nada: abstenerse
es además la respuesta más rápida posible.

### El índice, por fin versionado

Se entrega construido (105 documentos). Reconstruirlo toma más de una hora, que no cabe en
el reloj del despliegue. Se versiona **ahora y no antes** porque hasta esta etapa sus
entradas —modelo de embeddings— estaban abiertas: comprometerlo en la etapa 3 habría dejado
en la historia un binario grande que había que reemplazar por otro.

### El índice pesaba 52 MB, y la mitad era desperdicio

GitHub avisó al empujarlo: *«File corpus_reto.db is 51.16 MB; larger than the recommended
50 MB»*. Pasó, pero justo. Y no es solo el reloj del `git clone`: el índice **se copia
entero a memoria al arrancar**, así que lo que pese en disco lo pesa también en RAM.

Al abrirlo, los 52 MB no eran corpus. Eran dos cosas, y ninguna es conocimiento clínico.

**Los vectores estaban en `float32`.** 10.955 fragmentos × 1.024 dimensiones × 4 bytes =
45 MB de los 52. Guardarlos en media precisión los deja en 22 MB. La pregunta no es si cabe
—cabe— sino **si mueve la decisión**, porque el umbral D3 se calibró con una separación de
0.01 entre valores contiguos. Se midió antes de aplicarlo, sobre el corpus real y las 21
preguntas de calibración:

| | float32 | float16 |
|---|---|---|
| Mayor cambio en `max_dense` | — | **1,3e-05** |
| Decisiones de evidencia distintas | — | **0 de 21** |
| Top-5 de citas distintos | — | **0 de 21** |

Cuatro órdenes de magnitud por debajo de la granularidad del umbral. `int8` pesaría la mitad
otra vez, pero mueve `max_dense` 2e-04 y obliga a guardar una escala por vector: se descarta
por relación, no por miedo.

**El desperdicio, en cambio, no lo vio nadie hasta medirlo.** Con los vectores ya en 22 MB,
el archivo bajó a… 45 MB. Los datos sumaban 30. Los 15 MB restantes eran páginas medio
vacías: una fila de `chunks` mide ~2,7 KB, y con páginas de 4 KB —el defecto de SQLite— cada
fila se desborda a una página propia de la que usa 1,7 KB. Se pagaba una página entera por
fila.

| `page_size` | archivo | leer los 10.955 fragmentos |
|---|---|---|
| 512 B | 31,6 MB | 157 ms |
| **2 KB** | **32,5 MB** | **113 ms** |
| 4 KB (defecto) | 45,1 MB | 106 ms |

Se elige 2 KB: 1 MB más que la página mínima y 44 ms más rápido. El coste sobre el turno son
7 ms contra los 4 KB —ruido frente a una latencia P50 de 1.640 ms—; bajar a 512 B ahorraría
1 MB y costaría 50.

**52 MB → 32 MB, sin tocar una sola respuesta.** 283/283 verde y la calibración da lo mismo
que antes: 19/21, con las dos fugas administrativas conocidas.

Un detalle de entrega que va con esto: el blob grande ya estaba **en la historia de Git**, y
ahí un archivo no se sustituye, se acumula. Añadir el índice comprimido en un commit nuevo
dejaría los dos —52 MB y 32— en el `clone` del jurado, que es exactamente lo contrario de lo
que se busca. Por eso el índice se reescribe **en el commit que lo introdujo**, que además es
la punta de la rama y de un solo autor.

---

## Ensayo · dos defectos que solo aparecen usándolo

Los 283 arneses pasaban y la calibración daba lo esperado. Los dos defectos de abajo
salieron de **hablar con el agente por la consola**, no de correr pruebas, y los dos son de
la clase que arruina una demostración sin romper nada técnicamente.

### La llamada nacía muerta

Al probar la consola, cada turno respondía lo mismo: *«Se nos acabó el tiempo de esta
llamada. Ya le paso el reporte a su equipo clínico.»* En el turno 4 de 25, sin haber
hablado antes.

El presupuesto por llamada tiene dos techos, turnos y duración, y el de duración arrancaba
el cronómetro **al construir el objeto**. La llamada se abre en el `lifespan`, es decir al
levantar el servidor. Súmese cómo se usa esto de verdad: quien evalúa arranca el servidor,
revisa la consola, sube un documento de prueba, mira las pestañas… y a los quince minutos
la llamada ya está agotada antes de la primera palabra.

No es un defecto de laboratorio: es **exactamente el escenario de la compuerta de
despliegue**. Un límite que se gasta solo es peor que no tenerlo, por la misma razón por la
que un límite anunciado y no aplicado lo era.

El arreglo dice lo que el límite siempre quiso decir: un presupuesto *por llamada* mide la
llamada, y mientras nadie ha hablado no hay llamada que medir. El cronómetro arranca en el
primer turno.

El arnés que había pasaba por casualidad —`max_segundos=0` da «excedido» tanto si el reloj
corre como si marca cero—, así que se corrigió además de añadir los casos nuevos: una
llamada abierta y en silencio no consume nada, y una envejecida a mano sí se agota.

### «Espere ya voy» → fotodocumentación de la válvula ileocecal

El otro se ve en el transcript y no se puede desver. El paciente escribió *«espere ya voy»*
y el agente respondió con una recomendación sobre registros fotográficos de la colonoscopia
y conservación de imagen de la válvula ileo-cecal y el ciego.

Nada falló: el fragmento existe, la recuperación lo trajo bien y la cita lo sustenta. Lo que
falla es de más arriba. La ruta segura solo se activaba ante **preguntas**; un turno que no
pregunta nada igual recuperaba, y al modelo se le entregaba un contexto clínico que no venía
a cuento. Un modelo pequeño y servicial, con contexto delante y una instrucción de responder,
lo usa.

La regla nueva es una frase: **contexto clínico solo cuando hay algo clínico que sustentar**
—una pregunta que responder, un síntoma que atender o una regla determinista que ya disparó—.
En los demás turnos el objetivo ya era avanzar el checklist, y para preguntar por el dolor no
hace falta evidencia.

La contraparte es lo que hace la regla defendible, y está en el arnés: no se trata de exigir
signo de pregunta. *«Me duele mucho la herida»* no pregunta nada y **sí** recupera, porque
menciona un tema del seguimiento. La lista de temas es la misma con la que se llenan los
slots del estado, no una segunda lista que se pueda quedar vieja.

**Efecto lateral: las métricas mejoraron.** El turno de apertura —«buenas, me sacaron el
apéndice hace dos días»— dejó de recuperar y de arrastrar 2.000 tokens de contexto que no
usaba:

| | antes | ahora |
|---|---|---|
| Latencia P50 (primera frase) | 1.640 ms | **1.217 ms** |
| Turno completo P50 | 3.088 ms | **2.489 ms** |
| Tokens de entrada por llamada | 15.067 | **13.061** |
| Consultas al RAG por llamada | 6 de 6 turnos | **5 de 6** |

No se buscó la latencia: se buscó que el agente no hablara de más. La latencia bajó porque
el trabajo que se quitó era trabajo que no había que hacer.

### Verificado

292/292 comprobaciones (nueve nuevas: cuatro del cronómetro, cinco del turno sin contenido
clínico). `ruff` limpio. Calibración sin cambios: 19/21, mismas dos fugas administrativas.
