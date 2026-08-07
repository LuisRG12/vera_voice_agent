# Arquitectura — decisión de partida

> Documento de planeación, escrito **antes** de empezar a construir. Registra qué se va a
> hacer y por qué, y deja marcadas las decisiones que no se pueden tomar sin medir.
> Lo que las mediciones vayan resolviendo se anota en [`bitacora.md`](bitacora.md).

## El problema

Un paciente sale de cirugía y necesita seguimiento en las primeras horas. Hoy eso lo hace
personal humano: caro, no escala, y sujeto a error. El paciente no tiene conocimiento
médico —a veces ni un termómetro— y describe lo que siente en lenguaje cotidiano y
regional: *«me duele como aquí abajito de la axila hace como 20 minutos»*.

Tres cosas lo hacen distinto de un chatbot:

- **Es voz.** Hay presupuesto de latencia, silencios incómodos, y respuestas largas
  inviables.
- **Es salud.** Cero tolerancia a inventar. Y honestidad explícita cuando no se sabe.
- **El conocimiento cambia.** Los protocolos se actualizan; el agente debe reflejar la
  versión vigente sin contaminarse con la anterior.

## La tesis

El modelo de lenguaje debe ser uno de una lista cerrada, y todos son pequeños. La
estrategia **no** es pedirle al modelo que sea inteligente: es rodearlo de una arquitectura
que lo haga fiable.

| Técnica | Qué compra |
|---|---|
| Máquina de estados + slots fuera del modelo | No pierde el hilo; el estado vive en código |
| Un prompt por etapa, no un mega-prompt | Menos instrucciones que desobedecer |
| Salida estructurada validada, con reintento | Fiabilidad de formato |
| Reglas de alarma **deterministas** | La seguridad clínica no depende de que el modelo acierte |
| Grounding con citas obligatorias y umbral | Sin evidencia → «no lo sé», que es la respuesta correcta |
| Verificación posterior de lo generado | El código audita la prosa del modelo |

La consecuencia de diseño más importante: **la decisión de escalar a un humano se toma con
reglas, en paralelo al modelo, y se queda con la más conservadora de las dos.** Si el
proveedor se cae a mitad de turno, el escalamiento sigue ocurriendo.

## Componentes

```
navegador ──audio──► voz (STT) ──texto──► recuperación (embeddings + BM25, local)
                                                    │
                                                    ▼
                                    ┌───────────────────────────────┐
                                    │ máquina de estados + slots    │
                                    │ respuesta fundamentada        │
                                    │ juez de riesgo (modelo)       │ ◄─ en paralelo
                                    │ reglas de alarma              │ ◄─ sin modelo
                                    └───────────────┬───────────────┘
navegador ◄─audio── voz (TTS) ◄──frases────────────┘
                                                    │
                                                    ▼
                              alerta al equipo clínico + registro auditable
```

- **Conocimiento**: ingesta de documentos → troceado por secciones → embeddings locales →
  recuperación híbrida. El borrado es *tombstone* y se relee en cada consulta, así que
  olvidar es instantáneo y no hay índice que reconstruir.
- **Diálogo**: fases clínicas y slots en código. Al modelo se le da poco y bueno.
- **Seguridad**: dos capas independientes; la decisión es el máximo de riesgo.
- **Trazabilidad**: cada afirmación clínica registra de qué documento sale.
- **Gobernanza**: permisos por fase, presupuesto por llamada, interruptor de parada.

## Dos superficies

| Superficie | Contrato funcional |
|---|---|
| Consola de administración | Subir, listar y eliminar documentos; ver que quedó procesado |
| Interfaz de llamada | Iniciar la llamada, hablar por micrófono, escuchar al agente |

El diseño visual no se evalúa; el contrato sí.

## Principios que se van a seguir

No como decoración, sino donde se ganen el sitio:

- **Una responsabilidad por módulo.** El store persiste, el retriever ordena, el chunker
  trocea. Si una prueba necesita levantar tres cosas para probar una, el corte está mal.
- **Depender de abstracciones.** Los proveedores de voz y el cliente del modelo van detrás
  de un `Protocol`, para poder cambiarlos —y medirlos comparativamente— sin tocar el
  diálogo.
- **Abierto a extensión.** Añadir un procedimiento clínico o un formato de documento no
  debería obligar a editar el diálogo.
- **Lo determinista se prueba sin modelo.** Todo lo que se pueda comprobar con reglas se
  aísla del LLM: es lo que permite tener una batería grande, rápida y reproducible.

## Decisiones que NO se toman aquí

Estas dependen de medir, y medirlas es parte del trabajo. Cada una se resuelve en su etapa
y queda anotada en la bitácora con el número que la respalda.

| # | Decisión abierta | Cómo se decide |
|---|---|---|
| D1 | Cuál de los modelos permitidos | Comparar en el hardware que puede tener el evaluador |
| D2 | Modelo de embeddings | Medir *separación* entre preguntas con respuesta y sin ella, no similitud media |
| D3 | Umbral de evidencia | Calibrar con preguntas de respuesta conocida y preguntas fuera de corpus |
| D4 | Reconocimiento y síntesis de voz | Latencia y naturalidad en español colombiano |
| D5 | Tamaño y solape del troceado | Precisión de recuperación sobre el corpus real |
| D6 | Cuánto contexto por turno | Coste en latencia frente a lo que aporta |

## Restricciones de entrega

- El modelo debe ser uno de la lista cerrada del reto. Usar otro descalifica.
- La solución debe quedar corriendo en **15 minutos** siguiendo solo el README.
- El agente conversa en **español**, con pacientes colombianos.
- Repositorio público, dependencias fijadas, sin credenciales en el árbol.
