# Vera — agente de voz para seguimiento postoperatorio

Vera llama a un paciente recién operado, conversa por voz en español colombiano, responde
**únicamente** con lo que dicen sus documentos clínicos —citando cuál sustenta cada
respuesta— y **escala a un humano** ante un signo de alarma.

Tech Sphere Challenge 2026 · [Arquitectura](docs/arquitectura.md) · [Bitácora](docs/bitacora.md)

---

## Estado

Etapa 1 de 11: andamiaje. El servidor levanta y reporta su estado.

## Requisitos

Python 3.11+ y [uv](https://docs.astral.sh/uv/). Sin Docker, sin base de datos, sin GPU.

## Ejecutar

```bash
uv sync
uv run main.py
```

Verificar:

```bash
curl http://localhost:8000/api/health
```

## Estructura

```
server/     backend (FastAPI)
docs/       arquitectura y bitácora de decisiones
main.py     punto de entrada
```

## Licencia

MIT.
