# Cosas que faltan en transacciones (notas rápidas)

Comparando la rama con lo visto en clase de recovery/ACID: lo implementado está bien pero le falta la mitad del protocolo.

## No hay redo, solo undo

El commit solo hace fsync del WAL; las páginas de datos quedan en el buffer pool hasta que se desalojan o el motor cierra ordenado. Recovery solo deshace transacciones activas, nunca rehace una que sí hizo commit pero no llegó a disco. Si el proceso muere justo después de un commit, se pierde el cambio aunque el WAL diga committed. Falta la mitad de recovery (el redo).

Arreglo rápido sin ARIES completo: en END TRANSACTION, después de forzar el COMMIT en el wal, forzar también a disco las tablas tocadas (buffer_manager.flush_file) antes de soltar los locks.

Tampoco está bien probado: el test de recovery cierra con sm.close(), que vacía el buffer pool solo. Eso es apagado ordenado, no un crash real.

## El log es lógico, no físico

Guarda valores de fila (json), no bytes de página como planteaba la FASE_1 original. Alcanza para deshacer insert/delete por clave, pero si el crash pasa a mitad de una operación que toca datos + índices + catálogo, no hay garantía de que el undo deje todo consistente. Aclarar esto en el informe.

## Fuera de BEGIN/END no se loguea nada

Un INSERT suelto no deja rastro en el wal. En un motor real cada sentencia corre en autocommit y pasa por el log también. Acá un crash en medio de un INSERT normal es irrecuperable. Dejarlo claro en el readme.

## CREATE TABLE / CREATE INDEX sin lock ni log

El plan de concurrencia pedía lock exclusivo en el catálogo; nunca se conectó. Tampoco generan WAL, no hay rollback de DDL. Puede ser decisión consciente, pero hay que decirlo.

## rollback bloquea de más

rollback corre el undo_handler (I/O real) mientras tiene tomado su propio lock interno global, así que cualquier otra transacción que quiera begin/commit/log_update espera ese lock aunque toque otra tabla.

## qué atacar primero

1. force-at-commit para durabilidad real
2. test de crash real (sin sm.close())
3. documentar en el readme el log lógico y que autocommit no se loguea
4. sacar el I/O del rollback de dentro del lock grande, si da tiempo
