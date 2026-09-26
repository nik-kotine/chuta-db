# Plan de integración de transacciones y concurrencia

## 1. Estado actual

El repositorio ya tiene el primer tramo del soporte SQL:

- `Scanner`, `Parser`, AST y `Visitor` reconocen `BEGIN TRANSACTION` y `END TRANSACTION`.
- `ExecuteVisitor.visit_transaction_stmt()` solo devuelve un mensaje; no crea ni cierra una transacción.
- `Table` expone `insert()`, `delete()`, `get()` y `scan()`, pero no recibe un contexto transaccional ni adquiere locks.
- `StorageManager` administra tablas y catálogo, pero no coordina transacciones ni concurrencia.
- No existen todavía `TransactionManager`, `LockManager` ni pruebas de ejecución concurrente.
- El README declara explícitamente que `BEGIN`/`END` solo se parsean y que no hay locks.

Por lo tanto, el trabajo pendiente se concentra en conectar el front-end ya existente con el almacenamiento y en demostrar el comportamiento con varios hilos.

## 2. Objetivo y alcance

Implementar una primera versión educativa de transacciones con atomicidad, consistencia, aislamiento y durabilidad (ACID) en el alcance soportado, con estas garantías:

1. Cada `ExecuteVisitor` representa una sesión y puede mantener una transacción activa.
2. `BEGIN TRANSACTION` inicia una transacción; un segundo `BEGIN` falla.
3. `END TRANSACTION` confirma la transacción y libera sus locks; un `END` sin transacción falla.
4. Las operaciones de escritura adquieren locks exclusivos antes de modificar una tabla o registro.
5. Las lecturas adquieren locks compartidos, o la política elegida explícitamente, y son compatibles entre sí.
6. Los locks se liberan siempre al confirmar, abortar o cerrar la sesión.
7. La demostración con hilos muestra competencia por un recurso y un resultado serializable o bloqueado correctamente.
8. Un `ROLLBACK` explícito o un abort implícito deshace todas las modificaciones de la transacción.
9. Un commit solo se confirma después de forzar el log a disco y el recovery puede resolver transacciones incompletas tras un reinicio.

### Decisión recomendada para la primera iteración

Usar locks a nivel de tabla (`SHARED`/`EXCLUSIVE`) y mantenerlos hasta `END TRANSACTION` (two-phase locking estricto). Es más simple de integrar con el CRUD actual y suficiente para demostrar exclusión mutua y carreras. Los locks por RID pueden quedar como extensión, salvo que la rúbrica exija que dos filas de la misma tabla puedan modificarse simultáneamente.

Usar WAL (Write-Ahead Logging) con registros de undo y redo. El log debe persistirse antes de escribir páginas de datos modificadas y debe incluir un `COMMIT` durable. Así, `ROLLBACK` puede revertir cambios y el recovery puede rehacer transacciones confirmadas y deshacer transacciones incompletas.

## 3. Orden de implementación

### Fase 0: fijar contratos y preparar errores

- Crear excepciones específicas, por ejemplo `TransactionError`, `LockError`, `DeadlockError` y `TransactionStateError`.
- Definir el identificador de transacción y su ciclo de vida: `ACTIVE`, `COMMITTED`, `ABORTED`.
- Definir una política de espera: bloqueo con `threading.Condition` y timeout configurable; al vencer, abortar la operación o la transacción con un error claro.
- Definir si una sentencia fuera de una transacción usa autocommit. Recomendación: permitir autocommit para conservar compatibilidad con los tests actuales, adquiriendo y liberando locks alrededor de cada sentencia.

**Criterio de aceptación:** los contratos y errores están documentados y una operación inválida produce un error determinista.

### Fase 1: diseñar el formato y protocolo del log

Crear `storage/log_manager.py` antes de conectar las operaciones. El log debe ser append-only, con registros autocontenidos y checksum o longitud validable para detectar un registro truncado.

Cada registro debe incluir como mínimo:

- `lsn` monotónico y `prev_lsn` por transacción.
- `transaction_id` y tipo: `BEGIN`, `UPDATE/INSERT/DELETE`, `COMMIT`, `ABORT`, `CLR` y `CHECKPOINT`.
- Recurso afectado: archivo, página, slot o header, según corresponda.
- Imagen anterior (`before`) y posterior (`after`) serializadas, o suficiente información para aplicar undo/redo.
- `page_lsn` para impedir que una página se escriba sin que su log previo sea durable.

Protocolo mínimo:

1. Escribir el registro de modificación y hacer `flush` del log antes de marcar/escribir la página de datos (WAL).
2. Para commit, escribir `COMMIT`, hacer `flush` durable del log y recién entonces liberar locks y responder éxito.
3. Para rollback, recorrer los registros de la transacción en orden inverso, aplicar undo y escribir `CLR` para que un rollback interrumpido pueda recuperarse.
4. Al reiniciar, escanear el log desde el último checkpoint: rehacer operaciones de transacciones confirmadas y deshacer operaciones de transacciones activas o abortadas.

**Criterio de aceptación:** se puede abrir el log, validar sus registros, recuperar el último LSN y reproducir una modificación en una copia de prueba sin tocar todavía el CRUD.

### Fase 2: implementar `TransactionManager`

Evolucionar el scaffold existente `storage/transaction_manager.py` con responsabilidades acotadas:

- `begin()` para registrar una transacción activa.
- `commit(transaction_id)` para cambiar su estado y pedir la liberación de locks.
- `abort(transaction_id)` para ejecutar rollback y cerrar la transacción como abortada.
- `rollback(transaction_id)` para aplicar undo en orden inverso y generar CLRs.
- `get_active(transaction_id)`/validación de estado.
- Registro de locks retenidos y limpieza idempotente al finalizar.
- Registro del `last_lsn` de cada transacción y asociación con `LogManager`.
- Protección de sus estructuras internas con `threading.Lock`.

Integrarlo en `StorageManager` como componente compartido por todas las sesiones que usan el mismo motor.

**Criterio de aceptación:** tests unitarios cubren begin duplicado, commit, rollback, abort por error, IDs distintos y liberación repetida sin corrupción de estado.

### Fase 3: implementar `LockManager`

Crear `storage/lock_manager.py` con:

- Recurso identificable, inicialmente `("table", table_name)`.
- Modos `SHARED` y `EXCLUSIVE`.
- Compatibilidad entre lectores; exclusión entre escritor y cualquier otro lock.
- Reentrancia para que una transacción que ya posee un lock pueda reutilizarlo.
- Upgrade `SHARED -> EXCLUSIVE` o rechazo explícito; elegir una sola política y probarla.
- Cola/espera mediante `Condition`, timeout y limpieza al liberar.
- Detección básica de deadlock por timeout; no introducir un detector de grafos hasta que sea necesario.

El manager debe ser dueño de la sincronización; `Table` no debe implementar otra política paralela.

**Criterio de aceptación:** dos lectores pueden coexistir, un escritor espera a los lectores, dos escritores no entran simultáneamente y el timeout libera correctamente la espera.

### Fase 4: integrar sesión, executor y `StorageManager`

Modificar `ExecuteVisitor` para recibir o crear un contexto de sesión que contenga:

- `transaction_id` opcional.
- Referencia al `TransactionManager` y `LockManager` compartidos.
- Referencia al `LogManager` compartido.
- Política de autocommit.
- Limpieza en caso de excepción.

Cambiar `visit_transaction_stmt()` para invocar begin/commit en lugar de solo generar texto y agregar `ROLLBACK` al parser/AST/visitor. En `ejecutar()`, si una sentencia falla dentro de una transacción, ejecutar rollback, liberar locks y propagar el error. No se debe confirmar parcialmente una lista de sentencias.

Agregar a `StorageManager` métodos de fachada para crear sesiones y cerrar transacciones antes de cerrar tablas. Mantener compatibilidad con `ExecuteVisitor(sm)` mientras se migra el código de tests.

**Criterio de aceptación:** `BEGIN; INSERT; END;` confirma de forma durable; `BEGIN; INSERT; ROLLBACK;` deja la base igual que antes; `BEGIN; BEGIN;` y `END;`/`ROLLBACK;` sin transacción fallan.

### Fase 5: integrar log, undo y locks en `Table` y executor

Elegir un único punto de adquisición para evitar dobles locks:

- `SELECT`: lock `SHARED` sobre cada tabla leída durante toda la sentencia o transacción, según la política adoptada.
- `INSERT` y `DELETE`: lock `EXCLUSIVE` antes de validar y modificar la tabla.
- `CREATE TABLE`, `CREATE INDEX` y operaciones de catálogo: lock exclusivo sobre el recurso de catálogo y sobre la tabla afectada.
- JOIN: adquirir locks en orden determinista por nombre de tabla para reducir deadlocks.
- En autocommit, liberar los locks al terminar cada sentencia; en una transacción explícita, conservarlos hasta `END`.

Cada mutación debe producir un registro de log antes de modificar el buffer pool. El registro debe cubrir:

- `HeapFile.insert/delete`: slot, página, directorio de espacio libre y creación de páginas nuevas.
- `SequentialFile.insert/delete`: registro, links `next_rid`, cabecera, contadores, overflow y reorganización.
- Índices secundarios, B+ y hash: entradas agregadas/eliminadas y páginas modificadas.
- Catálogo: altas/bajas de tablas e índices y sus páginas físicas.

Preferir pasar un contexto de ejecución a `Table` o a métodos privados de operación, en lugar de usar variables globales. Asegurar que cualquier excepción ejecute undo de la operación actual, y que el commit fuerce primero el log (`LogManager.flush`) y luego las páginas mediante el `BufferManager`. Añadir `page_lsn` o una estructura equivalente para que una página dirty no pueda llegar a disco antes que su log.

**Criterio de aceptación:** una escritura concurrente no interleavea su sección crítica con otra escritura incompatible; rollback restaura datos, headers, índices y catálogo; y el comportamiento existente de CRUD permanece intacto fuera de transacciones.

### Fase 6: recovery y checkpoints

Crear `storage/recovery_manager.py` y conectarlo al arranque de `StorageManager`:

- Detectar un log existente con transacciones sin `COMMIT`.
- Ejecutar análisis para identificar transacciones activas y páginas afectadas.
- Rehacer cambios confirmados que no hayan llegado a las páginas.
- Deshacer cambios de transacciones incompletas usando undo/CLR.
- Escribir un checkpoint consistente después de abrir o cerrar correctamente el motor.
- Hacer `fsync` del log y de los archivos de datos en el orden correcto.

El recovery debe ser idempotente: ejecutarlo dos veces no puede duplicar registros ni corromper índices.

**Criterio de aceptación:** se simula un crash después de una escritura, antes del commit y después del commit; al reabrir, solo permanecen las transacciones confirmadas y los índices coinciden con las tablas.

### Fase 7: pruebas de transacciones, recovery y concurrencia

Crear tests enfocados, idealmente en `tests/table/test_transactions.py` y `tests/table/test_concurrency.py`:

- Parser: BEGIN/END válidos y errores sintácticos existentes.
- Ciclo de vida: begin, commit, abort implícito por error y cierre de sesión.
- Rollback de un insert, delete y operación que actualice varias estructuras.
- Atomicidad de una transacción con varias sentencias: todas las modificaciones aparecen o ninguna.
- Durabilidad: los datos confirmados sobreviven al cierre/reapertura.
- Recovery después de truncar el log, perder páginas dirty o interrumpir en puntos controlados.
- Idempotencia del recovery y consistencia de índices/catálogo.
- Aislamiento: una escritura mantiene el lock hasta `END`.
- Compatibilidad de locks: lectores concurrentes, escritor bloqueado, timeout y liberación.
- Carrera reproducible con `threading.Barrier` y `threading.Event`, evitando `sleep` como sincronización principal.
- Dos transacciones que intentan modificar el mismo recurso; comprobar que no se pierde una actualización y que el resultado final es válido.
- Excepciones en medio de una operación; verificar que no quedan locks huérfanos.
- Regresión: ejecutar todos los tests actuales.

Los tests deben limpiar archivos `.dat` y cualquier estado del catálogo para conservar el aislamiento entre procesos.

### Fase 8: demo multi-hilo

Crear un script ejecutable, por ejemplo `demo/concurrency_demo.py` o `tests/interfaces/concurrency_demo.py`, que:

1. Cree una tabla y datos iniciales.
2. Lance al menos dos hilos con transacciones independientes.
3. Fuerce una contención reproducible sobre la misma tabla/recurso mediante eventos.
4. Imprima inicio, espera por lock, adquisición, operación y commit/abort con el ID de transacción.
5. Muestre el resultado final y explique por qué la carrera fue controlada.

La salida debe distinguir claramente ejecución simultánea de exclusión mutua. No depender de pausas arbitrarias para demostrar el resultado.

### Fase 9: documentación y entrega

Actualizar `README.md` y, si corresponde, `markdown/README.md` para:

- Describir BEGIN/END, autocommit, nivel de lock, aislamiento y timeout.
- Describir `ROLLBACK`, WAL, undo/redo, recovery y el momento en que commit se vuelve durable.
- Añadir un ejemplo SQL de transacción.
- Añadir un ejemplo de rollback y una demostración de recuperación tras reinicio.
- Añadir el comando para ejecutar tests y demo.
- Actualizar la sección de funcionalidades pendientes.

Relacionar la implementación con los issues `#22` a `#28` y cerrar cada uno solo cuando su criterio de aceptación esté cubierto.

## 4. Dependencias entre issues

| Issue | Entregable | Dependencias |
|---|---|---|
| #22 | `TransactionManager` + integración con `LogManager` | Ninguna; define ciclo de vida y recuperación lógica |
| #24 | `LockManager` | Contrato de transacción recomendado |
| #23 | Integración de BEGIN/END/ROLLBACK | Parser existente; requiere #22 |
| #28 | WAL, undo/redo, recovery y locks en `Table`/executor | Requiere #22, #24 y #23 |
| #25 | Tests de atomicidad, durabilidad, recovery y concurrencia | Requiere la integración de #28 |
| #26 | Demo multi-hilo con commit/rollback | Requiere locks funcionales y tests básicos |
| #27 | Documentación README | Puede redactarse al final con el comportamiento verificado |

Orden sugerido: **#22 -> #24 -> #23 -> #28 -> #25 -> #26 -> #27**. Dentro de #22/#28, el log y su protocolo WAL deben estar listos antes de modificar las operaciones físicas.

## 5. Checklist de definición de terminado

- [ ] Existe un único `TransactionManager` compartido por el motor.
- [ ] Existe un único `LockManager` compartido por el motor.
- [ ] BEGIN/END cambian estado real y validan estados inválidos.
- [ ] ROLLBACK revierte todas las modificaciones de la transacción, incluidos índices, headers y catálogo.
- [ ] El WAL se fuerza a disco antes de escribir páginas modificadas.
- [ ] COMMIT durable deja una transacción recuperable tras reinicio.
- [ ] Recovery rehace commits incompletos y deshace transacciones sin commit de forma idempotente.
- [ ] Los locks tienen una política explícita de compatibilidad, espera, timeout y liberación.
- [ ] Las operaciones del executor adquieren el lock correcto antes de tocar `Table`.
- [ ] Las excepciones no dejan locks retenidos.
- [ ] Hay tests deterministas para carreras y contención.
- [ ] La demo muestra varios hilos, una carrera y su resolución.
- [ ] El README describe las garantías reales y las limitaciones restantes de rollback/recovery.
- [ ] `python run_all_tests.py` pasa completo.

## 6. Riesgos y decisiones abiertas

- **Rollback real:** cada mutación debe tener una imagen anterior suficiente para revertir también efectos secundarios en índices, headers y catálogo.
- **Granularidad:** locks de tabla simplifican la integración, pero reducen el paralelismo. Migrar a locks por RID requiere coordinar índices, `scan()` y borrados.
- **Lecturas:** conservar locks compartidos hasta commit ofrece una demostración más clara de aislamiento, pero puede bloquear más de lo necesario.
- **Deadlocks:** el orden determinista de locks en JOIN y un timeout son suficientes para la primera versión; un detector formal puede ser una mejora posterior.
- **Persistencia:** los locks viven en memoria y no deben escribirse en el catálogo ni sobrevivir al proceso; el WAL sí debe sobrevivir hasta que un checkpoint permita truncarlo de forma segura.