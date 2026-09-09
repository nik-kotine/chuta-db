import struct

PAGE_SIZE = 4096
HEADER_FORMAT = ">IHH" # (page_id, slot_count, free_space_high)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

SLOT_FORMAT = ">HH" # (offset, length)
SLOT_SIZE = struct.calcsize(SLOT_FORMAT)

class SlottedPage:
    def __init__(self, page_id: int, data: bytearray = None):
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            self.page_id = page_id
            self.slot_count = 0
            self.free_space_high = PAGE_SIZE # porque está vacía
            self.save_header()
        else:
            self.data = data
            self.load_header()

    def save_header(self):
        struct.pack_into(HEADER_FORMAT, self.data, 0, self.page_id, self.slot_count, self.free_space_high)

    def load_header(self):
        self.page_id, self.slot_count, self.free_space_high = struct.unpack_from(HEADER_FORMAT, self.data, 0)

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

        target_slot_id = -1
        needed_space = record_len

        for i in range(self.slot_count):
            offset, length = self.get_slot(i)
            if length == 0: # slot vacio/reutilizable
                target_slot_id = i
                break

        if target_slot_id == -1: # no encontramos uno reutlizable
            needed_space += SLOT_SIZE

        if self.free_space_bytes < needed_space: # si no hay espacio
            self.defragment() # intentamos compactar

        if self.free_space_bytes < needed_space:
            return -1 # la pag esta llena

        new_offset = self.free_space_high - record_len # retrocedemos el espacio
        self.data[new_offset : self.free_space_high] = record_data # en el anterior espacio libre escribimos la data
        self.free_space_high = new_offset

        if target_slot_id != -1: # reescribimos el slot
            self.set_slot(target_slot_id,new_offset,record_len)
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

        self.set_slot(slot_id, 0, 0)
        self.save_header()
        return True

    
        