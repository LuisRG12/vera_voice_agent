# Bitácora

> Qué se encontró, qué se decidió y con qué número. Se escribe a medida que pasa, no al
> final. El plan de partida está en [`arquitectura.md`](arquitectura.md).
>
> Formato: **hallazgo → evidencia → decisión**. La evidencia es lo que importa; una decisión
> sin medición es una opinión.

---

## Etapa 1 · Andamiaje

**Punto de partida.** Un proceso, una dependencia de sistema (Ollama), sin base de datos
externa y sin claves. La decisión de arrancar así no es minimalismo: la solución tiene que
quedar corriendo en 15 minutos siguiendo solo el README, y cada servicio que se añade es un
punto de fallo en la máquina de otro.

**`/api/health` desde el primer día.** No para monitorización, sino porque durante todo el
desarrollo la pregunta «¿está bien lo que tengo montado?» se responde por HTTP y no
levantando la interfaz. Reporta el modelo configurado, si el runtime local responde y
cuántos documentos hay cargados.

**Decisiones abiertas** al cerrar la etapa: las seis de `arquitectura.md`. Ninguna se puede
resolver sin medir, y medir requiere que exista algo que ejecutar.
