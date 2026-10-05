import os
from indexes.b_plus_unclustered import BPlusTreeUnclustered
from indexes.b_plus_unclustered_sequential import BPlusTreeUnclusteredSequential
from indexes.b_plus_clustered import BPlusTreeClustered
from indexes.bitmap_index import BitmapIndex
from indexes.r_tree import RTree
from spatial.geometry import Point as GeoPoint
from indexes.extendible_hash import HashIndex
from indexes.extendible_hash import PAGE_SIZE as HASH_PAGE_SIZE
from storage.buffer_manager import BufferManager
from storage.file_manager import FileManager
from storage.schema_catalog import SchemaCatalog
from storage.table import Table


HASH_FILE_HEADER_SIZE = 16
HASH_DEFAULT_BUCKET_SIZE = 16
HASH_DEFAULT_DEPTH = 1
HASH_DEFAULT_SEED = 0


class IndexManager:
    """
    Administra la creación, apertura y eliminación de índices B+, hash y bitmap en la base de datos.
    """
    def __init__(self, catalog: SchemaCatalog):
        self.catalog = catalog
        self.open_indexes: dict[str, object] = {}

    def create_unclustered_index(
        self,
        index_name: str,
        table: Table,
        column_index: int,
        column_name: str = "col"
    ) -> BPlusTreeUnclustered | BPlusTreeUnclusteredSequential:
        """
        Crea un índice secundario no agrupado sobre una columna.
        Si la tabla ya tiene datos, se encarga de leerlos e indexarlos.

        Sobre una tabla Heap usa BPlusTreeUnclustered (los RID del heap son
        estables). Sobre una tabla Sequential usa BPlusTreeUnclusteredSequential,
        que se reconstruye solo cuando el archivo se reorganiza (ver su
        docstring): un SequentialFile reubica filas vivas al reorganizarse,
        asi que sus RID no son estables.
        """
        index_filename = f"{index_name}.idx"

        if table.file_type == "sequential":
            index = BPlusTreeUnclusteredSequential(
                index_filename=index_filename,
                sequential_file=table.data_file,
                schema=table.schema,
                column_index=column_index,
            )
        else:
            index = BPlusTreeUnclustered(
                index_filename=index_filename,
                heap_file=table.data_file,
                schema=table.schema
            )

        for rid, record_params in table.scan():
            key = record_params[column_index]
            index._insert_ref(key, rid)

        self.catalog.register_index(
            index_name=index_name,
            table_name=table.name,
            column_name=column_name,
            column_index=column_index,
            index_type="unclustered"
        )

        self.open_indexes[index_name] = index
        table.attach_index(column_index, index)
        return index

    def create_clustered_index(
        self, 
        index_name: str, 
        table: Table, 
        column_name: str = "pk"
    ) -> BPlusTreeClustered:
        """
        Crea un índice primario agrupado (BPlusTreeClustered).
        Solo se puede aplicar a tablas del tipo 'sequential'.
        """
        if table.file_type != "sequential":
            raise ValueError("Un índice Clustered solo puede crearse sobre una tabla SequentialFile.")

        index_filename = f"{index_name}.idx"
        index = BPlusTreeClustered(index_filename, table.data_file)
        
        # Sincronizamos el índice con los datos que ya tenga el archivo
        index._reindex()

        self.catalog.register_index(
            index_name=index_name,
            table_name=table.name,
            column_name=column_name,
            column_index=table.key_index, # Siempre usa la clave primaria
            index_type="clustered"
        )

        self.open_indexes[index_name] = index
        table.set_clustered_index(index)
        return index

    def create_hash_index(
        self, 
        index_name: str, 
        table: Table, 
        column_index: int, 
        column_name: str = "col"
    ) -> HashIndex:
        """
        Crea un índice hash no agrupado (extendible hashing) sobre una
        columna. Solo tiene sentido sobre tablas Heap (igual que el B+
        no agrupado). Si la tabla ya tiene datos, se encarga de leerlos
        e indexarlos.
        """
        self.catalog.register_index(
            index_name=index_name,
            table_name=table.name,
            column_name=column_name,
            column_index=column_index,
            index_type="hash"
        )
        return self._build_hash_index(index_name, table, column_index, column_name)

    def _hash_key_config(self, table: Table, column_index: int):
        """
        Traduce el tipo de la columna a la configuración del KVSerializer
        del índice hash: formato struct de la clave y si es variable.
        """
        col_type = table.schema[column_index].strip().lower()
        if col_type in ("int", "integer", "int4", "smallint", "int2", "date"):
            return (">i", False)
        if col_type in ("bigint", "int8"):
            return (">q", False)
        if col_type in ("float", "real", "float4", "double precision", "float8"):
            # ">d" en vez de ">f": el hash y la igualdad trabajan sobre el
            # float de Python, y con 8 bytes no se pierde precisión extra.
            return (">d", False)
        if col_type in ("bool", "boolean"):
            return (">?", False)
        if col_type.startswith("varchar") or col_type in ("text", "string", "str"):
            return ("s", True)
        raise ValueError(
            f"El tipo '{table.schema[column_index]}' no es soportado por "
            f"el índice hash sobre '{table.name}'"
        )

    def _build_hash_index(
        self, 
        index_name: str, 
        table: Table, 
        column_index: int, 
        column_name: str
    ) -> HashIndex:
        """
        Construye el índice hash desde cero y lo enlaza a la tabla.

        El hash no tiene persistencia de páginas (a diferencia del B+):
        siempre se reconstruye barriendo la tabla, así que se descartan
        páginas viejas en caché y se truncate el archivo .idx antes de
        arrancar, para que una sesión anterior no deje basura.
        """
        index_filename = f"{index_name}.idx"
        key_format, key_variable = self._hash_key_config(table, column_index)

        fm = FileManager(index_filename, HASH_PAGE_SIZE, HASH_FILE_HEADER_SIZE)
        bm = BufferManager(fm)
        bm.invalidate_all(fm)
        fm.truncate(fm.file_header_size)

        index = HashIndex(
            table_name=table.name,
            column_name=column_name,
            key_format=key_format,
            key_variable=key_variable,
            buffer_manager=bm,
            max_bucket_size=HASH_DEFAULT_BUCKET_SIZE,
            depth=HASH_DEFAULT_DEPTH,
            seed=HASH_DEFAULT_SEED,
            file_manager=fm
        )

        # Poblar el índice con los registros existentes en la tabla
        for rid, record_params in table.scan():
            index._insert_ref(record_params[column_index], rid)

        self.open_indexes[index_name] = index
        table.attach_index(column_index, index)
        return index

    def create_bitmap_index(
        self,
        index_name: str,
        table: Table,
        column_index: int,
        column_name: str = "col"
    ) -> BitmapIndex:
        """
        Crea un índice de bitmap no agrupado sobre una columna. Solo tiene
        sentido sobre tablas Heap (igual que el B+ no agrupado): el bitmap
        apunta a filas del heap, no a un archivo ordenado. Si la tabla ya
        tiene datos, se encarga de leerlos e indexarlos.
        """
        if table.file_type != "heap":
            raise ValueError(
                "Un índice BITMAP solo puede crearse sobre una tabla Heap."
            )

        self.catalog.register_index(
            index_name=index_name,
            table_name=table.name,
            column_name=column_name,
            column_index=column_index,
            index_type="bitmap"
        )
        return self._build_bitmap_index(index_name, table, column_index, column_name)

    def _build_bitmap_index(
        self,
        index_name: str,
        table: Table,
        column_index: int,
        column_name: str
    ) -> BitmapIndex:
        """
        Construye el índice de bitmap desde cero y lo enlaza a la tabla.

        A diferencia del hash, el bitmap sí persiste sus páginas, pero al
        crearlo de cero se descarta el archivo anterior para no arrastrar la
        basura de una sesión vieja.
        """
        index_filename = f"{index_name}.idx"
        if os.path.exists(index_filename):
            os.remove(index_filename)

        index = BitmapIndex(index_filename, table.name, column_name)

        for rid, record_params in table.scan():
            index._insert_ref(record_params[column_index], rid)

        self.open_indexes[index_name] = index
        table.attach_index(column_index, index)
        return index

    # ---------------- indice espacial (R-Tree) ----------------

    def create_rtree_index(
        self,
        index_name: str,
        table: Table,
        column_index: int,
        column_name: str = "col"
    ) -> "RTreeSecondaryIndex":
        """
        Crea un índice espacial R-Tree sobre una columna de tipo point.

        Es siempre no agrupado: a diferencia del B+, un R-Tree no define
        un orden total de los registros, así que no puede imponer el orden
        físico del archivo de datos.
        """
        col_type = table.schema[column_index].strip().lower()
        if col_type != "point":
            raise ValueError(
                f"Un índice R-Tree solo aplica a columnas POINT; "
                f"'{column_name}' es de tipo '{table.schema[column_index]}'"
            )

        self.catalog.register_index(
            index_name=index_name,
            table_name=table.name,
            column_name=column_name,
            column_index=column_index,
            index_type="rtree"
        )
        return self._build_rtree_index(index_name, table, column_index, column_name)

    def _build_rtree_index(
        self,
        index_name: str,
        table: Table,
        column_index: int,
        column_name: str
    ) -> "RTreeSecondaryIndex":
        """
        Construye el R-Tree desde cero y lo enlaza a la tabla. Se descarta
        el archivo anterior para no arrastrar entradas de una sesión vieja.
        """
        index_filename = f"{index_name}.idx"
        if os.path.exists(index_filename):
            os.remove(index_filename)

        index = RTreeSecondaryIndex(index_filename, table.data_file)

        for rid, record_params in table.scan():
            index._insert_ref(record_params[column_index], rid)

        self.open_indexes[index_name] = index
        table.attach_index(column_index, index)
        return index

    def load_indexes_for_table(self, table: Table):
        """
        Carga los índices registrados en el catálogo para una tabla dada
        y los enlaza a la instancia de Table para que se actualicen automáticamente.
        """
        registered = self.catalog.get_table_indexes(table.name)
        for meta in registered:
            index_name = meta["index_name"]
            col_idx = meta["column_index"]
            idx_type = meta["index_type"]
            index_filename = f"{index_name}.idx"

            if index_name not in self.open_indexes:
                if idx_type == "unclustered":
                    if table.file_type == "sequential":
                        idx = BPlusTreeUnclusteredSequential(
                            index_filename, table.data_file, table.schema, col_idx
                        )
                    else:
                        idx = BPlusTreeUnclustered(index_filename, table.data_file, table.schema)
                    self.open_indexes[index_name] = idx
                    table.attach_index(col_idx, idx)

                elif idx_type == "clustered":
                    idx = BPlusTreeClustered(index_filename, table.data_file)
                    self.open_indexes[index_name] = idx
                    table.set_clustered_index(idx)

                elif idx_type == "hash":
                    # El hash no persiste sus páginas: se reconstruye
                    # desde la tabla en cada apertura.
                    self._build_hash_index(
                        index_name,
                        table,
                        col_idx,
                        meta["column_name"]
                    )

                elif idx_type == "rtree":
                    # El R-Tree persiste sus páginas, pero se reconstruye
                    # igual que el hash: así el índice queda consistente
                    # con la tabla aunque se hayan borrado filas con el
                    # índice cerrado.
                    self._build_rtree_index(
                        index_name, table, col_idx, meta["column_name"]
                    )

                elif idx_type == "bitmap":
                    # El bitmap sí persiste sus páginas: se reabre el archivo
                    # y el directorio clave -> (pagina, slot) se rearma solo.
                    idx = BitmapIndex(
                        index_filename, table.name, meta["column_name"]
                    )
                    self.open_indexes[index_name] = idx
                    table.attach_index(col_idx, idx)

    def drop_index(self, index_name: str):
        """Elimina un índice y su archivo físico .idx."""
        if index_name in self.open_indexes:
            self.open_indexes[index_name].close()
            del self.open_indexes[index_name]

        self.catalog.drop_index_info(index_name)

        filename = f"{index_name}.idx"
        if os.path.exists(filename):
            os.remove(filename)


    def close(self):
        """Cierra todos los índices abiertos."""
        for idx in self.open_indexes.values():
            idx.close()
        self.open_indexes.clear()

class RTreeSecondaryIndex(RTree):
    """
    Adaptador del R-Tree para usarlo como índice secundario de una Table.

    RTree.insert(point, params) escribe el registro en el heap y después
    lo indexa: sirve para un índice primario. Como índice secundario el
    registro ya existe, así que hay que indexar el RID que la tabla
    acaba de obtener, sin volver a escribir nada. Eso es exactamente lo
    que hace _insert_entry_into_tree.

    Table llama a _insert_ref / delete_ref sobre todos sus índices
    secundarios, igual que al bitmap o al hash.
    """

    @staticmethod
    def _a_punto(key) -> GeoPoint:
        if isinstance(key, GeoPoint):
            return key
        if isinstance(key, (tuple, list)) and len(key) == 2:
            return GeoPoint(key[0], key[1])
        raise TypeError(
            f"El índice R-Tree espera un punto (x, y); se recibió {key!r}"
        )

    def _delete_record(self, point, ref) -> bool:
        """
        Como índice secundario NO se toca el archivo de datos.

        RTree._delete_record borra el registro del heap, porque el R-Tree
        primario es dueño de la fila. Acá la fila es de la Table, que ya
        la borró antes de avisarle a sus índices: volver a borrarla
        eliminaría una fila ajena.
        """
        return True

    def _insert_ref(self, key, rid):
        self._insert_entry_into_tree(self._a_punto(key), rid)

    def delete_ref(self, key, rid):
        """
        Quita del árbol la entrada (punto, rid), no la primera que tenga
        ese punto.

        RTreeBase.delete busca solo por punto, así que con coordenadas
        repetidas podía sacar la entrada de otra fila y dejar el índice
        apuntando a un RID ya borrado.
        """
        punto = self._a_punto(key)
        path = self._buscar_hoja_con_ref(punto, rid)
        if path is None:
            return False

        hoja = path[-1]
        idx = next(i for i, e in enumerate(hoja.entries)
                   if e.point == punto and e.ref == rid)
        hoja.delete_at(idx)
        self._save_node(hoja)
        self._condense_tree(path)
        return True

    def _buscar_hoja_con_ref(self, point, ref, path=None):
        """
        Camino hasta la hoja que contiene exactamente (point, ref).

        A diferencia de _search_leaf_for_point, no se detiene en la
        primera hoja que tenga el punto: con duplicados, las entradas
        pueden haber quedado repartidas en varias hojas.
        """
        if path is None:
            path = [self._load_node(self.root_page_id)]

        nodo = path[-1]
        if nodo.is_leaf:
            for entry in nodo.entries:
                if entry.point == point and entry.ref == ref:
                    return path
            return None

        for entry in nodo.entries:
            if entry.mbr.contains_point(point):
                hijo = self._load_node(entry.ref)
                encontrado = self._buscar_hoja_con_ref(point, ref, path + [hijo])
                if encontrado is not None:
                    return encontrado
        return None
