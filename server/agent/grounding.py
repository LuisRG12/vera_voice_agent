"""Auditoría determinista de cifras contra las fuentes citadas.

Verificar que las citas existan no basta: el modelo puede citar un fragmento
correcto y aun así decir un número que no está en él. Comprobar en general que lo
dicho **se sigue** de la fuente exige juicio semántico, pero **la clase más
peligrosa es numérica** y esa sí se puede comprobar sin modelo: un umbral de
fiebre, un plazo en días, una dosis.

Si el agente dice «38.5 °C» y ningún fragmento citado contiene esa cifra, la
afirmación no viene de los documentos. No se puede retirar lo ya dicho en voz,
pero queda marcado en la traza y en el registro de la llamada, que es lo que
permite auditarlo después.

Es el mismo principio que el resto del sistema: **el código audita la prosa del
modelo**, no al revés.
"""
from __future__ import annotations

import re

_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")

# Números que aparecen por conversación y no como dato clínico del protocolo.
# Marcarlos generaría ruido constante, y un control que grita en casos inocentes
# deja de mirarse.
_IGNORAR = {"1", "2", "3", "24", "0"}


# Cifras que los pacientes dicen EN LETRAS. Un paciente no dice «tengo 39»:
# dice «tengo treinta y nueve». Si el agente lo devuelve en dígitos —que es lo
# natural al escribir— la auditoría lo marcaba como inventado, porque el dígito
# no aparecía literalmente en lo que dijo el paciente. Era un falso positivo
# constante en el caso más frecuente de todos: reportar fiebre.
#
# Se cubre el rango que de verdad se dice hablando: temperaturas, días de
# postoperatorio y escalas de dolor.
_UNIDADES = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
}
_DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50}
_MEDIO = re.compile(r"\s+y\s+medio\b", re.I)


def _cifras_en_letras(texto: str) -> set[str]:
    """Números escritos con palabras, como dígitos: «treinta y nueve» -> {39}."""
    t = _MEDIO.sub(".5", texto.lower())
    encontrados: set[str] = set()
    for decena, base in _DECENAS.items():
        for m in re.finditer(rf"\b{decena}(?:\s+y\s+(\w+))?", t):
            unidad = _UNIDADES.get(m.group(1) or "", 0)
            encontrados.add(str(base + unidad))
    for palabra, valor in _UNIDADES.items():
        if re.search(rf"\b{palabra}\b", t):
            encontrados.add(str(valor))
    return encontrados


def _normalizar(n: str) -> set[str]:
    """Un mismo valor escrito de varias formas: 38.5 / 38,5 / 38.50."""
    n = n.replace(",", ".")
    formas = {n}
    if "." in n:
        entero, dec = n.split(".", 1)
        dec = dec.rstrip("0")
        formas.add(f"{entero}.{dec}" if dec else entero)
        formas.add(f"{entero},{dec}" if dec else entero)
    return formas


def cifras_sin_respaldo(utterance: str, cites, dicho_por_paciente: str = "") -> list[str]:
    """Cifras dichas por el agente que no aparecen en ninguna fuente válida.

    `dicho_por_paciente` **también cuenta como fuente**: cuando el agente
    responde «¿cómo se ha sentido estos 4 días?» a quien acaba de decir que lo
    operaron hace 4 días, no está inventando una cifra — está devolviendo la que
    le dieron. Sin esto, el control marcaba como no sustentado el eco normal de
    la conversación.
    """
    if not utterance:
        return []
    fuente = " ".join(getattr(c, "text", "") or "" for c in cites)
    fuente = f"{fuente} {dicho_por_paciente}".replace(",", ".")
    if not fuente.strip():
        return []
    # Lo que el paciente dijo en letras cuenta igual que si lo hubiera dicho en
    # dígitos: sigue siendo su cifra, no una del agente.
    dichas_en_letras = _cifras_en_letras(dicho_por_paciente)

    sin_respaldo: list[str] = []
    for bruto in _NUMERO.findall(utterance):
        normal = bruto.replace(",", ".")
        if bruto in _IGNORAR or normal in _IGNORAR:
            continue
        if normal in dichas_en_letras or normal.rstrip(".0") in dichas_en_letras:
            continue
        if not any(f in fuente for f in _normalizar(bruto)):
            sin_respaldo.append(bruto)
    return sorted(set(sin_respaldo))
