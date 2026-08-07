"""El conocimiento se puede añadir y olvidar en caliente.

    uv run python -m evals.conocimiento_vivo

Es la comprobación de la que depende una de las compuertas del reto: subir un
documento y que el agente lo use, borrarlo y que lo olvide. Se asevera sobre
**qué documento sustenta la respuesta**, no sobre un booleano: es lo que de
verdad significa que el conocimiento esté vivo, y es verificable contra la
fuente.

No invoca a ningún modelo de lenguaje: recuperación y almacenamiento son
deterministas, así que esta batería corre en segundos y siempre da lo mismo.
"""
from __future__ import annotations

import sys

from server.knowledge.service import KnowledgeService

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []

PROTOCOLO = """# Colecistectomía
## Signos de alarma
Contacte de inmediato a la clínica si presenta fiebre igual o mayor a 38.5 grados,
salida de material purulento por la herida o dolor abdominal que aumenta.
## Alimentación
Reintroduzca las grasas de forma gradual durante las dos primeras semanas.
"""

CUIDADO_PIEL = """# Cuidado de la piel
## Cremas
No aplique cremas ni maquillaje sobre la cicatriz hasta que cierre por completo.
"""


def check(label: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(bool(ok))


def cita(res, sub: str) -> bool:
    return any(sub.lower() in c.doc_name.lower() for c in res["citations"][:3])


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    svc = KnowledgeService(db_path=":memory:")
    svc.add_text("protocolo_colecistectomia.md", PROTOCOLO)

    print("\n== Recupera lo que tiene ==")
    r = svc.query("tengo fiebre de 39 grados y sale pus de la herida")
    check("recupera el protocolo cargado", cita(r, "colecistectomia"),
          str([c.doc_name for c in r["citations"]]))
    check("los signos de alarma son la sección pertinente",
          any("alarma" in (c.section or "").lower() for c in r["citations"][:3]),
          str([c.section for c in r["citations"][:3]]))

    print("\n== Aprende en caliente ==")
    q = "¿puedo aplicarme crema sobre la cicatriz?"
    check("antes de cargarlo, nada de piel lo sustenta", not cita(svc.query(q), "piel"))
    doc_id = svc.add_text("cuidado_piel.md", CUIDADO_PIEL)
    check("tras cargarlo, lo sustenta el documento nuevo", cita(svc.query(q), "piel"),
          str([c.doc_name for c in svc.query(q)["citations"]]))

    print("\n== Olvida al instante ==")
    svc.delete(doc_id)
    check("tras borrarlo, deja de sustentarlo", not cita(svc.query(q), "piel"),
          str([c.doc_name for c in svc.query(q)["citations"]]))
    check("el documento queda como eliminado, no desaparece del registro",
          any(d.id == doc_id and d.status == "deleted" for d in svc.documents()))

    print("\n== Una versión nueva reemplaza a la anterior ==")
    svc.add_text("protocolo_colecistectomia.md", PROTOCOLO + "\n## Baño\nPuede ducharse.\n")
    activos = [d for d in svc.documents(include_deleted=False)
               if d.name == "protocolo_colecistectomia.md"]
    check("queda una sola versión activa", len(activos) == 1, str(activos))
    check("y es la versión 2", activos and activos[0].version == 2, str(activos))
    check("la recuperación ya ve el contenido nuevo",
          cita(svc.query("¿me puedo duchar?"), "colecistectomia"))

    print("\n== Sin documentos no se inventa evidencia ==")
    vacio = KnowledgeService(db_path=":memory:")
    r = vacio.query("tengo fiebre")
    check("sin corpus no hay citas ni evidencia",
          not r["citations"] and not r["has_evidence"], str(r))
    vacio.close()

    svc.close()
    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones pasaron.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
