# Fase 6: recovery y checkpoints

## Propósito

La Fase 5 ya puede deshacer inserts y deletes mientras la sesión sigue viva.
La Fase 6 aplica ese mismo undo cuando el motor vuelve a abrirse después de un
cierre inesperado.

## Flujo de arranque

`StorageManager` ahora crea los managers, abre el catálogo y construye un
`RecoveryManager`. Este recorre las transacciones activas del WAL:

```text
abrir WAL -> reconstruir estados -> abrir catálogo
          -> undo de transacciones sin COMMIT
          -> aceptar nuevas sesiones
```

Las transacciones con `COMMIT` no se modifican. Sus operaciones se consideran
confirmadas y permanecen en las tablas.

## Undo durante recovery

El recovery usa el mismo payload CRUD de la Fase 5:

- `table_insert`: elimina por primary key;
- `table_delete`: reinserta los valores originales.

Las llamadas se hacen sin `mutation_logger`, para no generar una segunda
operación CRUD mientras se aplica undo. `TransactionManager` registra `ABORT`
y `CLR` y fuerza el WAL después de completar la restauración.

## Checkpoints

Al cerrar correctamente el motor se escribe un registro `CHECKPOINT` durable.
El checkpoint no altera el estado de una transacción y se ignora al reconstruir
los estados de `TransactionManager`. El WAL todavía se conserva; truncarlo de
forma segura requiere page LSN y un recovery físico completo.

## Alcance y límites

Implementado:

- undo automático de transacciones CRUD incompletas;
- conservación de commits tras reabrir;
- registro durable de checkpoint;
- tests de reinicio.

Pendiente:

- redo físico de páginas confirmadas;
- page LSN y flush coordinado del buffer pool;
- recovery de headers, índices y catálogo modificados a bajo nivel;
- truncado seguro del WAL.

## Definición de terminado

- Un insert sin commit desaparece al reabrir.
- Un insert confirmado permanece al reabrir.
- Recovery es repetible sin duplicar filas.
- Checkpoint se escribe al cierre y no se interpreta como transacción activa.
- Los tests de las fases anteriores continúan pasando.