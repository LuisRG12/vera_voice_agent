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
    # Modelo de embeddings. Cuál conviene es la decisión D2 y se resuelve
    # midiendo separación entre preguntas con respuesta y sin ella.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    knowledge_db: str = "vera_knowledge.db"
    # Umbral de "sin evidencia" (decisión D3, pendiente de calibrar contra el
    # corpus real). Un valor provisional es honesto mientras se declare como tal.
    min_evidence: float = 0.55

    # --- Persistencia ---
    governance_db: str = "vera_governance.db"
    calls_db: str = "vera_calls.db"


settings = Settings()
