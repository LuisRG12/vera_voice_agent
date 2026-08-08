"""Aplicación FastAPI.

`/api/health` existe desde el primer commit y no por costumbre: durante todo el
desarrollo la pregunta «¿está bien lo que tengo montado?» se responde por HTTP,
sin levantar interfaz ni micrófono. Cada pieza que se añade reporta aquí su
estado real, no su configuración.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server.agent.dialogue import DialogueManager
from server.agent.llm import probe
from server.config import settings
from server.governance.limits import MENSAJE_LIMITE, KillSwitch
from server.governance.store import GovernanceStore
from server.knowledge.service import KnowledgeService
from server.recorder.resumen import construir as construir_resumen
from server.recorder.store import CallStore
from server.voz.sesion import SesionLlamada


@asynccontextmanager
async def lifespan(app: FastAPI):
    # En memoria: la sesión de demostración empieza limpia y lo que se suba
    # durante ella no se arrastra a la siguiente.
    app.state.knowledge = KnowledgeService(db_path=":memory:")
    app.state.gobernanza = GovernanceStore(settings.governance_db)
    app.state.llamadas = CallStore(settings.calls_db)
    app.state.killswitch = KillSwitch()
    _abrir_llamada(app)
    yield
    app.state.knowledge.close()
    app.state.gobernanza.close()
    app.state.llamadas.close()


def _abrir_llamada(app: FastAPI) -> None:
    """Abre una llamada nueva y engancha el diálogo a su registro."""
    call_id = app.state.llamadas.open_call()
    app.state.call_id = call_id
    app.state.turno_idx = 0
    app.state.dialogo = DialogueManager(
        app.state.knowledge, governance=app.state.gobernanza, call_id=call_id)


# Sin `Cache-Control` el navegador aplica caché heurística y puede mostrar una
# consola vieja sin preguntar. `no-cache` no significa «no guardes», sino
# «guarda pero revalida siempre»: con el ETag que ya emite FastAPI, la
# revalidación es un 304 sin cuerpo.
SIN_CACHE = {"Cache-Control": "no-cache"}

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


@app.post("/api/knowledge/upload")
async def subir(file: UploadFile = File(...)) -> dict:
    """Sube un documento desde la consola.

    Mismo parser que el resto: la consola y los scripts recorren exactamente el
    mismo código, para que no puedan divergir. El documento sube **sin
    procedimiento** a propósito —no podemos adivinar a qué cirugía pertenece el
    que traiga quien evalúe, y equivocarnos lo dejaría invisible—.
    """
    datos = await file.read()
    try:
        doc_id = await asyncio.to_thread(
            app.state.knowledge.add_bytes, file.filename or "documento", datos)
    except (ValueError, Exception) as exc:  # noqa: B014 — formato o contenido inválido
        raise HTTPException(status_code=400, detail=str(exc)[:200]) from exc
    return {"doc_id": doc_id, "nombre": file.filename, "estado": "procesado y disponible"}


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


class TurnoPaciente(BaseModel):
    text: str


@app.post("/api/llamada/turno")
async def turno(body: TurnoPaciente) -> dict:
    """Un turno de conversación por texto.

    Existe antes que la voz a propósito: permite ejercitar y auditar el cerebro
    del agente —decisión de riesgo, citas, estado— sin micrófono ni proveedor de
    voz de por medio. Cuando algo suene mal en una llamada, aquí se puede
    reproducir el turno exacto.
    """
    if app.state.killswitch.activo:
        raise HTTPException(
            status_code=503,
            detail=f"El agente está detenido por un operador ({app.state.killswitch.motivo}).")

    dm = app.state.dialogo
    # El límite no solo se anuncia: se aplica. Anunciarlo y seguir aceptando
    # turnos es peor que no tenerlo, porque da una falsa sensación de control.
    if motivo := dm.budget.excedido():
        raise HTTPException(status_code=409, detail=MENSAJE_LIMITE[motivo])

    t = await asyncio.to_thread(dm.handle_turn, body.text)
    app.state.turno_idx += 1
    await asyncio.to_thread(app.state.llamadas.record_turn,
                            app.state.call_id, app.state.turno_idx, body.text, t)
    return t.model_dump()


@app.get("/api/llamada/estado")
async def estado_llamada() -> dict:
    return {**app.state.dialogo.state.snapshot(),
            "call_id": app.state.call_id,
            "presupuesto": app.state.dialogo.budget.snapshot()}


@app.post("/api/llamada/colgar")
async def colgar() -> dict:
    """Cierra la llamada con su resumen y abre una nueva."""
    detalle = app.state.llamadas.call_detail(app.state.call_id)
    resumen = construir_resumen(app.state.dialogo.state, detalle["turns"] if detalle else [])
    app.state.llamadas.close_call(app.state.call_id, resumen)
    cerrada = app.state.call_id
    _abrir_llamada(app)
    return {"call_id": cerrada, "resumen": resumen, "nueva_llamada": app.state.call_id}


@app.get("/api/llamadas")
async def listar_llamadas() -> dict:
    return {"llamadas": [c.__dict__ for c in app.state.llamadas.calls()]}


@app.get("/api/llamadas/{call_id}")
async def detalle_llamada(call_id: int) -> dict:
    if (d := app.state.llamadas.call_detail(call_id)) is None:
        raise HTTPException(status_code=404, detail="llamada no encontrada")
    return d


@app.get("/api/alertas")
async def listar_alertas(solo_activas: bool = False) -> dict:
    return {"alertas": [a.__dict__ | {"activa": a.activa}
                        for a in app.state.gobernanza.alerts(solo_activas)]}


class Acuse(BaseModel):
    who: str
    note: str = ""


@app.post("/api/alertas/{alert_id}/acuse")
async def acusar(alert_id: int, body: Acuse) -> dict:
    """Cierra el ciclo con una persona.

    Sin acuse, «el sistema alertó» es una afirmación que nadie puede verificar.
    No se puede acusar dos veces: el primer acuse es el que cuenta.
    """
    if not app.state.gobernanza.ack(alert_id, body.who, body.note):
        raise HTTPException(status_code=409, detail="la alerta no existe o ya fue acusada")
    return {"alert_id": alert_id, "acusada_por": body.who}


class Parada(BaseModel):
    activo: bool
    motivo: str = ""


@app.get("/api/parada")
async def ver_parada() -> dict:
    return app.state.killswitch.snapshot()


@app.post("/api/parada")
async def cambiar_parada(body: Parada) -> dict:
    """Interruptor de parada: impide abrir llamadas nuevas.

    Las llamadas en curso NO se cortan a mitad de frase: dejar a un paciente con
    la palabra en la boca sería peor que el motivo por el que se activó.
    """
    ks = app.state.killswitch
    ks.activar(body.motivo) if body.activo else ks.liberar()
    return ks.snapshot()


@app.websocket("/ws/llamada")
async def llamada_ws(ws: WebSocket) -> None:
    """La llamada de voz.

    El reconocimiento y la síntesis ocurren en el navegador; por aquí viaja el
    texto en los dos sentidos. Lo que sí hace el servidor es devolver **frases**
    en cuanto están cerradas, para que el paciente empiece a oír mientras el
    modelo todavía genera.
    """
    if app.state.killswitch.activo:
        await ws.accept()
        await ws.send_json({"type": "error",
                            "message": f"Agente detenido ({app.state.killswitch.motivo})."})
        await ws.close()
        return
    await SesionLlamada(ws, app.state).atender()


WEB = Path(__file__).resolve().parent.parent / "web"


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html", headers=SIN_CACHE)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
