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



settings = Settings()
