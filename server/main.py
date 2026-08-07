"""Aplicación FastAPI.

`/api/health` existe desde el primer commit y no por costumbre: durante todo el
desarrollo la pregunta «¿está bien lo que tengo montado?» se responde por HTTP,
sin levantar interfaz ni micrófono. Cada pieza que se añade reporta aquí su
estado real, no su configuración.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from server.agent.llm import probe
from server.config import settings
from server.knowledge.service import KnowledgeService


@asynccontextmanager
async def lifespan(app: FastAPI):
    # En memoria: la sesión de demostración empieza limpia y lo que se suba
    # durante ella no se arrastra a la siguiente.
    app.state.knowledge = KnowledgeService(db_path=":memory:")
    yield
    app.state.knowledge.close()


app = FastAPI(title="Vera", lifespan=lifespan)


class DocumentoTexto(BaseModel):
    name: str
    text: str


@app.get("/api/health")
async def health() -> dict:
    k = app.state.knowledge
    # Estado REAL del modelo, no presencia de configuración: lo que puede fallar
    # es que el runtime no esté arriba o que el modelo no esté descargado, y eso
    # hay que verlo aquí y no en el primer turno de una llamada.
    modelo = await asyncio.to_thread(probe)
    return {
        "status": "ok",
        "version": app.version,
        "llm_ready": modelo["server"] and modelo["model_present"],
        "llm": modelo,
        "embedding_model": k.embedder.name,
        "docs": len(k.documents(include_deleted=False)),
    }


@app.get("/api/knowledge")
async def listar() -> dict:
    return {"documents": [d.__dict__ for d in app.state.knowledge.documents()]}


@app.post("/api/knowledge/add")
async def agregar(body: DocumentoTexto) -> dict:
    try:
        doc_id = await asyncio.to_thread(app.state.knowledge.add_text, body.name, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"doc_id": doc_id, "estado": "procesado y disponible"}


@app.delete("/api/knowledge/{doc_id}")
async def eliminar(doc_id: int) -> dict:
    if not await asyncio.to_thread(app.state.knowledge.delete, doc_id):
        raise HTTPException(status_code=404, detail="documento no encontrado o ya eliminado")
    return {"doc_id": doc_id, "estado": "olvidado"}


@app.get("/api/knowledge/query")
async def consultar(q: str, k: int = 5) -> dict:
    """Consulta directa al RAG. Existe para poder auditar la recuperación sin
    pasar por el agente: si una respuesta sale mal, lo primero que hay que saber
    es si el problema fue lo que se recuperó o lo que se generó con ello."""
    r = await asyncio.to_thread(app.state.knowledge.query, q, k)
    return {
        "has_evidence": r["has_evidence"],
        "max_dense": round(r["max_dense"], 4),
        "citations": [
            {"chunk_id": c.chunk_id, "doc_name": c.doc_name, "section": c.section,
             "dense": round(c.dense, 4), "text": c.text}
            for c in r["citations"]
        ],
    }


@app.get("/")
async def index() -> dict:
    return {"servicio": "Vera", "salud": "/api/health"}


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
