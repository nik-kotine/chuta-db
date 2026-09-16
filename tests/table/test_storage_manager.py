import os
from storage.storage_manager import StorageManager

def limpiar_entorno(db_name="test_db"):
    cat_file = f"{db_name}_catalog.json"
    if os.path.exists(cat_file):
        os.remove(cat_file)

def test_storage_manager_lifecycle():
    db_name = "test_db"
    limpiar_entorno(db_name)

    # 1. Inicializar StorageManager
    with StorageManager(db_name=db_name) as sm:
        
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
    with StorageManager(db_name=db_name) as sm_reloaded:
        assert "usuarios" in sm_reloaded.catalog
        assert "productos" in sm_reloaded.catalog
        
        # Reabrir la tabla de usuarios y verificar datos
        t_usr = sm_reloaded.open_table("usuarios")
        records = list(t_usr.scan())
        assert len(records) == 2

    print("Persistencia del catálogo: OK")

    # 3. Test de Drop Table
    with StorageManager(db_name=db_name) as sm:
        sm.drop_table("usuarios")
        assert "usuarios" not in sm.catalog
        
        # Archivo físico eliminado
        assert not os.path.exists("usuarios.dat")

    limpiar_entorno(db_name)
    print("Eliminación de tablas (Drop): OK")

if __name__ == "__main__":
    print("=== PROBANDO STORAGE MANAGER ===")
    test_storage_manager_lifecycle()
    print("¡Todas las pruebas del StorageManager pasaron con éxito!")