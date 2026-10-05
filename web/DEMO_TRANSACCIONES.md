# Guion de demo: transacciones y concurrencia

Este guion usa la consola web y sus sesiones persistentes. Mantén abierta la vista `Consultas` para ejecutar SQL y cambia a `Transacciones` para observar el estado, los locks, el WAL y el recovery.

## 0. Preparación

Ejecuta estas sentencias una por una desde una sesión limpia:

```sql
CREATE TABLE demo_ventas (
    id INT PRIMARY KEY,
    cliente VARCHAR(30),
    monto FLOAT
) USING HEAP;
```

```sql
INSERT INTO demo_ventas VALUES (1, 'Ana', 100.0);
INSERT INTO demo_ventas VALUES (2, 'Beto', 250.0);
INSERT INTO demo_ventas VALUES (3, 'Clara', 80.0);
```

Consulta de comprobación:

```sql
SELECT * FROM demo_ventas ORDER BY id ASC;
```

Debe aparecer una tabla con tres filas y el plan de ejecución. En el catálogo debería aparecer `demo_ventas` con su clave primaria.

## 1. Commit normal

Ejecuta:

```sql
BEGIN TRANSACTION;
```

```sql
INSERT INTO demo_ventas VALUES (4, 'Diego', 180.0);
```

```sql
END TRANSACTION;
```

En la vista `Transacciones` debería verse la secuencia:

```text
BEGIN -> EXCLUSIVE -> UPDATE -> COMMIT
```

El `COMMIT` debe aparecer como durable y la transacción debe dejar de estar activa después de liberar sus locks.

Comprueba el resultado:

```sql
SELECT * FROM demo_ventas WHERE id = 4;
```

## 2. Rollback de un INSERT

Ejecuta:

```sql
BEGIN TRANSACTION;
INSERT INTO demo_ventas VALUES (5, 'Eva', 320.0);
```

Antes de hacer rollback, cambia a `Transacciones` y observa el estado `ACTIVE`, el lock `EXCLUSIVE` y los registros WAL.

Luego ejecuta:

```sql
ROLLBACK;
```

La timeline debería mostrar:

```text
BEGIN -> EXCLUSIVE -> UPDATE -> ABORT -> CLR
```

Comprueba que la fila no exista:

```sql
SELECT * FROM demo_ventas WHERE id = 5;
```

El resultado esperado es cero filas.

## 3. Rollback de un DELETE

Ejecuta:

```sql
BEGIN TRANSACTION;
```

```sql
DELETE FROM demo_ventas WHERE id = 2;
```

```sql
ROLLBACK;
```

Comprueba que la fila haya sido restaurada:

```sql
SELECT * FROM demo_ventas WHERE id = 2;
```

En el WAL se puede explicar que el `DELETE` conserva la imagen anterior y que el undo reinserta los valores originales mediante la API de `Table`.

## 4. Rollback de un UPDATE

Ejecuta:

```sql
BEGIN TRANSACTION;
```

```sql
UPDATE demo_ventas SET monto = 999.0 WHERE id = 1;
```

Antes del rollback, consulta:

```sql
SELECT * FROM demo_ventas WHERE id = 1;
```

Luego ejecuta:

```sql
ROLLBACK;
```

Vuelve a consultar:

```sql
SELECT * FROM demo_ventas WHERE id = 1;
```

El monto debe regresar a `100.0`. En la vista de transacciones deberían aparecer `UREAD`, promoción a `EXCLUSIVE`, `UPDATE`, `ABORT` y `CLR`.

## 5. UPDATE de varias filas

Esta consulta sirve para demostrar que el mensaje de filas afectadas no debe decir siempre `1 fila`:

```sql
BEGIN TRANSACTION;
```

```sql
UPDATE demo_ventas SET monto = 50.0 WHERE monto < 200.0;
```

```sql
ROLLBACK;
```

El mensaje debería indicar la cantidad real de filas actualizadas. Después del rollback, los montos deben conservar sus valores originales.

## 6. Commit de UPDATE y consistencia del índice

Primero crea un índice secundario:

```sql
CREATE INDEX ON demo_ventas (cliente) USING BTREE;
```

Luego ejecuta:

```sql
BEGIN TRANSACTION;
```

```sql
UPDATE demo_ventas SET cliente = 'Ana Actualizada' WHERE id = 1;
```

```sql
END TRANSACTION;
```

Comprueba la búsqueda por la columna indexada:

```sql
SELECT * FROM demo_ventas WHERE cliente = 'Ana Actualizada';
```

En el plan debería poder observarse el uso del índice si el planificador lo selecciona.

## 7. UREAD y promoción a EXCLUSIVE

Este escenario se entiende mejor usando dos ventanas o dos perfiles de navegador, porque cada uno tiene un `X-Session-ID` distinto.

### Sesión A

```sql
BEGIN TRANSACTION;
```

```sql
SELECT * FROM demo_ventas WHERE id = 1;
```

La sesión A conserva un lock `SHARED` mientras la transacción siga activa.

### Sesión B

```sql
BEGIN TRANSACTION;
```

```sql
UPDATE demo_ventas SET monto = 110.0 WHERE id = 1;
```

La sesión B adquiere primero `UREAD` y luego intenta promoverlo a `EXCLUSIVE`. Como la sesión A conserva `SHARED`, la escritura debe esperar o terminar por timeout.

Vuelve a la sesión A:

```sql
ROLLBACK;
```

Después de liberar el lector, la sesión B puede continuar o debe repetirse según el timeout configurado.

En la vista `Transacciones` deberían observarse:

```text
SHARED + SHARED -> permitido
SHARED + UREAD -> permitido
UREAD -> EXCLUSIVE -> espera si existe otro SHARED
```

## 8. Dos lectores simultáneos

Usa dos sesiones diferentes.

### Sesión A

```sql
BEGIN TRANSACTION;
SELECT * FROM demo_ventas WHERE id = 1;
```

### Sesión B

```sql
BEGIN TRANSACTION;
SELECT * FROM demo_ventas WHERE id = 2;
```

Ambas lecturas deberían coexistir con locks `SHARED`. En la tabla de locks deberían aparecer dos propietarios para el mismo recurso o tabla.

Finaliza ambas sesiones:

```sql
ROLLBACK;
```

## 9. Recovery después de un cierre inesperado

Este escenario requiere dos instancias del motor o detener el backend sin ejecutar un cierre normal.

En una sesión ejecuta:

```sql
BEGIN TRANSACTION;
```

```sql
INSERT INTO demo_ventas VALUES (6, 'Sin Commit', 700.0);
```

No ejecutes `END TRANSACTION` ni `ROLLBACK`. Detén el backend y vuelve a iniciarlo con:

```bash
./run_web.sh
```

Al reabrir, `RecoveryManager` debe detectar la transacción incompleta y aplicar undo. Comprueba:

```sql
SELECT * FROM demo_ventas WHERE id = 6;
```

El resultado esperado es cero filas. En la vista `Transacciones` debería aparecer el evento de recovery y la cantidad de operaciones undo/CLR.

## 10. Recovery de una transacción confirmada

Ejecuta y confirma:

```sql
BEGIN TRANSACTION;
INSERT INTO demo_ventas VALUES (7, 'Confirmado', 900.0);
END TRANSACTION;
```

Reinicia el backend y consulta:

```sql
SELECT * FROM demo_ventas WHERE id = 7;
```

La fila debe conservarse. En el WAL se puede explicar que las operaciones de la transacción confirmada se consideran candidatas para redo.

## 11. DDL transaccional

Rollback de una tabla nueva:

```sql
BEGIN TRANSACTION;
CREATE TABLE demo_temporal (id INT PRIMARY KEY) USING HEAP;
ROLLBACK;
```

La tabla `demo_temporal` no debería aparecer en el catálogo.

Commit de una tabla nueva:

```sql
BEGIN TRANSACTION;
CREATE TABLE demo_confirmada (id INT PRIMARY KEY, nota VARCHAR(30)) USING HEAP;
END TRANSACTION;
```

La tabla debería permanecer después de recargar el catálogo o reiniciar el backend.

## 12. Cierre recomendado para la presentación

Termina con esta secuencia corta:

```sql
BEGIN TRANSACTION;
UPDATE demo_ventas SET monto = 1234.0 WHERE id = 1;
ROLLBACK;
SELECT * FROM demo_ventas WHERE id = 1;
```

Mientras se ejecuta, muestra en paralelo:

- el SQL en el editor;
- el lock `UREAD` y su promoción a `EXCLUSIVE`;
- el `UPDATE` del WAL;
- el `ABORT` y el `CLR`;
- la fila restaurada en resultados;
- el tiempo de ejecución y la cantidad real de filas afectadas.

## Notas para evitar problemas

- Ejecuta cada sentencia por separado cuando quieras mostrar la timeline paso a paso.
- Usa la misma pestaña para conservar la misma transacción; usa otra pestaña para probar concurrencia.
- Si una demo queda bloqueada, ejecuta `ROLLBACK` desde la sesión propietaria o reinicia el backend.
- No uses `DROP TABLE` ni `DROP INDEX`: todavía no están expuestos por el parser.
- Para presentar, conviene limpiar el catálogo y usar nombres `demo_*` para distinguir los objetos creados durante la demostración.
