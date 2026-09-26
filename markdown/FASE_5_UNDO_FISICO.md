# Fase 5: WAL y undo físico de operaciones CRUD

## Propósito

La Fase 4 conectó sesiones y locks, pero `ROLLBACK` todavía no restauraba
filas. La Fase 5 conecta las mutaciones de `Table` con el WAL y proporciona un
undo real para `INSERT` y `DELETE`.

## Estrategia

Se usa un hook opcional en `Table.insert()` y `Table.delete()`:

1. `Table` valida constraints o recupera la fila existente.
2. El hook serializa la operación y fuerza el registro `UPDATE` del WAL.
3. `Table` modifica el archivo e índices.
4. `ROLLBACK` recorre esos registros en orden inverso.
5. El executor aplica:
   - `table_insert`: `delete_by_key(key)`.
   - `table_delete`: `insert(values)`.
6. `TransactionManager` genera un CLR por cada undo.

La reinserción y el borrado pasan nuevamente por `Table`, por lo que los
índices secundarios y clustered se actualizan usando las mismas rutas CRUD.

## Registro de operaciones

El payload JSON del WAL contiene:

- `values`: valores completos de la fila;
- `key`: valor de la primary key;
- `rid`: RID original cuando está disponible.

Para un insert, el payload va en `after`; para un delete, en `before`. El
`rid` se conserva para futuras optimizaciones de restauración exacta, aunque
la primera implementación restaura por clave y mantiene la consistencia
lógica de la tabla.

El hook se ejecuta después de validar constraints y antes de modificar la
página. El executor fuerza el WAL antes de permitir la mutación física.

## Alcance y limitaciones

Implementado:

- rollback de inserts;
- rollback de deletes;
- rollback de varias operaciones en orden inverso;
- actualización de índices mediante las APIs de `Table`;
- abort automático de una transacción con error.

Pendiente:

- logging de bytes de páginas, headers y asignación de páginas;
- rollback de `CREATE TABLE` y `CREATE INDEX`;
- restauración exacta del RID original;
- redo/recovery después de un crash;
- `UPDATE` SQL.

Por eso esta fase garantiza rollback lógico de CRUD, pero la durabilidad ante
crash requiere la Fase 6 de recovery y checkpoints.

## Pruebas

- Insertar y hacer rollback deja la tabla sin la fila.
- Borrar y hacer rollback restaura la fila.
- Varias operaciones se revierten en orden inverso.
- El rollback conserva los índices secundarios.
- Un error después de una mutación aborta y restaura la transacción.
- Commit conserva las filas.
- Los tests CRUD existentes continúan pasando.

## Definición de terminado

- `Table` acepta hooks de mutación sin romper callers existentes.
- El WAL registra antes de cada mutación transaccional.
- `ROLLBACK` aplica undo a inserts y deletes.
- Los CLRs se escriben y fuerzan.
- Tests de atomicidad pasan junto con las fases 1 a 4.

La siguiente fase implementará recovery físico, redo de commits y undo de
transacciones incompletas después de reiniciar.