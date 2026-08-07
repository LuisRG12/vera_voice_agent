"""La recuperación no cruza de una cirugía a otra.

    uv run python -m evals.pertinencia_procedimiento

**Por qué existe.** El corpus del reto trae 8 pacientes operados de mastectomía
—el 20 % de los casos— y **ni un solo documento de mama**: los 19 archivos de esa
carpeta son de cáncer de cuello uterino. Sin compuerta, «me duele la herida del
seno» recupera material de otra cirugía: misma familia clínica, mismo vocabulario
postoperatorio, y el coseno no distingue. Responder así es alucinación clínica.

Con compuerta, esos pacientes reciben la verdad —no hay documentos de su cirugía,
se avisa al equipo—, que es la conducta correcta y además la que el reto premia.

No invoca ningún modelo: se asevera sobre qué documentos entran al contexto, que
es determinista. Corre con corpus sintético para no depender de tener el material
del reto descargado.
"""
from __future__ import annotations

import sys

from server.knowledge.service import KnowledgeService

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []

# Documentos deliberadamente parecidos entre sí: todos los protocolos
# postoperatorios comparten vocabulario, y es ese parecido el que hace que la
# recuperación cruce. Un corpus de temas dispares mediría un problema más fácil.
CORPUS = [
    ("apendicectomia", "protocolo_apendicectomia.md",
     "# Apendicectomía\n## Signos de alarma\nConsulte de inmediato si presenta fiebre "
     "mayor a 38.5 grados, enrojecimiento creciente de la herida o salida de material "
     "purulento.\n## Herida\nMantenga la incisión limpia y seca."),
    ("colectomia", "protocolo_colectomia.md",
     "# Colectomía\n## Signos de alarma\nConsulte de inmediato si presenta fiebre mayor "
     "a 38.5 grados, distensión abdominal o ausencia de gases.\n## Herida\nMantenga la "
     "incisión limpia y seca."),
    # El material que SÍ existe en la carpeta mal nombrada, indexado por su tema
    # real. Ningún paciente de los cinco procedimientos debe recuperarlo.
    ("cancer_cuello_uterino", "guia_cuello_uterino.pdf",
     "# Cáncer de cuello uterino\n## Cuidados posoperatorios\nDespués de la cirugía "
     "mantenga la herida limpia. Consulte si presenta fiebre o sangrado abundante.\n"
     "## Seguimiento\nControl a las cuatro semanas."),
]

# Documento SIN procedimiento: es lo que sube el evaluador por la consola, y
# tiene que llegarle a cualquier paciente.
GENERAL = ("cuidado_general.md",
           "# Cuidado de la herida\n## Cremas\nNo aplique cremas ni maquillaje sobre la "
           "cicatriz hasta que cierre por completo.")


def check(label: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(bool(ok))


def nombres(citations) -> list[str]:
    return [c.doc_name for c in citations]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    svc = KnowledgeService(db_path=":memory:")
    for proc, nombre, texto in CORPUS:
        svc.add_text(nombre, texto, procedure=proc)
    svc.add_text(*GENERAL)

    print("\n== Se sabe qué procedimientos tienen material ==")
    presentes = svc.procedures_present()
    check("apendicectomía tiene corpus", "apendicectomia" in presentes, str(presentes))
    check("mastectomía NO tiene corpus", "mastectomia" not in presentes, str(presentes))
    check("el material de cuello uterino no se hace pasar por mastectomía",
          "cancer_cuello_uterino" in presentes and "mastectomia" not in presentes,
          str(presentes))

    print("\n== Un paciente solo ve documentos de SU cirugía ==")
    docs = nombres(svc.query("tengo fiebre de 39 grados y la herida roja", k=8,
                             procedimiento="apendicectomia")["citations"])
    check("recupera el protocolo de su procedimiento",
          "protocolo_apendicectomia.md" in docs, str(docs))
    check("NO recupera el de otra cirugía", "protocolo_colectomia.md" not in docs, str(docs))
    check("NO recupera material de cuello uterino",
          "guia_cuello_uterino.pdf" not in docs, str(docs))

    print("\n== Mastectomía: no hay material, y se nota ==")
    docs = nombres(svc.query("me duele la herida del seno y está roja", k=8,
                             procedimiento="mastectomia")["citations"])
    check("NO recupera guías de cuello uterino para una mastectomía",
          "guia_cuello_uterino.pdf" not in docs, str(docs))
    check("NO recupera protocolos de otras cirugías",
          not any(d.startswith("protocolo_") for d in docs), str(docs))

    print("\n== El conocimiento vivo sigue funcionando ==")
    for proc in ("apendicectomia", "mastectomia", None):
        docs = nombres(svc.query("¿me puedo aplicar crema en la cicatriz?", k=8,
                                 procedimiento=proc)["citations"])
        check(f"el documento general le llega a {proc or 'un paciente sin procedimiento'}",
              "cuidado_general.md" in docs, str(docs))

    print("\n== Sin procedimiento conocido no se filtra nada ==")
    docs = nombres(svc.query("fiebre y herida roja", k=8, procedimiento=None)["citations"])
    check("busca en todo el corpus",
          "protocolo_apendicectomia.md" in docs and "protocolo_colectomia.md" in docs, str(docs))

    print("\n== Borrar un documento retira su procedimiento ==")
    doc_id = next(d.id for d in svc.documents() if d.name == "protocolo_colectomia.md")
    svc.delete(doc_id)
    check("tras borrarlo, su procedimiento ya no figura como presente",
          "colectomia" not in svc.procedures_present(), str(svc.procedures_present()))

    svc.close()
    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones pasaron.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
