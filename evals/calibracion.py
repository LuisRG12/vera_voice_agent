"""D3 — ¿cuándo debe el agente decir «no tengo esa información»?

    uv run python -m evals.calibracion            # informe con el umbral actual
    uv run python -m evals.calibracion --barrido  # cómo se mueven los dos errores

Requiere el índice del corpus construido (`scripts/corpus.py`). No invoca al
modelo: mide la recuperación y la decisión de evidencia, que son deterministas.

**Por qué no basta con elegir un número a ojo.** Hay dos errores y tiran en
sentidos opuestos:

- **Rechazo falso**: la respuesta estaba en el corpus y el agente se abstuvo.
  Cuesta puntos de RAG y frustra al paciente.
- **Afirmación falsa**: el agente respondió sin respaldo. Es el error caro.

La asimetría clínica manda —ante duda, abstenerse—, pero abstenerse siempre no es
prudencia: es no haber construido el RAG.
"""
from __future__ import annotations

import sys
from pathlib import Path

from server.config import settings
from server.knowledge.service import KnowledgeService, _content_words

PASS, FAIL = "  [OK]", "  [FALLA]"

# (procedimiento, pregunta del paciente, fragmento esperado en el nombre del doc)
# En lenguaje de paciente colombiano, no en terminología clínica: es lo que de
# verdad va a llegar por el micrófono.
RESPONDIBLES = [
    ("apendicectomia", "tengo fiebre de treinta y nueve y la herida me bota un líquido amarillo", "pendic"),
    ("apendicectomia", "¿cuándo me puedo bañar después de que me sacaron el apéndice?", "pendic"),
    ("apendicectomia", "me duele mucho la barriga y está dura", "pendic"),
    ("colecistectomia", "me operaron de la vesícula y me duele el hombro derecho", "olecist"),
    ("colecistectomia", "¿puedo comer grasa después de que me sacaron la vesícula?", "olecist"),
    ("colecistectomia", "se me puso la piel amarilla y los ojos amarillos", "olecist"),
    ("colectomia", "no he podido hacer del cuerpo desde la operación del colon", "colo"),
    ("colectomia", "¿cómo cuido la bolsa de la colostomía?", "colo"),
    ("colectomia", "tengo la barriga hinchada y no boto gases", "colo"),
    ("reemplazo_articular", "¿puedo caminar hasta la tienda de la esquina con la rodilla nueva?", "odilla"),
    ("reemplazo_articular", "¿cuánto tiempo me pongo el hielo en la rodilla?", "odilla"),
    ("reemplazo_articular", "tengo la pantorrilla hinchada y caliente", "eemplazo"),
]

# Preguntas que NINGÚN documento del corpus responde. Aquí lo correcto es
# declarar el límite y ofrecer escalar.
FUERA_DE_CORPUS = [
    ("apendicectomia", "¿me puedo tomar una cerveza con el remedio?"),
    ("apendicectomia", "¿cuánto cuesta la consulta de control?"),
    ("colecistectomia", "¿mi EPS me cubre el transporte a la cita?"),
    ("colecistectomia", "¿qué carro me recomienda comprar?"),
    ("colectomia", "¿me puedo hacer un tatuaje ahora?"),
    ("reemplazo_articular", "¿puedo viajar en avión a Estados Unidos la próxima semana?"),
    ("reemplazo_articular", "¿cuál es la clave del wifi del hospital?"),
    # Mastectomía: hay pacientes, no hay corpus. Debe abstenerse SIEMPRE, y no
    # por umbral sino por la compuerta de procedimiento.
    ("mastectomia", "me duele la herida del seno y está roja"),
    ("mastectomia", "¿cuándo me quitan el drenaje del pecho?"),
]


def evidencia(svc: KnowledgeService, pregunta: str, proc: str) -> dict:
    """Los ingredientes de la decisión, sin aplicar todavía el umbral."""
    q = svc.query(pregunta, k=5, procedimiento=proc)
    qw = _content_words(pregunta)
    lexico = max((len(qw & _content_words(c.text)) for c in q["citations"][:3]), default=0)
    return {"max_dense": q["max_dense"], "lexico": lexico,
            "docs": [c.doc_name for c in q["citations"]], "sin_citas": not q["citations"]}


def decide(ev: dict, umbral: float, min_lexico: int) -> bool:
    return ev["max_dense"] >= umbral or ev["lexico"] >= min_lexico


def barrido(svc: KnowledgeService) -> None:
    """Cómo se mueven los dos errores al mover el umbral. Justifica el número
    elegido en vez de afirmarlo."""
    ok = [evidencia(svc, q, p) for p, q, _ in RESPONDIBLES]
    no = [evidencia(svc, q, p) for p, q in FUERA_DE_CORPUS]

    print(f"\n  {'umbral':>7} {'léxico':>7} | {'responde bien':>14} {'se abstiene bien':>17} "
          f"| {'rechazos falsos':>16} {'afirmaciones falsas':>20}")
    print("  " + "-" * 96)
    for min_lex in (2, 3):
        for umbral in (0.80, 0.81, 0.82, 0.83, 0.84, 0.85, 0.86):
            bien_si = sum(1 for e in ok if decide(e, umbral, min_lex))
            bien_no = sum(1 for e in no if not decide(e, umbral, min_lex))
            print(f"  {umbral:>7.2f} {min_lex:>7} | {bien_si:>10}/{len(ok)} "
                  f"{bien_no:>13}/{len(no)} | {len(ok)-bien_si:>16} {len(no)-bien_no:>20}")


def informe(svc: KnowledgeService) -> int:
    resultados: list[bool] = []

    print("\n== Preguntas CON respuesta en el corpus ==")
    print("   (abstenerse aquí es un rechazo falso: cuesta puntos de RAG)\n")
    for proc, pregunta, esperado in RESPONDIBLES:
        ev = evidencia(svc, pregunta, proc)
        recupero = any(esperado.lower() in d.lower() for d in ev["docs"])
        responde = decide(ev, svc.min_evidence, settings.min_lexico)
        ok = recupero and responde
        resultados.append(ok)
        print(f"{PASS if ok else FAIL} [{proc[:12]:<12}] {pregunta[:54]}")
        print(f"          dense={ev['max_dense']:.3f} léxico={ev['lexico']} "
              f"recuperó={'sí' if recupero else 'NO'} responde={'sí' if responde else 'NO'}")

    print("\n== Preguntas SIN respuesta en el corpus ==")
    print("   (responder aquí es una afirmación sin respaldo: el error caro)\n")
    for proc, pregunta in FUERA_DE_CORPUS:
        ev = evidencia(svc, pregunta, proc)
        responde = decide(ev, svc.min_evidence, settings.min_lexico)
        resultados.append(not responde)
        print(f"{PASS if not responde else FAIL} [{proc[:12]:<12}] {pregunta[:54]}")
        if responde:
            print(f"          dense={ev['max_dense']:.3f} léxico={ev['lexico']} "
                  f"-> respondería citando {ev['docs'][:2]}")

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones "
          f"(umbral {svc.min_evidence}, modelo {svc.embedder.name.split('/')[-1]}).")
    return 0 if ok == len(resultados) else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    corpus = Path(__file__).resolve().parents[1] / "corpus_reto.db"
    if not corpus.exists():
        print("[X] Falta corpus_reto.db. Constrúyalo con:")
        print("    uv run scripts/corpus.py --ruta <repo-del-reto>/dataset/textos")
        return 1

    svc = KnowledgeService(db_path=":memory:", plantilla=str(corpus))
    try:
        if "--barrido" in sys.argv:
            barrido(svc)
            return 0
        return informe(svc)
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
