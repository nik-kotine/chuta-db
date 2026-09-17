from storage.formats.serializers.fixed_length_serializer import FixedLengthRecordSerializer

def test_fixed_length_record_format():
    # Formato: Int (4 bytes), Int (4 bytes), Double (8 bytes)
    fmt = ["int", "int", "double precision"]
    serializer = FixedLengthRecordSerializer(fmt)

    datos_originales = [42, 100, 3.14159]

    # 1. Probar encode
    encoded_bytes = serializer.encode(datos_originales)
    assert isinstance(encoded_bytes, bytes)
    assert len(encoded_bytes) == serializer.record_size

    # 2. Probar decode
    datos_recuperados = serializer.decode(encoded_bytes)
    assert list(datos_recuperados) == datos_originales

    print("OK: FixedLengthRecordSerializer serializa y deserializa correctamente")

if __name__ == "__main__":
    print("Corriendo tests de RecordFormat...")
    test_fixed_length_record_format()
    print("¡Todos los tests de RecordFormat pasaron!")