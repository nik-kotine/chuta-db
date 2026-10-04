# Fase 3: LockManager

## Propósito

La Fase 3 agrega control de concurrencia en memoria. El `LockManager` coordina
qué transacciones pueden usar un recurso al mismo tiempo, sin conocer todavía
`Table`, el executor ni las páginas físicas.

En esta versión el recurso se identifica como un valor hashable, normalmente:

```python
("table", "ventas")
```

## Modos

### `SHARED`

Representa una lectura. Varias transacciones pueden mantener el mismo recurso
en este modo simultáneamente.

### `EXCLUSIVE`

Representa una escritura. Solo una transacción puede poseerlo y no puede haber
lectores de otras transacciones.

El mismo transaction ID puede volver a adquirir su lock. Cada adquisición se
cuenta y cada `release()` libera una adquisición.

## API

### `acquire(resource, transaction_id, mode, timeout=None)`

Adquiere el modo solicitado o espera con `Condition`. Si `timeout` vence lanza
`LockTimeoutError`.

La política de upgrade es automática: una transacción que tiene `SHARED` y
solicita `EXCLUSIVE` espera a que desaparezcan los lectores restantes. Dos
transacciones que intentan hacer upgrade simultáneamente pueden esperar entre
sí, por lo que el timeout evita un bloqueo infinito.

### `release(resource, transaction_id)`

Reduce el contador del propietario. Cuando el contador llega a cero, despierta
a los hilos que esperan el recurso. Liberar un lock que la transacción no posee
lanza `LockError`.

### `release_all(transaction_id)`

Libera todos los recursos retenidos por una transacción. Será usado por
`COMMIT`, `ROLLBACK` y el cierre de sesión en una fase posterior.

### `held_resources(transaction_id)`

Devuelve una copia de los recursos retenidos. Sirve para depuración, tests y
para verificar que no quedan locks huérfanos.

## Compatibilidad

| Lock existente | Solicitud `SHARED` | Solicitud `EXCLUSIVE` |
|---|---:|---:|
| Ninguno | Sí | Sí |
| `SHARED` de otra transacción | Sí | No |
| `EXCLUSIVE` de otra transacción | No | No |
| Propio `SHARED` | Sí | Upgrade si no hay otros lectores |
| Propio `EXCLUSIVE` | Sí | Sí, reentrante |

Los escritores pendientes tienen preferencia sobre lectores nuevos. Esto
reduce starvation de escritores sin implementar todavía una cola formal de
deadlocks.

## Sincronización interna

El manager usa una `Condition` basada en `RLock`:

1. `acquire()` revisa compatibilidad bajo el lock interno.
2. Si no puede entrar, espera en la condición hasta recibir `notify_all()` o
   hasta que venza el timeout.
3. `release()` actualiza ownership y notifica a todos los candidatos.

La condición protege solamente el estado del manager. No protege operaciones de
la base de datos; esas operaciones deberán adquirir el lock antes de entrar a
`Table`.

## Reglas de integración futura

- `SELECT` adquirirá `SHARED`.
- `INSERT` y `DELETE` adquirirán `EXCLUSIVE`.
- Un JOIN adquirirá sus tablas en orden lexicográfico para reducir deadlocks.
- En autocommit, los locks se liberarán al terminar la sentencia.
- En una transacción explícita, se conservarán hasta commit o rollback.
- Un timeout deberá abortar la transacción y liberar sus locks.

## Pruebas y definición de terminado

La fase está terminada cuando se comprueba:

- dos lectores concurrentes;
- escritor bloqueado por lectores;
- dos escritores mutuamente excluyentes;
- reentrancia del mismo propietario;
- upgrade `SHARED -> EXCLUSIVE`;
- timeout sin lock huérfano;
- liberación individual y con `release_all()`;
- errores al liberar un lock ajeno;
- IDs distintos en recursos distintos sin interferencia.

La siguiente fase conectará `TransactionManager`, `LockManager`, sesiones y
executor. Esta fase todavía no altera las operaciones CRUD.