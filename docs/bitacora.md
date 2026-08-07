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
