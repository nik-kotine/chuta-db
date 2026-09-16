import os
import struct
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.table import Table
from indexes.b_plus_unclustered import BPlusTreeUnclustered
from indexes.b_plus_clustered import BPlusTreeClustered
from storage.files.sequential_file import FILE_HEADER_FORMAT, FILE_HEADER_SIZE

PAGE_SIZE = 4096
HEADER_SIZE = 16
BUFFER_FRAMES = 50


def limpiar():
    archivos = [
        "empleados_heap.dat", "empleados_seq.dat",
        "idx_emp_edad.idx", "idx_emp_pk.idx"
    ]
    for f in archivos:
        if os.path.exists(f):
            os.remove(f)


def test_table_with_unclustered_index():
    limpiar()
    filename = "empleados_heap.dat"

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    # Esquema: [id, nombre, edad]
    schema = ["integer", "varchar(30)", "integer"]

    # Creamos una tabla tipo Heap
    with Table("empleados_heap", schema, bm, file_type="heap") as tabla:
        
        # Creamos un índice secundario (unclustered) para la columna 2 ("edad", que es entero)
        idx_edad = BPlusTreeUnclustered("idx_emp_edad.idx", tabla.data_file, schema)
        tabla.attach_index(column_index=2, index_obj=idx_edad)

        # 1. Insertar registros (debe actualizar la tabla y el índice de edades automáticamente)
        rid1 = tabla.insert([1, "Alice", 30])
        rid2 = tabla.insert([2, "Bob", 25])
        rid3 = tabla.insert([3, "Charlie", 30]) # Edad repetida para probar múltiples RIDs

        # 2. Buscar a través del índice secundario unclustered usando la edad (entero)
        rids_edad_30 = idx_edad.search(30)
        assert len(rids_edad_30) == 2
        assert rid1 in rids_edad_30
        assert rid3 in rids_edad_30

        # Verificar que podemos recuperar los datos reales usando los RIDs devueltos por el índice
        assert tabla.get(rid1) == [1, "Alice", 30]

        # 3. Borrar un registro y verificar que el índice secundario también se limpie
        assert tabla.delete(rid1) is True
        
        rids_edad_30_despues = idx_edad.search(30)
        assert len(rids_edad_30_despues) == 1
        assert rids_edad_30_despues[0] == rid3

        idx_edad.close()

    limpiar()
    print("test_table_with_unclustered_index: OK")


def test_table_with_clustered_index():
    limpiar()
    data_filename = "empleados_seq.dat"

    # Preparar el archivo físico secuencial base con su cabecera y espacio inicial
    if not os.path.exists(data_filename):
        with open(data_filename, "wb") as f:
            f.write(struct.pack(FILE_HEADER_FORMAT, 0, -1, 0, 0))
            f.write(b"\x00" * PAGE_SIZE)

    fm = FileManager(data_filename, PAGE_SIZE, FILE_HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    schema = ["integer", "varchar(30)"]

    with Table("empleados_seq", schema, bm, file_type="sequential", key_index=0) as tabla:
        
        # Envolvemos el archivo secuencial en un Árbol B+ Clustered (clave numérica en col 0)
        idx_clustered = BPlusTreeClustered("idx_emp_pk.idx", tabla.data_file)
        tabla.set_clustered_index(idx_clustered)

        # 1. Insertar a través del índice Clustered
        tabla.insert([10, "Zack"])
        tabla.insert([5, "Ana"])
        tabla.insert([20, "Luis"])

        # 2. Búsqueda rápida por clave numérica usando el árbol primario
        res = tabla.search_by_key(5)
        assert len(res) == 1
        assert res[0][0] == 5
        assert res[0][1].startswith("Ana")

        idx_clustered.close()

    limpiar()
    print("test_table_with_clustered_index: OK")


if __name__ == "__main__":
    print("=== PROBANDO INTEGRACIÓN DE TABLAS E ÍNDICES (NUMÉRICOS) ===")
    test_table_with_unclustered_index()
    test_table_with_clustered_index()
    print("¡Todas las pruebas de integración pasaron con éxito!")