"""Cliente del modelo de lenguaje, local vía Ollama.

**Por qué local.** El reto fija una lista cerrada de modelos permitidos y usar
otro descalifica la entrega. De los cuatro, los dos de nube están retirados por
sus proveedores, así que los únicos vivos son locales. Correr local además
elimina la clave del evaluador, elimina el techo de peticiones por minuto del
nivel gratuito —que con dos invocaciones por turno se agota en una conversación
real— y deja el costo por llamada en cero.

**Dónde corre.** No se fuerza nada: el runtime usa GPU si la hay y CPU si no, así
que la solución levanta en cualquier equipo. `llm_num_gpu=0` existe para **medir**
el escenario sin GPU, que es el que hay que reportar honestamente, no para
degradar a quien sí la tenga.

**Salida estructurada por gramática.** El esquema se traduce a una gramática y el
modelo no puede emitir nada que no la cumpla. Es más fuerte que pedir un formato
por prompt —donde el esquema es una instrucción desobedecible— y es lo que hace
viable un modelo pequeño aquí: el formato deja de ser un riesgo y el reintento por
validación pasa a ser excepcional.

Se habla por HTTP plano con `httpx`. Un SDK no aporta nada sobre una API local.
"""
from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from server.agent.stream_parse import PartialToolJSON
from server.config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def _options(max_tokens: int) -> dict[str, Any]:
    """Opciones de generación por petición.

    `num_ctx` es la que más importa: el runtime usa un valor por defecto pequeño
    y el prompt de un turno —estado de la llamada, fragmentos recuperados,
    objetivo— lo desborda. Al desbordarse trunca **por el principio**, que es
    donde va el prompt de sistema con las reglas de seguridad, y lo hace sin
    avisar. Se fija explícito.
    """
    opts: dict[str, Any] = {
        "num_ctx": settings.llm_num_ctx,
        "num_predict": max_tokens,
        "temperature": settings.llm_temperature,
    }
    if settings.llm_num_gpu is not None:
        opts["num_gpu"] = settings.llm_num_gpu
    return opts


def _usage_of(payload: dict) -> dict[str, int]:
    return {
        "input_tokens": payload.get("prompt_eval_count", 0) or 0,
        "output_tokens": payload.get("eval_count", 0) or 0,
    }


class StructuredLLM:
    def __init__(self, model: str | None = None, host: str | None = None):
        self.model = model or settings.llm_model
        self._host = (host or settings.ollama_host).rstrip("/")
        self._url = f"{self._host}/api/chat"
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def _payload(self, system: str, messages: list[dict], schema: type[BaseModel],
                 max_tokens: int, stream: bool) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "format": schema.model_json_schema(),
            "stream": stream,
            "options": _options(max_tokens),
            # Sin esto el runtime descarga el modelo tras unos minutos ociosos, y
            # el primer turno después de una pausa paga la recarga entera. En una
            # llamada de voz eso es un silencio inaceptable.
            "keep_alive": settings.llm_keep_alive,
        }

    def structured(self, system: str, user: str, schema: type[T],
                   max_tokens: int = 400, retries: int = 1) -> tuple[T, dict]:
        """Una respuesta validada contra el esquema."""
        messages: list[dict] = [{"role": "user", "content": user}]
        last_err: Exception | None = None

        with httpx.Client(timeout=settings.llm_timeout_s) as client:
            for _ in range(retries + 1):
                try:
                    r = client.post(
                        self._url,
                        json=self._payload(system, messages, schema, max_tokens, False))
                except httpx.HTTPError as exc:
                    raise LLMError(f"el modelo no responde en {self._host}: {exc}") from exc
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")

                body = r.json()
                usage = _usage_of(body)
                self.last_usage = usage
                content = (body.get("message") or {}).get("content") or ""

                try:
                    return schema.model_validate_json(content), usage
                except (ValidationError, ValueError) as e:
                    last_err = e
                    # La gramática garantiza la forma, así que llegar aquí es raro:
                    # casi siempre es una respuesta truncada por `num_predict`.
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": f"Esa salida no cumple el esquema: {e}. "
                                   "Devuelve únicamente el JSON corregido.",
                    })

        raise LLMError(f"el modelo no produjo una salida válida: {last_err}")

    async def astructured_stream(self, system: str, user: str, schema: type[T],
                                 max_tokens: int = 260):
        """Generador de eventos del turno, para hablar mientras se genera:

            ("grounding", dict)  -> campos previos a `utterance`
            ("delta", str)       -> texto nuevo de `utterance`
            ("final", (obj|None, usage))
        """
        payload = self._payload(system, [{"role": "user", "content": user}],
                                schema, max_tokens, True)
        parser = PartialToolJSON()
        usage = {"input_tokens": 0, "output_tokens": 0}

        async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
            try:
                async with client.stream("POST", self._url, json=payload) as r:
                    if r.status_code >= 400:
                        detalle = (await r.aread()).decode("utf-8", "replace")[:300]
                        raise LLMError(f"HTTP {r.status_code}: {detalle}")

                    async for linea in r.aiter_lines():
                        if not linea.strip():
                            continue
                        try:
                            event = json.loads(linea)
                        except json.JSONDecodeError:
                            continue

                        if err := event.get("error"):
                            raise LLMError(str(err))

                        if chunk := (event.get("message") or {}).get("content"):
                            parser.feed(chunk)
                            if (g := parser.grounding()) is not None:
                                yield "grounding", g
                            if texto := parser.utterance_delta():
                                yield "delta", texto

                        if event.get("done"):
                            usage = _usage_of(event)
            except httpx.HTTPError as exc:
                raise LLMError(f"el modelo no responde en {self._host}: {exc}") from exc

        self.last_usage = usage
        obj: T | None = None
        try:
            obj = schema.model_validate_json(parser.buf)
        except (ValidationError, ValueError):
            obj = None
        yield "final", (obj, usage)


def probe(model: str | None = None, host: str | None = None) -> dict:
    """Estado del runtime y del modelo, sin generar tokens.

    Lo que puede fallar aquí no es una credencial: es que el runtime no esté
    arriba o que el modelo no esté descargado. Eso tiene que verse en `health`,
    no en el primer turno de una llamada.
    """
    model = model or settings.llm_model
    host = (host or settings.ollama_host).rstrip("/")
    out: dict[str, Any] = {"host": host, "model": model, "server": False,
                           "model_present": False, "installed": []}
    try:
        r = httpx.get(f"{host}/api/tags", timeout=5.0)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — se reporta como estado, no se propaga
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    out["server"] = True
    nombres = [m.get("name", "") for m in r.json().get("models", [])]
    out["installed"] = nombres
    # `llama3.2` y `llama3.2:latest` son el mismo modelo: se compara por prefijo.
    base = model.split(":")[0]
    out["model_present"] = any(n == model or n.split(":")[0] == base for n in nombres)
    return out
