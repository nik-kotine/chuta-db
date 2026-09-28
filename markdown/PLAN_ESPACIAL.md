# TODO ESPACIAL

## Plan de trabajo

1. geometría y distancias (issue 29)
2. test geometry module (issue 30)
3. scaffold y rtree (issue 31)

en ese orden porque el rtree necesita la geometría para los bounding box,
y no puedo testear algo que todavía no existe. fácil

## Actualmente

Ahora trabajando en geometry y distance module (issue 29)

**Hecho**: Point, distancia euclidiana

**TODO**: haversine, point in polygon (ray casting), y decidir si point
va con lat/lon o x/y de una vez (ver notas en el archivo)

Al terminar este issue se pasa al 30 (tests)
