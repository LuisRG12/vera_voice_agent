"""Configuración de la aplicación, con valores por defecto que funcionan.

Todo tiene un default razonable: quien clone el repositorio debe poder arrancar
sin escribir un `.env`. Las variables existen para ajustar, no para desbloquear.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Ruta absoluta al `.env`: se resuelve igual sin importar desde qué directorio se
# lance la aplicación. Con ruta relativa, arrancar desde otra carpeta cargaba una
# configuración vacía sin avisar de nada.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000

    # --- Modelo de lenguaje (local) ---
    # Cuál de los permitidos se usa es la decisión D1, resuelta midiendo en CPU
    # (ver bitácora, etapa 4). No hace falta ninguna clave.
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "llama3.2:3b"
    llm_num_ctx: int = 3072       # el default del runtime trunca el prompt del turno
    llm_temperature: float = 0.3  # bajo: la gramática fija el formato, no el criterio
    llm_keep_alive: str = "1h"    # evita la recarga del modelo a mitad de llamada
    llm_timeout_s: float = 120.0  # un modelo pequeño en CPU es lento; mejor esperar
    # Capas descargadas a la GPU. `None` = que decida el runtime, que es lo que
    # hace que esto corra en cualquier equipo. `0` fuerza CPU y existe para poder
    # MEDIR ese escenario, no para degradar a quien tenga tarjeta.
    llm_num_gpu: int | None = None

    # --- Conocimiento ---
    # D2, resuelta midiendo separación entre preguntas con respuesta y sin ella
    # (`evals/spike_embeddings.py`): AUC 1.00 frente a 0.94 del anterior, y las
    # dos poblaciones dejan de solaparse. Es un modelo de RECUPERACIÓN, no de
    # paráfrasis, y por eso `Embedder` distingue `query` de `passage`.
    embedding_model: str = "intfloat/multilingual-e5-large"
    knowledge_db: str = "vera_knowledge.db"
    # D3, calibrada contra el CORPUS COMPLETO que se entrega —no contra una
    # muestra— con `evals/calibracion.py --barrido`:
    #
    #   umbral 0.82, léxico >= 2  ->  12/12 respondidas, 0 rechazos falsos, 2 fugas
    #   umbral 0.83, léxico >= 3  ->   9/12 respondidas, 3 rechazos falsos, 0 fugas
    #
    # Se elige el primero: cero rechazos falsos significa que el RAG responde
    # todas las preguntas legítimas, y las dos fugas son administrativas —costo
    # de la consulta, clave del wifi—, no clínicas. Además quedan cubiertas por
    # las capas de abajo: la cita se deriva de la evidencia y las cifras se
    # auditan contra su fuente.
    #
    # OJO: el número NO transfiere. Ni entre modelos de embeddings (con el
    # anterior el valor bueno era 0.35) ni entre tamaños de corpus (calibrado
    # sobre una muestra de 30 documentos daba 0.81, y con los 105 reales se queda
    # corto: cuantos más fragmentos, más oportunidades de que alguno caiga cerca
    # por azar). Cambiar cualquiera de los dos obliga a recalibrar.
    min_evidence: float = 0.82
    # Términos clínicos que la pregunta debe compartir con un fragmento del top
    # para contar como evidencia léxica, cuando la semántica no alcanza.
    min_lexico: int = 2

    # --- Persistencia ---
    governance_db: str = "vera_governance.db"
    calls_db: str = "vera_calls.db"


settings = Settings()
