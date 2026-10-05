Además en front faltaban bastantes de las cosas que habíamos visto - resultados debería dar una notificación más notoria, los números de línea ahora se quedan fijos

(fix) índices de página se van del espacio negro del espacio en el que se tipea la query
Plan de ejecución debería estar a la izquierda y resultados justo debajo de la query
Mensaje de éxito al correr la query sin errores, tiempo tomado

Siempre se dice 1 fila(s) afectadas aunque sean más

Botón para parar consulta a mitad de ejecución (idealmente)
Que se note que el frontend no es un solo prompt a Claude, cambiar un poquito el formato

## Vista de transacciones y concurrencia para la demo

La consola debería tener una vista específica para explicar el comportamiento del motor. No conviene mezclar toda esta información con la tabla de resultados: la demo necesita mostrar el estado de una transacción, sus locks y el orden de eventos de manera inmediata.

### Propuesta de interfaz

- **Pestaña `Transacciones`** junto a la vista principal de consultas.
- **Resumen superior** con transacciones activas, locks retenidos, transacciones recuperadas y último checkpoint.
- **Tarjetas de estado** para `ACTIVE`, `COMMITTED`, `ABORTED` y `WAITING`, usando color, icono y texto; no depender únicamente del color.
- **Timeline de eventos** con `BEGIN`, adquisición de `SHARED`/`UREAD`/`EXCLUSIVE`, `UPDATE`, `COMMIT`, `ROLLBACK`, `ABORT`, `CLR` y `CHECKPOINT`.
- **Tabla de locks** con recurso, transacción propietaria, modo, contador de reentrada, transacciones esperando y tiempo de espera.
- **Panel de WAL** con `LSN`, `prev_lsn`, tipo de registro, transacción, recurso, página y tamaño de `before`/`after`.
- **Diagrama de conflicto** que conecte una transacción escritora con lectores bloqueados o con otra transacción esperando promover `UREAD` a `EXCLUSIVE`.
- **Panel de recovery** que indique cuántas operaciones se ejecutaron en redo, cuántas en undo y cuántos `CLR` se generaron.

### Demo guiada

La vista debería incluir escenarios reproducibles para no depender de escribir todo a mano durante la presentación:

1. **Commit normal**: `BEGIN`, `INSERT`, adquisición de `EXCLUSIVE`, `COMMIT` durable y liberación del lock.
2. **Rollback**: `BEGIN`, `UPDATE`, generación de `before`/`after`, `ROLLBACK`, undo y restauración de la fila.
3. **Dos lectores**: dos transacciones con `SHARED` simultáneo.
4. **Promoción de update**: una transacción adquiere `UREAD`, encuentra filas y espera lectores antes de promocionarse a `EXCLUSIVE`.
5. **Timeout**: una transacción intenta escribir mientras otra conserva un lock incompatible; se muestra la espera y luego `LockTimeoutError`.
6. **Crash y recovery**: se simula una transacción sin `COMMIT`, se reabre el motor y se muestra `REDO` de confirmadas seguido de `UNDO` de incompletas.

Cada escenario debería tener una pequeña explicación, un botón `Iniciar demo`, controles `Paso anterior`, `Siguiente paso`, `Reproducir` y `Reiniciar`, y una consulta SQL visible pero no editable durante la animación. El usuario debe poder pausar la demo para explicar cada evento.

### Contrato mínimo del backend

Para mostrar datos reales, sería útil agregar endpoints separados del endpoint de consultas:

```text
GET  /api/transactions
GET  /api/transactions/{transaction_id}
GET  /api/locks
GET  /api/wal?transaction_id=...
GET  /api/recovery/status
POST /api/demo/{scenario}/reset
POST /api/demo/{scenario}/step
```

La respuesta de una transacción podría incluir:

```json
{
	"transaction_id": 7,
	"status": "ACTIVE",
	"last_lsn": 18432,
	"locks": [
		{"resource": "ventas", "mode": "UREAD", "count": 1}
	],
	"events": [
		{"type": "BEGIN", "lsn": 18100},
		{"type": "UPDATE", "lsn": 18432, "resource": "ventas"}
	]
}
```

Si todavía no se quiere exponer el estado interno del motor, se puede implementar primero un modo demo con un estado controlado en el frontend. Debe estar etiquetado como `SIMULACIÓN` y no presentarse como una lectura real del WAL. La versión final debería reemplazar esa simulación con los endpoints reales.

### Orden visual recomendado

- **Columna izquierda**: lista de escenarios y transacciones.
- **Centro**: timeline y diagrama de locks.
- **Columna derecha**: detalle del WAL y estado de recovery.
- **Parte inferior**: SQL ejecutado, resultado y mensaje de estado.

En móvil, estas áreas deberían convertirse en pestañas: `Estado`, `Locks`, `WAL` y `Recovery`. La timeline debe conservar el orden vertical y permitir desplazamiento horizontal si los detalles del evento son largos.

### Detalles que hacen que la demo se entienda

- Mostrar una leyenda persistente para `SHARED`, `UREAD` y `EXCLUSIVE`.
- Animar únicamente el evento que está ocurriendo y atenuar los eventos ya completados.
- Mostrar una flecha explícita cuando `UREAD` se promociona a `EXCLUSIVE`.
- Diferenciar `esperando`, `bloqueada`, `confirmada` y `abortada`; no usar solo `loading`.
- Al seleccionar un evento, resaltar su registro correspondiente del WAL.
- Al seleccionar un lock, resaltar las transacciones afectadas.
- Mostrar una marca de durabilidad cuando el `COMMIT` ya pasó por `force()`.
- Incluir un botón para copiar el SQL y el resumen de eventos de la demo.

Esta vista demostraría que el frontend representa componentes reales del DBMS: catálogo, executor, locks, WAL, undo, redo y recovery. Sería una evidencia mucho más fuerte que una consola que únicamente envía una consulta y muestra una tabla.

## Panel espacial y visualización de mapas

La consola debe incluir un panel `Mapa de puntos` para demostrar la parte espacial del proyecto. El mapa debe visualizar filas que tengan columnas `latitude`/`longitude`, `lat`/`lon` o `x`/`y`, usando la convención del motor: `x = longitud` y `y = latitud`.

### Comportamiento implementado

- El backend expone `GET /api/spatial/points`.
- El endpoint recorre las tablas del catálogo y detecta columnas de coordenadas por nombre.
- Cada punto devuelve tabla, RID, coordenadas, etiqueta y valores originales.
- El frontend usa Leaflet con OpenStreetMap.
- Los puntos aparecen como marcadores y muestran un popup con etiqueta, coordenadas y tabla.
- El mapa se filtra según la tabla seleccionada en el catálogo.
- Si no existen filas espaciales, el panel explica qué nombres de columnas espera.
- El mapa ajusta automáticamente el encuadre para incluir todos los puntos visibles.

### Próximas extensiones espaciales

- Resaltar en un color diferente los puntos devueltos por una consulta de rango.
- Dibujar el centro y radio de una consulta de distancia.
- Dibujar polígonos y resaltar los puntos contenidos en ellos.
- Mostrar una línea entre el punto de consulta y sus vecinos `k` más cercanos.
- Selector de métrica: Euclidiana o Haversine.
- Controles de radio, `k` y polígono directamente sobre el mapa.
- Endpoint específico para resultados espaciales, por ejemplo `GET /api/spatial/search`.
- Mostrar en el popup la distancia calculada y el tipo de consulta que produjo el resultado.

### Datos mínimos para probarlo

La tabla debe tener, como mínimo, una clave primaria, una columna de latitud y una de longitud:

```sql
CREATE TABLE demo_lugares (
	id INT PRIMARY KEY,
	nombre VARCHAR(40),
	latitude FLOAT,
	longitude FLOAT
) USING HEAP;
```

```sql
INSERT INTO demo_lugares VALUES (1, 'Centro de Lima', -12.0464, -77.0428);
INSERT INTO demo_lugares VALUES (2, 'Miraflores', -12.1260, -77.0300);
INSERT INTO demo_lugares VALUES (3, 'San Isidro', -12.0970, -77.0365);
```

Al seleccionar `demo_lugares` en el catálogo, el panel debe mostrar los tres puntos sobre el mapa.

## Mejoras adicionales propuestas

### Prioridad alta: flujo de consulta

- **Historial de consultas ejecutadas**: mostrar las últimas consultas con hora, duración, estado y cantidad de filas. Permitir volver a cargar una consulta en el editor con un clic.
- **Guardar consultas favoritas**: permitir marcar consultas frecuentes y conservarlas en `localStorage`, con nombre editable y opción de eliminar.
- **Ejecutar solo la selección**: si el usuario selecciona una parte del SQL, ejecutar únicamente ese fragmento; si no hay selección, ejecutar todo el editor.
- **Validación antes de enviar**: detectar editor vacío, sentencias incompletas y errores básicos de sintaxis antes de hacer el `POST` al backend.
- **Resultados de operaciones DML más informativos**: mostrar verbo ejecutado, cantidad real de filas afectadas, duración y estado (`COMMIT`, `ROLLBACK` o error), en vez de reutilizar el mismo mensaje para todas las consultas.
- **Separar resultado de datos y mensaje de operación**: un `SELECT` debe mostrar una tabla; un `INSERT`, `UPDATE`, `DELETE` o DDL debe mostrar una tarjeta de resumen con filas afectadas y cambios realizados.
- **Abortar consulta de verdad**: usar `AbortController` en el `fetch`, conectar el botón de cancelar con el backend y distinguir entre cancelación, timeout y error del servidor.
- **Evitar respuestas obsoletas**: asignar un identificador a cada ejecución para que una consulta antigua que termine tarde no reemplace el resultado de una consulta más reciente.

### Prioridad alta: editor SQL

- **Sincronizar correctamente el gutter**: los números de línea deben desplazarse junto con el textarea y conservar exactamente el mismo alto de línea, padding y scroll vertical.
- **Indicador de posición del cursor**: mostrar línea y columna actuales en la barra inferior del editor.
- **Indentación automática**: al presionar Enter, conservar la indentación de la línea anterior y manejar el cierre de paréntesis.
- **Atajos de edición**: soportar `Cmd/Ctrl + Enter`, `Cmd/Ctrl + /` para comentarios, `Tab` para indentación y `Escape` para cancelar una ejecución.
- **Plantillas SQL**: ofrecer comandos iniciales para `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE` y transacciones.
- **Resaltado de sintaxis**: reemplazar el textarea plano por un editor con highlighting, manteniendo el gutter sincronizado. Una alternativa ligera es CodeMirror o Monaco.
- **Indicador de cambios sin guardar**: mostrar cuando el contenido del editor difiere de una consulta guardada o favorita.

### Prioridad alta: resultados y plan

- **Reordenar la composición**: colocar el plan de ejecución a la izquierda y los resultados inmediatamente debajo del editor, para que la relación entre consulta, plan y salida sea evidente.
- **Panel de resultados redimensionable**: permitir ampliar verticalmente la tabla cuando haya muchas filas sin ocultar completamente el editor.
- **Columnas ajustables**: permitir cambiar el ancho de las columnas, copiar una celda y copiar una fila completa.
- **Formato por tipo de dato**: alinear números a la derecha, mostrar booleanos y `NULL` con estilos distintos y formatear fechas de manera consistente.
- **Exportar resultados**: descargar la salida como CSV o JSON y copiarla al portapapeles como tabla Markdown.
- **Búsqueda dentro de resultados**: filtrar las filas visibles sin volver a ejecutar la consulta.
- **Plan expandible**: permitir abrir y cerrar detalles de cada nodo, mostrando tabla, índice, acceso, filas estimadas y filas reales cuando estén disponibles.
- **Comparación de planes**: conservar el plan anterior y permitir compararlo con el plan de la consulta actual para ver cuándo se comenzó a usar un índice.
- **Estado vacío diferenciado**: distinguir entre “todavía no se ejecutó”, “la consulta devolvió cero filas”, “la consulta modificó datos” y “la consulta falló”.

### Prioridad media: catálogo y navegación

- **Buscar tablas**: agregar un filtro al catálogo para encontrar rápidamente una tabla por nombre.
- **Vista de detalles de tabla**: mostrar columnas, tipos, clave primaria, organización física, cantidad de índices y nombre de los índices.
- **Insertar nombres desde el catálogo**: al hacer clic en una tabla o columna, insertar su nombre en la posición del cursor del editor.
- **Menú contextual del catálogo**: acciones para generar un `SELECT *`, un `SELECT` limitado o una plantilla de `INSERT` para la tabla seleccionada.
- **Refresh con estado visible**: mostrar cuándo se actualizó el catálogo, si la carga está en curso y si falló la conexión.
- **Persistir la tabla seleccionada**: recordar la última tabla abierta al recargar la página.

### Prioridad media: estados, errores y observabilidad

- **Toast de éxito y error**: mostrar una notificación notoria, con color, icono, duración y mensaje específico; no depender únicamente del badge `QUERY COMPLETE`.
- **Errores accionables**: separar mensaje del backend, línea, columna y sugerencia de corrección cuando el parser devuelva posiciones.
- **Estado de conexión real**: diferenciar `conectando`, `online`, `sin conexión`, `backend ocupado` y `backend caído`.
- **Tiempo de ejecución**: medir la duración desde el envío hasta la respuesta y mostrarla junto con la cantidad de filas afectadas.
- **Métricas de la sesión**: mostrar número de consultas ejecutadas, consultas fallidas y tiempo acumulado, sin convertir la interfaz en un dashboard recargado.
- **Mensajes de recuperación**: si el backend informa que recuperó transacciones después de un reinicio, mostrarlo como evento de sistema en un panel de actividad.
- **Logs visibles para desarrollo**: agregar un modo de diagnóstico que muestre endpoint, código HTTP, duración y tamaño de respuesta sin exponer información sensible.

### Prioridad media: accesibilidad y responsive

- **Navegación completa con teclado**: asegurar foco visible y orden lógico entre catálogo, editor, ejecución, resultados y plan.
- **Etiquetas accesibles**: añadir `aria-label`, `aria-live` para resultados/notificaciones y `aria-expanded` en tablas del catálogo.
- **Contraste y estados de foco**: revisar contraste del texto ámbar, gris y verde sobre los fondos actuales, además de hover, focus, disabled y loading.
- **Diseño móvil útil**: convertir los paneles en pestañas o acordeones en pantallas pequeñas para no obligar a recorrer cuatro bloques verticales enormes.
- **Responsive para tablas anchas**: mantener el encabezado visible y permitir scroll horizontal sin que la página completa se desplace accidentalmente.
- **Preferencia de movimiento reducido**: respetar `prefers-reduced-motion` si se agregan animaciones para resultados o notificaciones.

### Prioridad media: consistencia visual

- **Jerarquía de estados más clara**: reservar verde para éxito, rojo para error, ámbar para advertencia/selección y azul o gris para información neutral.
- **Acciones con iconos y tooltips**: usar iconos para refrescar, copiar, descargar, cancelar y limpiar, con tooltips y texto accesible.
- **Feedback durante carga**: reemplazar el estado estático `EJECUTANDO` por una barra o indicador de progreso indeterminado, sin hacer creer que se conoce el porcentaje real.
- **Diseñar un panel de actividad**: registrar consultas, commits, rollbacks, errores y eventos de recovery en una línea de tiempo compacta.
- **Reducir texto monolítico en la pantalla**: usar badges, metadatos y bloques de estado para que la consola se sienta como una herramienta de base de datos, no como una página de chat.

### Prioridad baja: funcionalidades futuras

- **Modo comparación de consultas**: mostrar dos editores y comparar resultados o planes.
- **Modo oscuro real**: crear una paleta propia para el editor y los paneles, no solo invertir colores.
- **Compartir consultas reproducibles**: generar un enlace o archivo con SQL, esquema esperado y parámetros, sin incluir datos sensibles.
- **Parámetros de consulta**: permitir variables como `:id` y mostrar un formulario de valores antes de ejecutar.
- **Historial persistente por proyecto**: guardar consultas y resultados resumidos en el backend, con fecha y usuario.
- **Vista de locks**: mostrar recursos bloqueados, modo (`SHARED`, `UREAD`, `EXCLUSIVE`), transacción propietaria y esperas activas.
- **Vista de recovery**: visualizar transacciones recuperadas, cantidad de registros undo/redo y último checkpoint.

## Propuesta de imágenes y demostraciones

Además de la interfaz, conviene incluir en el informe o README capturas pequeñas y funcionales:

- **Captura de consulta exitosa**: editor, toast de éxito, duración, filas afectadas y tabla de resultados.
- **Captura de error SQL**: consulta con error, línea/columna marcada y mensaje accionable.
- **Captura del plan**: una consulta que use índice y otra que haga scan, mostrando la diferencia en el plan.
- **Captura del catálogo**: tabla expandida con columnas, clave primaria e índices.
- **Captura responsive**: versión móvil con editor, resultados y plan organizados en acordeón o pestañas.
- **Captura de transacción**: `BEGIN`, operaciones, `ROLLBACK` y resultado restaurado.

La captura con mayor valor demostrativo sería una secuencia de tres estados: consulta antes de ejecutar, resultado exitoso con plan y una operación fallida con rollback. Eso demostraría que el frontend comunica estados del DBMS y no se limita a enviar texto y mostrar una respuesta.

