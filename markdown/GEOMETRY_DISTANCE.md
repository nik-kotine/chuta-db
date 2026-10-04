# Geometría y distancias (`spatial/geometry.py`)

Módulo base de geometría 2D: punto, rectángulo (MBR), y las funciones de
distancia/relación espacial que necesita cualquier estructura que indexe
coordenadas.

## Qué incluye

- **`Point(x, y)`**: `x` es longitud (eje horizontal), `y` es latitud (eje
  vertical) — convención estándar tipo `(x, y)`, no `(lat, lon)`. `__eq__`
  y `__hash__` son consistentes entre sí (comparación exacta en ambos, para
  poder usar puntos como llave de `dict`/`set` sin romper el contrato de
  Python).
- **`euclidean(p1, p2)`**: distancia euclidiana estándar.
- **`haversine(p1, p2)`**: distancia geodésica (arco de círculo máximo)
  sobre una esfera de radio 6,371,000 m, para pares de coordenadas en
  grados decimales.
- **`Rectangle(min_x, min_y, max_x, max_y)`**: el MBR (bounding box). Expone
  `area()`, `union(other)`, `enlargement(other)` (cuánto crece el área al
  incluir `other`), `intersects(other)`, `contains_point(point)`, y los
  constructores `from_point(point)` / `from_points(points)`.
- **`mindist(point, rectangle, metric)`**: distancia mínima posible entre
  un punto y cualquier punto dentro de un rectángulo — se proyecta el punto
  sobre cada eje, clampeado al rango del rectángulo, y se mide esa
  distancia con la métrica pedida (`"euclidean"` o `"haversine"`). Con
  `"haversine"` es una aproximación (la proyección por eje asume geometría
  plana), razonable para áreas no continentales.
- **`point_in_polygon(point, polygon)`**: ray casting sobre una lista de
  puntos que forman un polígono (cerrado implícitamente entre el último
  punto y el primero).

## Decisiones tomadas

- `Point` queda genérico `(x, y)` en vez de atado a `(lat, lon)`: sirve
  igual para coordenadas proyectadas (x/y en metros) que para coordenadas
  geográficas (lon/lat en grados), y evita tener dos tipos de punto
  distintos según el caso de uso.
- La utilidad de MBR (`Rectangle`) vive en este módulo, no en el código de
  ningún índice en particular: es geometría genérica, reutilizable por
  cualquier estructura que la necesite.
- `Point.__eq__` usa comparación exacta de floats (no tolerancia), para que
  `__hash__` sea consistente con `__eq__` sin ambigüedad.

## Cómo se usa

```python
from spatial.geometry import Point, Rectangle, euclidean, haversine, mindist, point_in_polygon

p1 = Point(-77.03, -12.05)   # x=lon, y=lat
p2 = Point(-77.02, -12.04)

euclidean(p1, p2)             # distancia en grados (geometria plana)
haversine(p1, p2)             # distancia en metros (geodesica)

mbr = Rectangle.from_points([p1, p2])
mindist(Point(0, 0), mbr)     # distancia minima de (0,0) al rectangulo
```

## Tests

Ver `markdown/TEST_GEOMETRY.md`.
