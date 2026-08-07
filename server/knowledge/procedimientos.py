"""Los procedimientos del reto y su correspondencia con el corpus.

El dataset trae 40 pacientes repartidos en cinco procedimientos, ocho cada uno, y
una carpeta de documentos por escenario. Este módulo es el **único** sitio donde
vive esa correspondencia: la usa la ingesta para etiquetar cada documento y el
diálogo para acotar la recuperación al procedimiento del paciente. Añadir un
procedimiento nuevo se hace aquí y no obliga a tocar nada más.

**El caso mastectomía.** La carpeta `breast_cancer/` del corpus no contiene un
solo documento de mama: sus 19 archivos son de cáncer de cuello uterino. Como
igual son ocho pacientes reales del dataset, la decisión está declarada y es
explícita: esos documentos se indexan por **el tema que de verdad tratan**, no
por el nombre de su carpeta, y para esos pacientes el agente declara que no tiene
material de su procedimiento y escala.

Etiquetarlos como `mastectomia` habría sido construir la trampa nosotros mismos:
un paciente operado del seno recuperando material de otra cirugía, sin forma de
notarlo. Contestar así es la alucinación clínica que el reto penaliza; declarar
el límite es la conducta que premia.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Procedimiento:
    slug: str
    nombre: str          # como aparece en el perfil clínico del dataset
    modulo: str          # identificador del escenario
    carpeta: str         # carpeta real del corpus (algunas llevan espacios)


PROCEDIMIENTOS = (
    Procedimiento("apendicectomia", "Apendicectomía", "appendicitis", "Appendicitis"),
    Procedimiento("colecistectomia", "Colecistectomía", "cholecystitis", "cholecystitis"),
    Procedimiento("colectomia", "Colectomía", "colorectal_cancer", "colorectal cancer"),
    Procedimiento("reemplazo_articular", "Reemplazo de cadera/rodilla",
                  "total_joint_replacement", "total joint replacement"),
    Procedimiento("mastectomia", "Mastectomía", "breast_cancer", "breast_cancer"),
)

POR_SLUG = {p.slug: p for p in PROCEDIMIENTOS}
POR_NOMBRE = {p.nombre.lower(): p for p in PROCEDIMIENTOS}
POR_CARPETA = {p.carpeta: p for p in PROCEDIMIENTOS}

# Carpetas cuyo CONTENIDO no corresponde a su nombre. Se marca aquí, en un solo
# sitio y con la evidencia a la vista, en lugar de resolverse en silencio dentro
# del script de ingesta.
TEMA_REAL_DE_CARPETA = {
    # Verificado documento por documento: 19/19 tratan de cáncer de cuello
    # uterino y ninguno menciona mama, mastectomía, axila ni linfedema.
    "breast_cancer": "cancer_cuello_uterino",
}

# Procedimientos con pacientes en el dataset pero SIN material clínico propio en
# el corpus entregado. Para estos, el agente declara el límite y escala.
CORPUS_AUSENTE = frozenset({"mastectomia"})


def desde_nombre(nombre: str | None) -> str | None:
    """Slug a partir del nombre del procedimiento. None si no se reconoce."""
    if not nombre:
        return None
    p = POR_NOMBRE.get(nombre.strip().lower())
    return p.slug if p else None


def etiqueta_de_carpeta(carpeta: str) -> str | None:
    """Con qué etiqueta se indexan los documentos de esta carpeta.

    Normalmente el slug del procedimiento; para las carpetas mal nombradas, el
    tema que de verdad contienen —que puede no ser ninguno de los cinco
    procedimientos, y entonces ningún paciente lo recuperará—.
    """
    if tema := TEMA_REAL_DE_CARPETA.get(carpeta):
        return tema
    p = POR_CARPETA.get(carpeta)
    return p.slug if p else None
