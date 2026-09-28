import math
class Point: # sujeto a modificaciones, falta terminar
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return f"Point({self.x}, {self.y})"

    def __eq__(self, other):
        # para que los test de igualdad no se rompan, ver si hace falta tolerancia
        return self.x == other.x and self.y == other.y


def euclidean(p1, p2):
    # raiz de la suma de cuadrados
    dx = p1.x - p2.x
    dy = p1.y - p2.y
    return math.sqrt(dx * dx + dy * dy)

# CAMBIOS EN POINT

# TODO Point: decidir si es x/y o lat/lon (ambiguo)

# TODO Point: __eq__ compara exacto, con floats eso tarde o temprano
# rompe algun test, ver si conviene tolerancia (math.isclose)

# TODO Point: pensar si le meto distance_to() o lo dejo todo como
# funciones sueltas (creo que separado es mas facil de testear)

# TODO Point: le falta __hash__ si en algun momento lo uso como key
# de dict o lo meto en un set (por el eq custom python lo saca solo)


# LO QUE FALTA

# falta haversine(p1, p2) -> distancia geodesica en metros, radio tierra
# 6371000 aprox, formula esta en el cuaderno

# falta point_in_polygon(point, poligono) -> ray casting, ver casos
# borde de vertice/arista antes de darlo por bueno

# el rtree (issue 31) va a necesitar sacar un MBR de un grupo de points,
# ver si eso va aca o en el archivo del rtree directo
