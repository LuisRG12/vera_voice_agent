"""D1 — cuál de los modelos permitidos aguanta en una máquina sin GPU.

    uv run python -m evals.spike_modelo          # CPU pura: el escenario que decide
    uv run python -m evals.spike_modelo --gpu    # con GPU, para contrastar

**Por qué se mide en CPU.** El despliegue se cronometra en la máquina de quien
evalúa, y el reto vende explícitamente viabilidad en hardware común. Medir solo
en la máquina de desarrollo y reportar esos números sería publicar métricas que
no se sostienen en la sesión.

**Qué se mide.** Todo lo determinista —reglas de alarma, léxico, turnos— es
idéntico con cualquier modelo. Lo que de verdad los distingue:

1. **El juez de riesgo**, en los casos donde una regla léxica falla y la
   valoración del modelo es la única red. Un falso negativo aquí es la falla
   clínica más grave.
2. **La calidad de lo que se oye**: usted, español, brevedad, sin palabras
   inventadas y —lo que casi se escapa— sin negarse a responder.
3. **TTFA**: milisegundos hasta la primera frase hablable, que es cuando empieza
   a sonar la voz. No el total: se habla por frases mientras el modelo sigue.
"""
from __future__ import annotations

import asyncio
import re
import statistics
import sys
import time

import server.config as config_mod
from server.agent.llm import LLMError, StructuredLLM
from server.agent.schemas import RiskAssessment, grounded_response_for

# Los dos modelos permitidos por el reto que siguen disponibles. Los otros dos de
# la lista son de nube y sus proveedores los retiraron.
MODELOS = ["llama3.2:3b", "phi3.5:3.8b"]
SEVERIDAD = ["none", "low", "moderate", "high", "critical"]

SYSTEM = "Eres Vera, enfermera de seguimiento postoperatorio en Colombia."

REGLAS = """Eres Vera, enfermera de seguimiento postoperatorio en Colombia. Hablas por teléfono; tu texto se convierte en voz.

REGLAS:
- Solo afirmas lo clínico que esté en el CONTEXTO. Si no está, dilo y ofrece avisar a su equipo.
- TRATA DE USTED siempre ("tiene", "su", "avísele"). Nunca tutees.
- ESPAÑOL siempre.
- Máximo 2 frases cortas. Una idea por turno.
- No empieces con "Entiendo", "Claro" ni similares.
- Ante un signo de alarma, con calma, encamina a contactar a su equipo clínico."""

JUEZ = """Evalúas el riesgo clínico de lo que reporta un paciente operado.

- critical: emergencia inmediata (no puede respirar, dolor en el pecho, sangrado masivo, desmayo).
- high: signo de alarma que requiere contacto clínico prioritario (fiebre >= 38.5, sangrado abundante, pus o infección, signos de trombosis).
- moderate: síntoma que amerita vigilancia; escalar si empeora.
- low: molestia esperable del postoperatorio.
- none: sin síntoma de riesgo.

Ante duda, SUBE el nivel. Nunca lo bajes."""

CONTEXTO = (
    "[#1 | protocolo_apendicectomia.md §Signos de alarma] Consulte de inmediato si "
    "presenta fiebre mayor a 38.5 °C, enrojecimiento creciente de la herida o salida "
    "de material purulento.\n"
    "[#2 | plan_casero.md §Dolor] Es normal un dolor leve a moderado los primeros días, "
    "que cede con el analgésico indicado."
)

# Casos donde una regla léxica se equivoca y el juez es la única red.
CASOS_JUEZ = [
    ("no aguanto el dolor en el pecho", "critical"),
    ("ya no puedo más, me falta el aire", "critical"),
    ("no me baja la fiebre", "high"),
    ("no he tomado nada y tengo fiebre de 39 grados", "high"),
    ("ayer no tenía fiebre, pero hoy tengo fiebre de 40", "high"),
    ("estoy botando materia por la herida", "high"),
    ("se me abrió la herida y se ven los puntos por dentro", "high"),
    ("me cuesta respirar", "critical"),
    # Estos NO deben escalar: alarmarse de más también es un defecto.
    ("me duele un poquito la herida pero es aguantable", "low"),
    ("ya estoy alentada, mucho mejor que la semana pasada", "none"),
]

# Habla real colombiana, con el síntoma envuelto en jerga.
CASOS_VOZ = [
    "ando aventao y con una cagalera tenaz desde anoche",
    "la herida está botando una materia con un olor bien maluco",
    "me siento maluco y con calentura",
    "ya estoy alentada, mucho mejor que la semana pasada",
    "¿me puedo bañar hoy o toca esperar?",
    "estoy asustada, no sé si esto es normal",
]

# --- Reglas de calidad comprobables por código ------------------------------
# Se comprueban con reglas y no con otro modelo: juzgar con un LLM sería medir
# con la misma vara torcida.

_TUTEO = re.compile(
    r"\b(?:tu|tus|tienes|puedes|debes|necesitas|sientes|estás|eres|avisa|presentas|"
    r"cu[ií]date|cont[aá]ctate|llama|comun[ií]cate|dime|cuéntame|contigo|ti)\b", re.I)
_INGLES = re.compile(r"\b(?:the|your|you|please|notice|wound|fever|pain|and|with|"
                     r"immediately|contact|doctor|surgery)\b", re.I)
_MULETILLA = re.compile(r"^\s*(?:entiendo|claro|aj[aá]|ya veo|qu[eé] bueno)\b", re.I)
# Negarse no viola ninguna regla de formato, así que sin esto contaba como
# respuesta limpia. Un agente que se niega a hablar de síntomas es inservible.
_NEGATIVA = re.compile(
    r"no puedo (?:cumplir|ayudar|asistir|responder|proporcionar)|lo siento,? pero no|"
    r"as an ai|i (?:can'?t|cannot|am unable)", re.I)
MAX_CARACTERES = 240


def violaciones(texto: str) -> list[str]:
    v = []
    if _NEGATIVA.search(texto):
        v.append("SE NIEGA")
    if _TUTEO.search(texto):
        v.append("tuteo")
    if len(_INGLES.findall(texto)) >= 2:
        v.append("inglés")
    if len(texto) > MAX_CARACTERES:
        v.append("largo")
    if _MULETILLA.match(texto):
        v.append("muletilla")
    if not texto.strip():
        v.append("vacío")
    return v


def ge(sev: str, minimo: str) -> bool:
    return SEVERIDAD.index(sev) >= SEVERIDAD.index(minimo)


async def hablar(llm: StructuredLLM, paciente: str) -> tuple[str, float]:
    """(texto, TTFA ms) — hasta la primera frase hablable."""
    user = (f"{REGLAS}\n\nCONTEXTO:\n{CONTEXTO}\n\n"
            f"OBJETIVO: responder y verificar signos de alarma.\n\nPACIENTE: {paciente}\n")
    t0 = time.perf_counter()
    ttfa = None
    partes: list[str] = []
    async for kind, payload in llm.astructured_stream(
        SYSTEM, user, grounded_response_for((1, 2)), max_tokens=140
    ):
        if kind == "delta":
            partes.append(payload)
            texto = "".join(partes)
            if ttfa is None and len(texto) >= 25 and re.search(r"[.?!]", texto[25:]):
                ttfa = (time.perf_counter() - t0) * 1000
        elif kind == "final":
            obj, _ = payload
            if obj is not None and not partes:
                partes.append(obj.utterance)
    total = (time.perf_counter() - t0) * 1000
    return "".join(partes).strip(), (ttfa if ttfa is not None else total)


async def evaluar(modelo: str) -> dict | None:
    print(f"\n{'='*80}\n  {modelo}\n{'='*80}")
    llm = StructuredLLM(model=modelo)
    try:
        await hablar(llm, "hola, buenos días")  # carga el modelo en memoria
    except LLMError as exc:
        print(f"  [X] no disponible: {exc}")
        return None

    print("\n  Juez de riesgo")
    aciertos = falsos_negativos = 0
    for texto, esperado in CASOS_JUEZ:
        try:
            ra, _ = await asyncio.to_thread(
                llm.structured, JUEZ, f"PACIENTE: {texto}\n\nClasifica el riesgo.",
                RiskAssessment, 300, 1)
        except LLMError as exc:
            print(f"    [X] {texto[:44]}: {str(exc)[:60]}")
            continue
        if esperado in ("critical", "high"):
            ok = ge(ra.risk, esperado)
            falsos_negativos += not ok
        else:
            ok = not ge(ra.risk, "high")
        aciertos += ok
        print(f"    {'ok ' if ok else '!! '}{ra.risk:<9} (esp {esperado:<9}) {texto[:46]}")

    print("\n  Lo que oye el paciente")
    limpios = 0
    ttfas, fallos = [], {}
    for caso in CASOS_VOZ:
        try:
            texto, ttfa = await hablar(llm, caso)
        except LLMError as exc:
            print(f"    [X] {caso[:44]}: {str(exc)[:60]}")
            continue
        v = violaciones(texto)
        for x in v:
            fallos[x] = fallos.get(x, 0) + 1
        limpios += not v
        ttfas.append(ttfa)
        print(f"    {'ok ' if not v else '!! '}[{','.join(v) or 'limpio':<20}] {texto[:92]}")

    return {
        "modelo": modelo, "juez": f"{aciertos}/{len(CASOS_JUEZ)}",
        "falsos_negativos": falsos_negativos,
        "limpios": f"{limpios}/{len(ttfas)}",
        "ttfa": statistics.median(ttfas) if ttfas else 0,
        "fallos": ", ".join(f"{k}×{v}" for k, v in sorted(fallos.items())) or "—",
    }


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    con_gpu = "--gpu" in sys.argv
    config_mod.settings.llm_num_gpu = None if con_gpu else 0
    print(f"\nD1 · {'CON GPU' if con_gpu else 'CPU PURA'} — el escenario que decide es CPU,")
    print("porque es lo que puede tener quien despliegue esto.")

    filas = [f for m in MODELOS if (f := await evaluar(m))]
    if not filas:
        print("\nNingún modelo respondió. ¿Está el runtime local corriendo?")
        return 1

    print(f"\n\n{'='*80}\n  RESUMEN\n{'='*80}")
    print(f"  {'modelo':<16}{'juez':>8}{'falsos neg':>12}{'voz limpia':>12}{'TTFA':>9}   fallos")
    for f in filas:
        print(f"  {f['modelo']:<16}{f['juez']:>8}{f['falsos_negativos']:>12}"
              f"{f['limpios']:>12}{f['ttfa']:>8.0f}m   {f['fallos']}")
    print("\n  'falsos neg' = casos donde había que escalar y el juez no lo hizo.")
    print("  Es la falla más grave; pesa más que la latencia.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
