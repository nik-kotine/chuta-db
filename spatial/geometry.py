import math

EARTH_RADIUS_M = 6371000


class Point:
    # Convencion: x = longitud (eje horizontal), y = latitud (eje vertical),
    # igual que ST_MakePoint(x, y) en PostGIS -- NO es (lat, lon).

    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return f"Point({self.x}, {self.y})"

    def __eq__(self, other):
        return isinstance(other, Point) and self.x == other.x and self.y == other.y

    def __hash__(self):
        return hash((self.x, self.y))


def euclidean(p1, p2):
    dx = p1.x - p2.x
    dy = p1.y - p2.y
    return math.sqrt(dx * dx + dy * dy)


def haversine(p1, p2):
    # p1, p2 en grados decimales (x=lon, y=lat). Devuelve metros.
    lat1, lat2 = math.radians(p1.y), math.radians(p2.y)
    dlat = lat2 - lat1
    dlon = math.radians(p2.x - p1.x)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


DISTANCE_FUNCS = {"euclidean": euclidean, "haversine": haversine}


class Rectangle:
    # MBR: Minimum Bounding Rectangle, {(min_x,min_y), (max_x,max_y)}

    def __init__(self, min_x, min_y, max_x, max_y):
        self.min_x = min_x
        self.min_y = min_y
        self.max_x = max_x
        self.max_y = max_y

    @classmethod
    def from_point(cls, point):
        return cls(point.x, point.y, point.x, point.y)

    @classmethod
    def from_points(cls, points):
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return cls(min(xs), min(ys), max(xs), max(ys))

    def area(self):
        return (self.max_x - self.min_x) * (self.max_y - self.min_y)

    def union(self, other):
        return Rectangle(
            min(self.min_x, other.min_x), min(self.min_y, other.min_y),
            max(self.max_x, other.max_x), max(self.max_y, other.max_y),
        )

    def enlargement(self, other):
        return self.union(other).area() - self.area()

    def intersects(self, other):
        return not (
            self.max_x < other.min_x or other.max_x < self.min_x
            or self.max_y < other.min_y or other.max_y < self.min_y
        )

    def contains_point(self, point):
        return self.min_x <= point.x <= self.max_x and self.min_y <= point.y <= self.max_y

    def __repr__(self):
        return f"Rectangle(({self.min_x}, {self.min_y}), ({self.max_x}, {self.max_y}))"


def mindist(point, rectangle, metric="euclidean"):
    # Distancia entre point y el punto mas cercano posible dentro de
    # rectangle: se proyecta point sobre cada eje, clampeado al rango del
    # rectangulo, y se mide la distancia al punto resultante.
    #
    # Para metric="haversine" esto es una aproximacion (la proyeccion por eje
    # asume geometria plana), no un lower bound estricto en la esfera -- pero
    # es la practica habitual para MBRs chicos frente al radio terrestre.
    closest_x = min(max(point.x, rectangle.min_x), rectangle.max_x)
    closest_y = min(max(point.y, rectangle.min_y), rectangle.max_y)
    closest = Point(closest_x, closest_y)
    return DISTANCE_FUNCS[metric](point, closest)


def point_in_polygon(point, polygon):
    # Ray casting. polygon: lista de Point, implicitamente cerrado
    # (se asume segmento entre el ultimo y el primero).
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i].x, polygon[i].y
        xj, yj = polygon[j].x, polygon[j].y
        crosses = (yi > point.y) != (yj > point.y)
        if crosses:
            x_at_y = (xj - xi) * (point.y - yi) / (yj - yi) + xi
            if point.x < x_at_y:
                inside = not inside
        j = i
    return inside
