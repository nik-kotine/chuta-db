import os
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.table import Table
from storage.constraints_manager import IntegrityError

PAGE_SIZE = 4096
HEADER_SIZE = 16
BUFFER_FRAMES = 50


def limpiar():
    filename = "test_ext_constraints.dat"
    if os.path.exists(filename):
        os.remove(filename)


def test_extended_constraints():
    limpiar()
    filename = "test_ext_constraints.dat"

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    
    # Esquema: [id (PK), correo (UNIQUE), edad (CHECK >= 18), telefono (NOT NULL)]
    schema = ["integer", "varchar(50)", "integer", "integer"]
    
    not_nulls = [3]  # columna 3 (telefono) es obligatoria
    uniques = [1]    # columna 1 (correo) debe ser única
    checks = [lambda v: v[2] >= 18]  # la edad debe ser >= 18

    with Table(
        name="usuarios", 
        schema=schema, 
        buffer_manager=bm, 
        file_type="heap", 
        key_index=0,
        not_null_columns=not_nulls,
        unique_columns=uniques,
        check_constraints=checks
    ) as tabla:
        
        # 1. Inserción correcta
        rid1 = tabla.insert([1, "alice@mail.com", 25, 999111222])
        assert rid1 is not None

        # 2. Fallo por restricción CHECK (edad < 18)
        try:
            tabla.insert([2, "bob@mail.com", 16, 888777666])
            assert False, "Debió fallar por regla CHECK de edad"
        except IntegrityError as e:
            print(f"CHECK capturado correctamente: {e}")

        # 3. Fallo por restricción UNIQUE (correo repetido)
        try:
            tabla.insert([3, "alice@mail.com", 30, 777666555])
            assert False, "Debió fallar por correo duplicado (UNIQUE)"
        except IntegrityError as e:
            print(f"UNIQUE capturado correctamente: {e}")

        # 4. Fallo por restricción NOT NULL (telefono en None)
        try:
            tabla.insert([4, "charlie@mail.com", 22, None])
            assert False, "Debió fallar por teléfono nulo (NOT NULL)"
        except IntegrityError as e:
            print(f"NOT NULL capturado correctamente: {e}")

        # 5. Otra inserción válida
        rid2 = tabla.insert([4, "charlie@mail.com", 22, 555444333])
        assert rid2 is not None

    limpiar()
    print("test_extended_constraints: OK")


if __name__ == "__main__":
    print("=== PROBANDO CONSTRAINTS EXTENDIDAS (NOT NULL, UNIQUE, CHECK) ===")
    test_extended_constraints()
    print("¡Todas las pruebas de restricciones extendidas pasaron con éxito!")