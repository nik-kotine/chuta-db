import os
from storage.storage_manager import StorageManager

def limpiar_entorno():
    for filename in [
        "sys_tables.dat",
        "sys_columns.dat",
        "usuarios.dat",
        "productos.dat",
    ]:
        if os.path.exists(filename):
            os.remove(filename)

def test_storage_manager_lifecycle():
    limpiar_entorno()

    # 1. Inicializar StorageManager
    with StorageManager() as sm:
        
        # Crear una tabla tipo Heap
        tabla_usuarios = sm.create_table(
            name="usuarios", 
            schema=["integer", "varchar(30)", "boolean"], 
            file_type="heap"
        )
        
        rid1 = tabla_usuarios.insert([1, "Carlos", True])
        rid2 = tabla_usuarios.insert([2, "Ana", False])
        
        assert tabla_usuarios.get(rid1) == [1, "Carlos", True]

        # Crear una tabla tipo Sequential
        tabla_productos = sm.create_table(
            name="productos", 
            schema=["integer", "real"], 
            file_type="sequential", 
            key_index=0
        )
        
        tabla_productos.insert([100, 19.99])
        tabla_productos.insert([50, 9.99])
        
        # Verificar ordenamiento secuencial
        keys = [val[0] for _, val in tabla_productos.scan()]
        assert keys == [50, 100]

    print("Ciclo de vida del StorageManager: OK")

    # 2. Test de Persistencia (reabrir la base de datos)
    with StorageManager() as sm_reloaded:
        assert sm_reloaded.catalog.get_table_info("usuarios") is not None
        assert sm_reloaded.catalog.get_table_info("productos") is not None
        
        # Reabrir la tabla de usuarios y verificar datos
        t_usr = sm_reloaded.open_table("usuarios")
        records = list(t_usr.scan())
        assert len(records) == 2

    print("Persistencia del catálogo: OK")

    # 3. Test de Drop Table
    with StorageManager() as sm:
        sm.drop_table("usuarios")
        assert sm.catalog.get_table_info("usuarios") is None
        
        # Archivo físico eliminado
        assert not os.path.exists("usuarios.dat")

    limpiar_entorno()
    print("Eliminación de tablas (Drop): OK")

if __name__ == "__main__":
    print("=== PROBANDO STORAGE MANAGER ===")
    test_storage_manager_lifecycle()
    print("¡Todas las pruebas del StorageManager pasaron con éxito!")