# Tests del módulo de geometría (`tests/test_geometry.py`)

Cobertura de correctitud para `spatial/geometry.py` (ver
`markdown/GEOMETRY_DISTANCE.md`), antes de construir cualquier estructura
que dependa de esas primitivas.

## Qué se prueba

| Test | Qué valida |
|---|---|
| `test_euclidean` | triángulo 3-4-5 |
| `test_rectangle_area_union_enlargement` | `Rectangle.area`/`union`/`enlargement` |
| `test_rectangle_intersects_y_contains` | `Rectangle.intersects`/`contains_point`, incluyendo el caso borde de dos rectángulos que solo se tocan en una esquina |
| `test_rectangle_from_points` | `Rectangle.from_points` reconstruye el bounding box correcto de un set de puntos |
| `test_mindist_proyeccion_por_eje` | `mindist` contra un caso con resultado conocido de antemano |
| `test_mindist_punto_adentro_es_cero` | `mindist` es 0 cuando el punto ya está dentro del rectángulo |
| `test_haversine_un_grado_de_longitud_en_el_ecuador` | 1° de longitud en el ecuador ≈ 111.2 km (tolerancia 500 m) |
| `test_point_in_polygon` | punto adentro / afuera de un cuadrado |

## Convención de tests usada en este repo

Sigue el mismo patrón que el resto de `tests/`: script plano (no pytest),
funciones `test_*`, `assert` desnudos, una lista `tests = [...]` al final
que corre todo con prints de progreso. Se descubre solo con
`run_all_tests.py` (busca `test_*.py` bajo `tests/`), no hace falta
registrarlo en ningún lado.

## Cómo correrlo

```bash
python run_all_tests.py geometry
```
