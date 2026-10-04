# Fase 2: TransactionManager conectado al WAL

## Propósito

La Fase 1 implementó el `LogManager`: escribe registros WAL, asigna LSN y
puede hacerlos durables con `force()`. Esta fase agrega el ciclo de vida de
una transacción sin integrar todavía locks ni operaciones físicas de tablas.

El flujo esperado es:

```text
BEGIN -> UPDATE* -> COMMIT
                   o
                 ABORT -> CLR*
```

## Alcance

Incluye:

- IDs monotónicos de transacción.
- Estados `ACTIVE`, `COMMITTED` y `ABORTED`.
- Registros `BEGIN`, `UPDATE`, `COMMIT`, `ABORT` y `CLR`.
- `last_lsn` y `prev_lsn` por transacción.
- Commit durable después de `LogManager.force()`.
- Rollback de cambios en orden inverso.
- Callback para que una futura capa física restaure `before`.
- Reconstrucción del estado lógico al reabrir el WAL.

No incluye todavía locks, `Table`, recuperación física de páginas ni comandos
SQL. El manager coordina estados y log, pero no conoce formatos de páginas.

## API implementada

### `begin() -> Transaction`

Genera un ID nuevo, registra `BEGIN`, fuerza el WAL y devuelve la transacción
activa.

### `log_update(transaction_id, **change) -> int`

Registra una mutación física como `UPDATE`. `change` puede contener
`before`, `after`, `operation`, `file_name`, `page_id` y otros campos aceptados
por `LogManager.append()`.

Este método todavía no modifica páginas. La futura capa de almacenamiento debe
seguir el protocolo WAL:

```text
lsn = tm.log_update(txid, before=old, after=new, ...)
log.force()
escribir_pagina(new)
```

### `commit(transaction_id) -> Transaction`

Agrega `COMMIT`, ejecuta `force()` y solo después cambia el estado a
`COMMITTED`. Si `force()` falla, la transacción no se presenta como confirmada.

### `rollback(transaction_id, undo_handler) -> Transaction`

Agrega `ABORT`, obtiene los `UPDATE` de la transacción en orden inverso, llama
`undo_handler(record)` y genera un `CLR` por cada cambio revertido.

El callback debe restaurar `record.before`; el manager no aplica bytes por sí
mismo porque aún no conoce el archivo ni la página que controla cada tabla.

### `get_active(transaction_id)`

Valida que la transacción exista y todavía pueda recibir modificaciones.

### `close(undo_handler=None)`

Aborta transacciones activas y cierra el WAL de forma durable. Una transacción
con cambios requiere un callback de undo.

## Reglas de estado

| Estado | `UPDATE` | `COMMIT` | `ROLLBACK` |
|---|---:|---:|---:|
| `ACTIVE` | Sí | Sí | Sí |
| `COMMITTED` | No | No | No |
| `ABORTED` | No | No | No |

Reglas adicionales:

1. Un ID no se reutiliza después de reabrir el WAL.
2. Cada modificación pertenece a una transacción activa.
3. `prev_lsn` mantiene la cadena de registros de cada transacción.
4. `COMMIT` durable ocurre antes de que futuras fases liberen locks.
5. Rollback genera CLRs para describir las operaciones inversas.

## Undo y CLRs

Para `U1, U2, U3`, rollback recorre `U3, U2, U1`:

```text
BEGIN, U1, U2, U3
              |
              v
ABORT, undo(U3), CLR3, undo(U2), CLR2, undo(U1), CLR1
```

Cada CLR guarda como `before` la imagen posterior del cambio original y como
`after` la imagen anterior. Esto prepara el formato para que una fase futura
pueda continuar un rollback interrumpido.

## Concurrencia

El manager usa `threading.RLock` para proteger su tabla de estados y la
asignación de IDs. `LogManager` protege el append físico del WAL. Esto evita
que dos hilos mezclen estados o bytes de registros.

Este mecanismo no reemplaza a `LockManager`: todavía no coordina acceso a
tablas ni evita conflictos entre transacciones.

## Pruebas y definición de terminado

La fase se considera terminada cuando los tests verifican:

- `BEGIN` y `COMMIT` durable.
- IDs únicos incluso con varios hilos.
- Rechazo de operaciones sobre transacciones inexistentes o terminales.
- Rollback en orden inverso.
- Un CLR por cada `UPDATE` revertido.
- Rollback sin callback no marca incorrectamente la transacción como abortada.
- Reapertura del WAL recupera IDs y estados.

La siguiente fase implementará `LockManager`. Después se conectarán el
executor, `Table` y el undo físico de páginas.