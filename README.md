# Vera — agente de voz para seguimiento postoperatorio

Vera llama a un paciente recién operado, conversa por voz en español colombiano, responde
**únicamente** con lo que dicen sus documentos clínicos —citando cuál sustenta cada
respuesta— y **escala a un humano** ante un signo de alarma.

Tech Sphere Challenge 2026 · [Arquitectura](docs/arquitectura.md) · [Bitácora](docs/bitacora.md)

---

## Estado

Etapa 4 de 11: el agente razona con un modelo de lenguaje local.

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

## El corpus clínico

Los documentos del reto **no se redistribuyen aquí**: son obra de sus autores y el material
oficial los incluye solo como referencia. Este repositorio trae el script que construye el
índice a partir de ellos.

```bash
uv run scripts/corpus.py --ruta <repo-del-reto>/dataset/textos --revisar   # informa
uv run scripts/corpus.py --ruta <repo-del-reto>/dataset/textos            # construye
```

La ingesta reporta lo que encuentra en vez de tragárselo: PDFs sin capa de texto,
casi-duplicados y procedimientos que se quedan sin material propio.

Cada documento queda etiquetado con su procedimiento, y **la recuperación se acota al del
paciente**. No es una mejora de precisión: los protocolos postoperatorios comparten
vocabulario casi por completo, así que sin ese filtro una pregunta sobre una cirugía se
responde citando otra.

## Pruebas

```bash
uv run python -m evals.conocimiento_vivo             # 10/10 · ingesta, versionado y olvido
uv run python -m evals.pertinencia_procedimiento     # 13/13 · la recuperación no cruza cirugías
```

No invocan ningún modelo de lenguaje: corren en segundos y siempre dan lo mismo.

La comparativa que eligió el modelo sí lo invoca, y tarda:

```bash
uv run python -m evals.spike_modelo        # CPU pura (el escenario que decide)
uv run python -m evals.spike_modelo --gpu  # con GPU, para contrastar
```

## Estructura

```
server/agent/       cliente del modelo, esquemas de salida, lectura incremental
server/knowledge/   ingesta, troceado, embeddings, recuperación híbrida, tombstones
server/             backend (FastAPI)
scripts/            preparación del entorno y construcción del índice
evals/              arneses de prueba reproducibles
docs/               arquitectura y bitácora de decisiones
```

## Licencia

MIT.
