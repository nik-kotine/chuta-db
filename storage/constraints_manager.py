class IntegrityError(Exception):
    """Excepción lanzada cuando se viola una restricción de integridad."""
    pass


class ConstraintsManager:
    """
    Gestiona y valida restricciones avanzadas: PRIMARY KEY, NOT NULL, UNIQUE y CHECK.
    """
    def __init__(
        self, 
        table, 
        not_null_columns: list[int] = None, 
        unique_columns: list[int] = None, 
        check_constraints: list[callable] = None
    ):
        self.table = table
        self.primary_key_index = table.key_index
        self.not_null_columns = not_null_columns or []
        self.unique_columns = unique_columns or []
        self.check_constraints = check_constraints or []

    def validate_insert(self, values: list):
        """
        Valida todas las reglas de restricciones antes de permitir la inserción.
        Lanza IntegrityError si se infringe alguna política.
        """
        # Validar la longitud del esquema
        if len(values) != len(self.table.schema):
            raise ValueError(
                f"La tabla '{self.table.name}' espera {len(self.table.schema)} valores, recibió {len(values)}"
            )

        # Primary Key: Implica NOT NULL estricto y UNICIDAD global
        pk_value = values[self.primary_key_index]
        if pk_value is None:
            raise IntegrityError(
                f"Violación de integridad: La clave primaria (columna {self.primary_key_index}) no puede ser NULL."
            )
        
        existing_pk = self.table.search_by_key(pk_value)
        if existing_pk and len(existing_pk) > 0:
            raise IntegrityError(
                f"Violación de clave primaria: Ya existe un registro con la llave '{pk_value}' en '{self.table.name}'."
            )

        # Restricciones NOT NULL en columnas configuradas
        for col_idx in self.not_null_columns:
            if values[col_idx] is None:
                raise IntegrityError(
                    f"Violación NOT NULL: La columna en el índice {col_idx} es obligatoria y no puede ser nula."
                )

        # Restricciones UNIQUE en columnas adicionales
        for col_idx in self.unique_columns:
            val = values[col_idx]
            if val is not None:
                # Escaneamos los registros vivos para comprobar unicidad
                for _, existing_params in self.table.scan():
                    if existing_params[col_idx] == val:
                        raise IntegrityError(
                            f"Violación UNIQUE: El valor '{val}' ya está registrado en la columna {col_idx}."
                        )

        # Restricciones CHECK 
        for check_fn in self.check_constraints:
            if not check_fn(values):
                raise IntegrityError(
                    "Violación CHECK: Los datos proporcionados no cumplen con la regla de validación de la tabla."
                )

        return True