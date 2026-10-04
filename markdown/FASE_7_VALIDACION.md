# Fase 7: validación de transacciones y concurrencia

## Propósito

Esta fase consolida las fases anteriores mediante pruebas de comportamiento.
No agrega una nueva capa de producción: verifica que el sistema aborte,
revierte, bloquea y recupera de forma observable.

## Casos cubiertos

### Atomicidad ante errores

Una transacción inserta una fila válida y después intenta insertar una primary
key duplicada. El executor debe abortar la transacción completa, restaurar el
estado anterior y liberar los locks de la sesión.

### Competencia entre hilos

Un lector adquiere `SHARED` y espera una señal. Un escritor intenta `EXCLUSIVE`
y debe permanecer bloqueado hasta que el lector libere. `Event` controla el
orden, evitando depender de `sleep`.

### Timeout

Un escritor mantiene un recurso y otro escritor espera con timeout. El segundo
debe recibir `LockTimeoutError` y no dejar ownership ni estado de espera
pendiente.

### Recovery idempotente

Se simula un crash con un insert sin commit. El primer arranque lo deshace. Un
segundo arranque no debe volver a insertar ni borrar filas adicionales.

## Ejecución

```bash
python3 -m tests.table.test_phase7_validation
python3 run_all_tests.py
```

El test específico es el criterio rápido de la fase; el runner completo es la
regresión final. Los tests usan directorios temporales para no contaminar el
catálogo ni los archivos `.dat` del proyecto.

## Definición de terminado

- Errores dentro de una transacción restauran el estado previo.
- No quedan locks después de abortar o de un timeout.
- La contención entre lectores y escritores es reproducible.
- Recovery de una transacción incompleta es idempotente.
- La suite de fases 1 a 7 pasa.