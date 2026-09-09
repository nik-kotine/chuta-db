## Planteamiento
- Longitud Fija
- Longitud Variable

La idea para el diseño es usar una sola estructura de página (slotted page) de tal forma que, sin importar el tipo de registro que se inserte, la página no necesite diferenciar si es variable o fija y solo guarde el `offset` y el `length` en el dir de slots.

Lo q si tenemos que usar es una estrategia para delimitar los regs varibles, usamos entonces una cabecera con offsets y se tendría un acceso directo con el slotID.

