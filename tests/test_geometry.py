import math

from spatial.geometry import Point, Rectangle, euclidean, haversine, mindist, point_in_polygon


def test_euclidean():
    p1 = Point(0, 0)
    p2 = Point(3, 4)
    assert euclidean(p1, p2) == 5.0
    print("OK: distancia euclidiana (triangulo 3-4-5)")


def test_rectangle_area_union_enlargement():
    r1 = Rectangle(0, 0, 2, 2)
    r2 = Rectangle(1, 1, 4, 4)

    assert r1.area() == 4.0
    union = r1.union(r2)
    assert union.min_x == 0 and union.min_y == 0 and union.max_x == 4 and union.max_y == 4
    assert union.area() == 16.0
    assert r1.enlargement(r2) == union.area() - r1.area()
    print("OK: Rectangle.area/union/enlargement")


def test_rectangle_intersects_y_contains():
    r1 = Rectangle(0, 0, 2, 2)
    r2 = Rectangle(2, 2, 3, 3)  # se tocan justo en una esquina -> intersecta
    r3 = Rectangle(5, 5, 6, 6)  # separado

    assert r1.intersects(r2)
    assert not r1.intersects(r3)
    assert r1.contains_point(Point(1, 1))
    assert not r1.contains_point(Point(5, 5))
    print("OK: Rectangle.intersects/contains_point")


def test_rectangle_from_points():
    points = [Point(3, 4), Point(5, 7), Point(6, 3), Point(4, 1)]
    mbr = Rectangle.from_points(points)
    assert (mbr.min_x, mbr.min_y, mbr.max_x, mbr.max_y) == (3, 1, 6, 7)
    print("OK: Rectangle.from_points")


def test_mindist_proyeccion_por_eje():
    # Q=(1,2), MBR={(3,1),(6,7)} -> MINDIST=2 (proyeccion sobre el eje x)
    q = Point(1, 2)
    mbr = Rectangle(3, 1, 6, 7)
    assert math.isclose(mindist(q, mbr), 2.0)
    print("OK: MINDIST con proyeccion por eje da el resultado esperado")


def test_mindist_punto_adentro_es_cero():
    mbr = Rectangle(0, 0, 10, 10)
    assert mindist(Point(5, 5), mbr) == 0.0
    print("OK: MINDIST es 0 cuando el punto esta dentro del MBR")


def test_haversine_un_grado_de_longitud_en_el_ecuador():
    # 1 grado de longitud en el ecuador son ~111.2 km (con R=6371000)
    p1 = Point(0, 0)
    p2 = Point(1, 0)
    distancia = haversine(p1, p2)
    assert abs(distancia - 111195) < 500
    print(f"OK: haversine(1 grado de longitud en el ecuador) = {distancia:.1f}m")


def test_point_in_polygon():
    cuadrado = [Point(0, 0), Point(0, 10), Point(10, 10), Point(10, 0)]

    assert point_in_polygon(Point(5, 5), cuadrado)
    assert not point_in_polygon(Point(15, 15), cuadrado)
    assert not point_in_polygon(Point(-1, 5), cuadrado)
    print("OK: point_in_polygon (adentro/afuera)")


tests = [
    test_euclidean,
    test_rectangle_area_union_enlargement,
    test_rectangle_intersects_y_contains,
    test_rectangle_from_points,
    test_mindist_proyeccion_por_eje,
    test_mindist_punto_adentro_es_cero,
    test_haversine_un_grado_de_longitud_en_el_ecuador,
    test_point_in_polygon,
]

for test in tests:
    print(f"Corriendo {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests pasaron.")
