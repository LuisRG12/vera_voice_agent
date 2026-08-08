# Vera — agente de voz para seguimiento postoperatorio

Vera llama a un paciente recién operado, conversa por voz en español colombiano, responde
**únicamente** con lo que dicen sus documentos clínicos —citando cuál sustenta cada
respuesta— y **escala a un humano** ante un signo de alarma.

Tech Sphere Challenge 2026 · [Arquitectura](docs/arquitectura.md) · [Bitácora](docs/bitacora.md)

---

## Estado

Completo. Calibrado contra el corpus real y con las métricas medidas.

## Requisitos

Python 3.11+, [uv](https://docs.astral.sh/uv/) y [Ollama](https://ollama.com).
Sin Docker, sin base de datos y **sin ninguna API key**.

## Ejecutar

```bash
uv sync
uv run scripts/setup.py    # descarga modelo y embeddings en paralelo
uv run main.py
```

```bash
curl http://localhost:8000/api/health
```

`llm_ready: true` significa que el runtime está arriba y el modelo descargado. Si es
`false`, el objeto `llm` de esa misma respuesta dice cuál de las dos cosas falta.

## El modelo

`llama3.2:3b`, local. El reto fija una lista cerrada de modelos permitidos; de los cuatro,
los dos de nube están retirados por sus proveedores. Entre los dos locales, la elección se
midió **en CPU** —que es lo que puede tener quien despliegue esto— y está en la
[bitácora](docs/bitacora.md), etapa 4.

**Corre en cualquier equipo**: el runtime usa GPU si la hay y CPU si no. No se fuerza nada.

Correr local no es solo cumplimiento: elimina la clave, elimina el techo de peticiones por
minuto de los niveles gratuitos —que con dos invocaciones por turno se agota en una
conversación real— y deja el costo por llamada en cero.

## Conocimiento vivo

Suba un documento y queda disponible para la siguiente consulta; bórrelo y deja de
sustentar respuestas **al instante**, sin reindexar.

```bash
curl -X POST http://localhost:8000/api/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{"name":"protocolo.md","text":"# Cuidado de la herida\n Mantenga la incisión limpia y seca."}'

curl "http://localhost:8000/api/knowledge/query?q=puedo+mojar+la+herida"
curl http://localhost:8000/api/knowledge
curl -X DELETE http://localhost:8000/api/knowledge/1
```

Formatos admitidos: `.md`, `.txt`, `.pdf`, `.docx`.

## Métricas de operación

Medidas con `uv run python -m evals.metricas` sobre una llamada representativa —apertura,
preguntas con respuesta, una fuera de corpus y un signo de alarma—, no solo los casos
fáciles.

| Métrica | Valor |
|---|---|
| **Latencia** (fin de habla → primera frase hablable) | **P50 1.640 ms** · P95 2.058 ms |
| Turno completo | P50 3.088 ms |
| Tokens por turno | 2.993 entrada / 104 salida |
| Tokens por llamada (6 turnos) | 15.067 entrada / 573 salida |
| **Invocaciones al modelo por turno** | 2 (respuesta y juez de riesgo, en paralelo) |
| **Consultas al RAG por llamada** | 6 (una por turno) |
| **Costo de API por llamada** | **$0** — el modelo corre local |
| Costo equivalente si se pagara API | $0,0016 a $0,10/MTok (referencia declarada) |

La latencia se mide hasta la **primera frase hablable**, no hasta el turno completo: el
agente habla por frases mientras el modelo sigue generando. Los turnos que toman la ruta
segura responden en ~250 ms, porque no generan nada.

El costo real de API es cero. La extrapolación existe para que la cifra sea comparable con
soluciones que sí pagan por token, y el precio de referencia está declarado en el arnés.

## El corpus clínico

Los documentos del reto **no se redistribuyen aquí**: son obra de sus autores y el material
oficial los incluye solo como referencia. Este repositorio trae el script que construye el
índice a partir de ellos.

**El índice ya viene construido** (`corpus_reto.db`, 105 documentos): reconstruirlo toma más
de una hora y no cabe en el reloj del despliegue. El script queda para reproducirlo y
comprobar que no hubo curaduría a mano.

```bash
uv run scripts/corpus.py --ruta <repo-del-reto>/dataset/textos --revisar   # informa
uv run scripts/corpus.py --ruta <repo-del-reto>/dataset/textos            # reconstruye
```

La ingesta reporta lo que encuentra en vez de tragárselo: PDFs sin capa de texto,
casi-duplicados y procedimientos que se quedan sin material propio.

Cada documento queda etiquetado con su procedimiento, y **la recuperación se acota al del
paciente**. No es una mejora de precisión: los protocolos postoperatorios comparten
vocabulario casi por completo, así que sin ese filtro una pregunta sobre una cirugía se
responde citando otra.

## Conversar con el agente

```bash
curl -X POST http://localhost:8000/api/llamada/turno \
  -H "Content-Type: application/json" \
  -d '{"text":"buenas, me sacaron el apéndice hace dos días"}'
```

Cada turno devuelve lo que se dijo, la decisión de riesgo con su justificación, las citas
que la sustentan y el estado de la llamada. La ruta de texto existe antes que la voz a
propósito: permite auditar el cerebro del agente sin micrófono, y reproducir un turno
exacto cuando algo suene mal.

**El estado vive en código**, no en la memoria del modelo: procedimiento, día
postoperatorio y síntomas ya reportados se extraen con reglas. Un modelo pequeño no puede
perder el hilo si no es él quien recuerda.

## Trazabilidad

Cada respuesta clínica registra **de qué fragmento sale**, y la cita la deriva el código —no
la declara el modelo—: un modelo pequeño identifica bien el documento pero lo escribe en el
campo equivocado, así que se recupera de donde lo haya puesto y, si no lo puso, se atribuye
por solapamiento con la evidencia.

Además, el código audita la prosa del modelo: **una cifra clínica que no aparezca en ninguna
fuente citada queda marcada** en la traza de la llamada.

## Seguridad clínica

La decisión de escalar la toman **dos capas independientes** y se queda la más
conservadora: reglas deterministas sobre léxico clínico colombiano, y una valoración del
modelo con el protocolo recuperado.

Lo importante es la consecuencia: **si el modelo se cae a mitad de turno, las reglas ya
evaluaron y la alerta sale igual**. Escalar no depende de que haya un modelo disponible.

El léxico (`server/agent/lexicon.py`) son frases planas, no expresiones regulares: añadir
una forma nueva de decir un síntoma no exige tocar la lógica.

## La consola

Abra **http://localhost:8000** en Chrome. Una sola página con cuatro superficies:

| Pestaña | Qué permite |
|---|---|
| **Llamada** | Contestar y hablar por micrófono; o escribir, para probar sin él |
| **Conocimiento** | Subir, listar y eliminar documentos, con el estado visible |
| **Alertas** | Ver la alerta con su evidencia y acusar recibo |
| **Registro** | Cada turno con su decisión, quién la tomó, qué la sustenta y qué costó |

El **Registro** es lo que convierte «el agente decidió escalar» en algo auditable sin abrir
la base de datos: por turno se ve el riesgo, **qué capa lo decidió**, el documento que lo
sustenta y el coste en tiempo y tokens.

El diseño visual no busca premio —el reto dice explícitamente que no puntúa—; busca que
todo lo que el sistema promete se pueda comprobar.

## La llamada de voz

El reconocimiento y la síntesis ocurren **en el navegador**: sin claves, sin descargas y sin
servicios de terceros. Por el WebSocket viaja texto en los dos sentidos.

Lo que hace el servidor es devolver **frases en cuanto están cerradas**, no la respuesta
completa: el paciente empieza a oír mientras el modelo todavía genera. Medido contra el
modelo real, la primera frase llega en **1,2–1,4 s**.

Y la llamada **se puede interrumpir**: si el paciente habla mientras el agente responde, se
cancela la generación en curso. Sin eso, corregir a un agente que se equivocó exige esperar
a que termine.

```
ws://localhost:8000/ws/llamada
  ->  {"type":"hablar","text":"..."}    lo que dijo el paciente
  <-  {"type":"frase","text":"..."}     cada frase, en cuanto está lista
  <-  {"type":"turno", ...}             decisión, citas y estado del turno
  ->  {"type":"interrumpir"}            corta la generación en curso
```

## Gobernanza

Cuando el agente decide alertar, la alerta queda registrada **con su evidencia** —lo que
dijo el paciente, qué reglas dispararon, qué documentos lo sustentan— y solo se cierra
cuando **una persona acusa recibo**. Sin eso, «el sistema alertó» es una afirmación que
nadie puede verificar.

```bash
curl http://localhost:8000/api/alertas
curl -X POST http://localhost:8000/api/alertas/1/acuse   -H "Content-Type: application/json" -d '{"who":"Dra. Gómez","note":"contactada"}'

curl -X POST http://localhost:8000/api/llamada/colgar    # cierra y devuelve el resumen
curl http://localhost:8000/api/llamadas/1                # registro completo de la llamada

curl -X POST http://localhost:8000/api/parada   -H "Content-Type: application/json" -d '{"activo":true,"motivo":"revisión"}'
```

El presupuesto por llamada y el interruptor de parada **se aplican**: un límite anunciado y
no aplicado es peor que no tenerlo. La parada impide abrir llamadas nuevas, pero no corta
las que están en curso — dejar a un paciente con la palabra en la boca sería peor que el
motivo por el que se activó.

El resumen de cierre lo arma el código desde los datos registrados, no el modelo: no puede
inventar un síntoma que nadie reportó.

## Pruebas

```bash
uv run python -m evals.suite
```

```
283/283 comprobaciones en 11 arneses (29 s, sin invocar al modelo).
```

Ninguna invoca al modelo de lenguaje: corren en segundos y dan siempre lo mismo. Cada arnés
se puede correr suelto (`uv run python -m evals.trazabilidad`, etc.).

Las que invocan al modelo o miden contra el corpus completo se corren aparte, y tardan:

```bash
uv run python -m evals.calibracion            # 19/21 · el agente sabe cuándo callar
uv run python -m evals.calibracion --barrido  # cómo se mueven los dos errores
uv run python -m evals.metricas               # las métricas de arriba, medidas
uv run python -m evals.spike_modelo           # la comparativa que eligió el modelo
uv run python -m evals.spike_embeddings       # la que eligió los embeddings
```

**Las decisiones del proyecto salieron de estos arneses**, no de preferencias: qué modelo,
qué embeddings y dónde va el umbral están medidos y son reproducibles.

## Estructura

```
server/agent/       cliente del modelo, diálogo, seguridad, trazabilidad
server/governance/  alertas, límites por llamada, interruptor de parada
server/recorder/    registro auditable y resumen de cierre
server/voz/         sesión de llamada por WebSocket
server/knowledge/   ingesta, troceado, embeddings, recuperación híbrida, tombstones
server/             backend (FastAPI)
scripts/            preparación del entorno y construcción del índice
evals/              arneses de prueba reproducibles
docs/               arquitectura y bitácora de decisiones
```

## Licencia

MIT.
