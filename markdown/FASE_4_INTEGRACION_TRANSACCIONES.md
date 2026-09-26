# Fase 4: integración de sesiones, executor y locks

## Propósito

La Fase 4 conecta los componentes de las fases anteriores:

```text
ExecuteVisitor -> StorageManager
                       ├── TransactionManager -> LogManager
                       └── LockManager
```

El parser ya reconoce `BEGIN TRANSACTION` y `END TRANSACTION`; esta fase
agrega también `ROLLBACK` y hace que las sentencias modifiquen el estado real
de la sesión.

## Sesiones

Cada `ExecuteVisitor` representa una sesión y mantiene:

- `transaction_id`: `None` cuando no hay transacción explícita.
- `session_id`: ID efímero usado para locks de autocommit.
- referencias a los managers compartidos por `StorageManager`.

Los IDs efímeros se separan de los IDs persistidos de transacciones para que
dos sesiones nunca parezcan ser el mismo propietario de un lock.

## Comandos transaccionales

### `BEGIN TRANSACTION`

- Rechaza una segunda transacción activa en la misma sesión.
- Llama a `TransactionManager.begin()`.
- Conserva el ID para las sentencias siguientes.

### `END TRANSACTION`

- Requiere una transacción activa.
- Escribe un `COMMIT` durable mediante `TransactionManager`.
- Libera todos los locks de la transacción.
- Limpia el `transaction_id` de la sesión.

### `ROLLBACK`

- Requiere una transacción activa.
- Llama a `TransactionManager.rollback()`.
- Libera todos los locks.
- Limpia el estado de la sesión.

En esta fase el executor no pasa todavía un `undo_handler` físico, porque las
operaciones de `Table` aún no registran cada página modificada. Por eso el
rollback ya es correcto para el estado transaccional y los locks, pero la
reversión de filas se implementará al instrumentar el almacenamiento.

## Política de locks

### Transacción explícita

- `SELECT` adquiere `SHARED` y conserva el lock hasta `END` o `ROLLBACK`.
- `INSERT` y `DELETE` adquieren `EXCLUSIVE` y conservan el lock hasta el fin.
- Las tablas de un JOIN se adquieren en orden lexicográfico.
- Un error aborta la transacción y libera sus locks.

### Autocommit

Si no existe `BEGIN TRANSACTION`, cada sentencia usa el `session_id` efímero:

1. Adquiere los locks necesarios.
2. Ejecuta la sentencia.
3. Libera los locks en un bloque `finally`.

Esto conserva la compatibilidad con los tests y consultas existentes.

## Manejo de errores

`ExecuteVisitor.ejecutar()` captura excepciones de una sentencia. Si hay una
transacción activa, ejecuta abort y libera todos sus locks antes de propagar el
error. No se deja una sesión activa con ownership huérfano.

## Archivos modificados

- `parser/token_sql.py`: token `ROLLBACK`.
- `parser/scanner.py`: palabra reservada `ROLLBACK`.
- `parser/ast_sql.py`: `TransactionStmt` distingue rollback.
- `parser/parser.py`: gramática `ROLLBACK`.
- `parser/visitor.py`: impresión del nuevo comando.
- `parser/executor.py`: sesión, locks, autocommit y ciclo transaccional.
- `storage/storage_manager.py`: managers compartidos y `wal_path` configurable.

## Pruebas

La fase debe verificar:

- parseo de `ROLLBACK`;
- `BEGIN; END;` escribe y confirma la transacción;
- `BEGIN; ROLLBACK;` deja la sesión sin transacción activa;
- doble `BEGIN`, `END` sin `BEGIN` y `ROLLBACK` sin `BEGIN` fallan;
- un error dentro de una transacción aborta y libera locks;
- autocommit libera locks al terminar la sentencia;
- una transacción explícita conserva ownership hasta `END`;
- el comportamiento CRUD existente sigue funcionando.

## Definición de terminado

- Parser y visitor aceptan `ROLLBACK`.
- `StorageManager` expone managers compartidos.
- El executor usa locks shared/exclusive según la operación.
- Autocommit y transacciones explícitas tienen lifecycles distintos.
- Los errores limpian la sesión y los locks.
- Las pruebas de las fases 1, 2, 3 y 4 pasan.

La siguiente fase registrará mutaciones de `Table`, índices, headers y catálogo
en el WAL y conectará el `undo_handler` para que rollback revierta datos reales.