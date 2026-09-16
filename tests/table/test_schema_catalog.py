import os
from storage.storage_manager import StorageManager


def limpiar():
    archivos = ["sys_tables.dat", "sys_columns.dat", "estudiantes.dat", "cursos.dat"]
    for f in archivos:
        if os.path.exists(f):
            os.remove(f)


def test_schema_catalog():
    limpiar()

    # 1. Crear tablas y verificar inserción
    with StorageManager() as sm:
        est = sm.create_table("estudiantes", ["integer", "varchar(30)"], "heap")
        est.insert([101, "Pedro"])
        est.insert([102, "Maria"])

        cur = sm.create_table("cursos", ["integer", "varchar(20)"], "sequential", key_index=0)
        cur.insert([1, "Base de Datos 2"])

    # Verificar que los archivos físicos del catálogo fueron creados
    assert os.path.exists("sys_tables.dat")
    assert os.path.exists("sys_columns.dat")
    print("Creación de tablas del sistema: OK")

    # 2. Reabrir StorageManager (Prueba de persistencia real)
    with StorageManager() as sm_reloaded:
        # Deben existir en el catálogo
        assert sm_reloaded.catalog.get_table_info("estudiantes") is not None
        assert sm_reloaded.catalog.get_table_info("cursos") is not None

        # Cargar tabla desde metadata persistida
        t_est = sm_reloaded.open_table("estudiantes")
        data = [val for _, val in t_est.scan()]
        assert len(data) == 2
        assert data[0] == [101, "Pedro"]

    print("Persistencia del catálogo desde HeapFiles: OK")

    # 3. Eliminar tabla (Drop Table)
    with StorageManager() as sm:
        sm.drop_table("estudiantes")
        assert sm.catalog.get_table_info("estudiantes") is None
        assert not os.path.exists("estudiantes.dat")

    print("Drop table del catálogo: OK")
    limpiar()


if __name__ == "__main__":
    print("=== PROBANDO SCHEMA CATALOG CON HEAPFILES ===")
    test_schema_catalog()
    print("¡Todas las pruebas del catálogo de esquema pasaron exitosamente!")