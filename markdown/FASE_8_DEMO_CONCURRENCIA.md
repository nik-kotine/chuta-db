# Fase 8: demo multi-hilo

## Propósito

La demo muestra en vivo dos transacciones independientes compitiendo por el
mismo recurso. Usa las implementaciones reales de `TransactionManager`,
`LockManager` y `LogManager`.

## Escenario

- Recurso compartido: `("table", "cuentas")`.
- `T1` y `T2` comienzan en hilos separados.
- `T1` obtiene `EXCLUSIVE` y realiza un update lógico de `saldo = 1`.
- `T2` intenta obtener `EXCLUSIVE` y queda esperando.
- `T1` confirma y libera el lock.
- `T2` obtiene el lock, realiza `saldo = 2` y confirma.

Los `Event` y `Barrier` coordinan el orden. La demo no depende de `sleep`, por
lo que la contención es reproducible.

## Ejecución

Desde la raíz del repositorio:

```bash
python3 demo/concurrency_demo.py
```

Salida esperada, con IDs y orden de mensajes variables solo donde corresponda:

```text
[T1] BEGIN
[T2] BEGIN
[T1] obtuvo EXCLUSIVE sobre ('table', 'cuentas')
[T2] intenta EXCLUSIVE y queda esperando
[T1] ejecuta UPDATE logico: saldo = 1
[T1] COMMIT y libera el lock
[T2] obtiene EXCLUSIVE despues de T1
[T2] ejecuta UPDATE logico: saldo = 2
[T2] COMMIT y libera el lock

Resultado final: saldo = 2
La segunda transaccion no interleavo su UPDATE: espero el lock y luego continuo.
Transacciones confirmadas: 2
```

## Qué demuestra

1. Las transacciones se ejecutan en hilos distintos.
2. Dos escritores no entran simultáneamente al recurso.
3. El lock se conserva hasta `COMMIT`.
4. La segunda transacción espera y continúa después de la liberación.
5. Las dos transacciones quedan confirmadas en el WAL.
6. No quedan locks retenidos al finalizar.

La demo usa un valor lógico para concentrar la explicación en la contención.
La integración con las filas reales de `Table` ya está cubierta por los tests de
atomicidad y recovery.

## Definición de terminado

- El script se ejecuta sin configuración adicional.
- La salida permite identificar cada transacción y su lock.
- La espera ocurre por sincronización explícita.
- El resultado final es estable.
- El WAL temporal se limpia al terminar.
