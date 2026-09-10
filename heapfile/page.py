import struct

PAGE_SIZE = 4096
HEADER_FORMAT = ">IHHH" # (page_id, slot_count, free_space_high, first_free_slot)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

SLOT_FORMAT = ">HH" # (offset, length)
SLOT_SIZE = struct.calcsize(SLOT_FORMAT)

NULL_SLOT = 0xFFFF # marca "no hay ningun slot muerto que reciclar" en la free list

class SlottedPage:
    def __init__(self, page_id: int, data: bytearray = None):
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            self.page_id = page_id
            self.slot_count = 0
            self.free_space_high = PAGE_SIZE # porque está vacía
            self.first_free_slot = NULL_SLOT # todavia no hay ningun muerto
            self.save_header()
        else:
            self.data = data
            self.load_header()

    def save_header(self):
        struct.pack_into(HEADER_FORMAT, self.data, 0, self.page_id, self.slot_count, self.free_space_high, self.first_free_slot)

    def load_header(self):
        self.page_id, self.slot_count, self.free_space_high, self.first_free_slot = struct.unpack_from(HEADER_FORMAT, self.data, 0)

    @property
    def free_space_low(self): 
        return HEADER_SIZE + (self.slot_count * SLOT_SIZE) # El fin del espacio de slots
    
    @property
    def free_space_bytes(self):
        return self.free_space_high - self.free_space_low # El espacio libre en el centro de la pag

    def get_slot(self, slot_id: int):
        slot_offset = HEADER_SIZE + (slot_id*SLOT_SIZE)
        return struct.unpack_from(SLOT_FORMAT, self.data, slot_offset)

    def set_slot(self, slot_id: int, offset: int, length: int):
        slot_offset = HEADER_SIZE + (slot_id*SLOT_SIZE)
        struct.pack_into(SLOT_FORMAT, self.data, slot_offset, offset, length)

    # Operaciones: insert, get, delete, defragment

    def defragment(self):
        active_records = []
        for i in range(self.slot_count):
            offset, length = self.get_slot(i)
            if length > 0:
                record = bytes(self.data[offset:offset+length])
                active_records.append((i, record)) # slot_id, bytes de record

        self.free_space_high = PAGE_SIZE
        for slot_id, record in active_records:
            record_len = len(record)
            new_offset = self.free_space_high - record_len
            self.data[new_offset : self.free_space_high] = record
            self.free_space_high = new_offset
            self.set_slot(slot_id, new_offset, record_len)

        l = self.free_space_low
        h = self.free_space_high
        self.data[l:h] = b"\x00" * (h-l) # limpiamos el espacio del centro

        self.save_header()

    def insert(self, record_data: bytes):
        record_len = len(record_data)

        # solo miramos quien es el primer muerto, todavia no lo consumimos
        # de la free list -- si el insert termina fallando por falta de
        # espacio, no queremos habernos comido un slot reciclable por nada
        target_slot_id = self.first_free_slot if self.first_free_slot != NULL_SLOT else -1
        needed_space = record_len

        if target_slot_id == -1: # no hay ningun muerto reciclable
            needed_space += SLOT_SIZE

        if self.free_space_bytes < needed_space: # si no hay espacio
            self.defragment() # intentamos compactar

        if self.free_space_bytes < needed_space:
            return -1 # la pag esta llena

        new_offset = self.free_space_high - record_len # retrocedemos el espacio
        self.data[new_offset : self.free_space_high] = record_data # en el anterior espacio libre escribimos la data
        self.free_space_high = new_offset

        if target_slot_id != -1: # reciclamos el primero de la free list
            next_free, _ = self.get_slot(target_slot_id) # su offset guardaba el puntero al siguiente muerto
            self.first_free_slot = next_free # recien ahora avanzamos la free list
            self.set_slot(target_slot_id, new_offset, record_len)
            res_slot_id = target_slot_id
        else: # nuevo slot
            self.set_slot(self.slot_count, new_offset, record_len)
            res_slot_id = self.slot_count
            self.slot_count += 1

        self.save_header()
        return res_slot_id

    def get_record(self, slot_id: int):
        if slot_id<0 or slot_id>=self.slot_count:
            return None

        offset, length = self.get_slot(slot_id)
        if length == 0: # eliminado
            return None

        return bytes(self.data[offset : offset+length])

    def delete_record(self, slot_id:int):
        if slot_id<0 or slot_id>=self.slot_count:
            return False
        
        offset, length = self.get_slot(slot_id)
        if length == 0: # eliminado
            return False # ya estaba eliminado

        # lo enganchamos a la cabeza de la free list: el offset (ya no
        # sirve para ubicar datos) pasa a guardar quien era el primer
        # muerto hasta ahora, y este slot se vuelve el nuevo primero
        self.set_slot(slot_id, self.first_free_slot, 0)
        self.first_free_slot = slot_id
        self.save_header()
        return True

    
        