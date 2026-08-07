"""Esquemas de salida del agente.

Con un runtime local el esquema **no es una sugerencia**: se traduce a una
gramática y el modelo no puede emitir nada fuera de ella. Eso convierte al
esquema en el lugar correcto para imponer invariantes, en vez de pedirlas por
prompt y confiar en que se obedezcan.

Ver `grounded_response_for`, que hace literalmente imposible citar un documento
que no se le ofreció al modelo.

Los tipos usan `Literal` en lugar de enumeraciones anidadas para producir un
esquema plano, sin `$defs`: el conversor a gramática lo acepta sin fricción.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, create_model

RiskLevel = Literal["none", "low", "moderate", "high", "critical"]
ActionType = Literal["continue", "advise", "escalate", "emergency"]


class GroundedResponse(BaseModel):
    """Lo que el agente dice, con su fundamento.

    El fundamento va antes del texto porque la gramática obliga a emitir los
    campos en el orden del esquema: cuando `utterance` empieza a llegar, lo que
    lo sustenta ya está completo.

    Aun así, el permiso para hacer afirmaciones clínicas no depende de eso: lo
    decide el retrieval, de forma determinista, antes de generar. La seguridad no
    puede quedar sujeta a en qué orden escriba el modelo.
    """

    uses_knowledge: bool = Field(description="True si la respuesta se apoya en el contexto.")
    citation_ids: list[int] = Field(default_factory=list,
                                    description="Números de los fragmentos usados.")
    extracted_symptoms: list[str] = Field(default_factory=list,
                                          description="Síntomas mencionados por el paciente.")
    utterance: str = Field(description="Lo que se le dice al paciente, breve y claro.")


@lru_cache(maxsize=256)
def grounded_response_for(chunk_ids: tuple[int, ...]) -> type[GroundedResponse]:
    """`GroundedResponse` con `citation_ids` restringido a los fragmentos ofrecidos.

    Un modelo pequeño cita mal: confunde un número con otro, o cita cuando la
    respuesta no estaba en el contexto. Y una cita equivocada es peor que
    ninguna, porque el reto verifica cada referencia contra su fuente.

    Ningún prompt arregla eso de forma fiable. El esquema sí: al declarar
    `citation_ids` como lista de un `Literal` con los ids reales, la gramática
    deja fuera cualquier otro número —el modelo no puede escribirlo—. Y cuando no
    hay contexto pertinente, `maxItems: 0` hace que la única lista emitible sea la
    vacía, que es justo la conducta correcta ante una pregunta fuera del corpus.

    Se cachea porque se construye una vez por turno y el juego de ids se repite.
    """
    if not chunk_ids:
        campo = (list[int], Field(default_factory=list, max_length=0,
                                  description="Sin contexto disponible: debe ir vacío."))
    else:
        campo = (list[Literal[chunk_ids]],  # type: ignore[valid-type]
                 Field(default_factory=list,
                       description=f"Números de los fragmentos usados: {list(chunk_ids)}."))

    return create_model(  # type: ignore[call-overload,no-any-return]
        "GroundedResponseCitas", __base__=GroundedResponse, citation_ids=campo)


class RiskAssessment(BaseModel):
    """Salida del juez de riesgo.

    Los campos van **acotados por longitud**, y no es cosmético: sin ese límite el
    modelo copia el fragmento entero —encabezado incluido— en `evidence` y agota
    su presupuesto de tokens a mitad del JSON. El resultado es una salida truncada
    y un turno degradado en el momento más delicado de la llamada.

    `risk` va primero a propósito: es lo único que la decisión necesita, y la
    gramática obliga a respetar el orden del esquema, así que llega aunque la
    generación se corte después.
    """

    risk: RiskLevel
    rationale: str = Field(max_length=240, description="Una frase, breve.")
    evidence: list[Annotated[str, StringConstraints(max_length=160)]] = Field(
        default_factory=list, max_length=3,
        description="Hasta 3 frases cortas del paciente o del protocolo.")
