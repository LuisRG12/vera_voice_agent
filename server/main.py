"""Aplicación FastAPI.

`/api/health` existe desde el primer commit y no por costumbre: durante todo el
desarrollo la pregunta «¿está bien lo que tengo montado?» se responde por HTTP,
sin levantar interfaz ni micrófono. A medida que se añadan piezas —modelo,
conocimiento, voz— cada una reporta aquí su estado real, no su configuración.
"""
from fastapi import FastAPI

from server.config import settings

app = FastAPI(title="Vera")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/")
async def index() -> dict:
    return {"servicio": "Vera", "salud": "/api/health"}


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
