#!/usr/bin/env python3
# 
# Cross Platform and Multi Architecture Advanced Binary Emulation Framework
#

import ctypes

from unicorn.unicorn import UcError
from qiling.hw.peripheral import QlPeripheral


class NVIC(QlPeripheral):
    class Type(ctypes.Structure):
        _fields_ = [
            ('ISER'     , ctypes.c_uint32 * 8),
            ('RESERVED0', ctypes.c_uint32 * 24),
            ('ICER'     , ctypes.c_uint32 * 8),
            ('RESERVED1', ctypes.c_uint32 * 24),
            ('ISPR'     , ctypes.c_uint32 * 8),
            ('RESERVED2', ctypes.c_uint32 * 24),
            ('ICPR'     , ctypes.c_uint32 * 8),
            ('RESERVED3', ctypes.c_uint32 * 24),
            ('IABR'     , ctypes.c_uint32 * 8),
            ('RESERVED4', ctypes.c_uint32 * 56),
            ('IPR'      , ctypes.c_uint8  * 240),
            ('RESERVED5', ctypes.c_uint32 * 644),
            ('STIR'     , ctypes.c_uint32 * 8),
        ]

    def __init__(self, ql, tag):
        super().__init__(ql, tag)
        
        # reference:
        # https://www.youtube.com/watch?v=uFBNf7F3l60
        # https://developer.arm.com/documentation/ddi0439/b/Nested-Vectored-Interrupt-Controller 
        
        NVIC_Type = type(self).Type
        self.nvic = NVIC_Type()

        ## The max number of interrupt request
        self.IRQN_MAX = NVIC_Type.ISER.size * 8

        ## The ISER unit size
        self.MASK     = self.IRQN_MAX // len(self.nvic.ISER) - 1
        self.OFFSET   = self.MASK.bit_length()

        ## special write behavior
        self.triggers = [
            (NVIC_Type.ISER, self.enable),
            (NVIC_Type.ICER, self.disable),
            (NVIC_Type.ISPR, self.set_pending),
            (NVIC_Type.ICPR, self.clear_pending),
        ]

        self.intrs = []
        self.reg_context = ['xpsr', 'pc', 'lr', 'r12', 'r3', 'r2', 'r1', 'r0']

    def enable(self, IRQn):
        if IRQn >= 0:
            self.nvic.ISER[IRQn >> self.OFFSET] |= 1 << (IRQn & self.MASK)
            self.nvic.ICER[IRQn >> self.OFFSET] |= 1 << (IRQn & self.MASK)
        else:
            self.ql.hw.sysctrl.enable(IRQn)

    def disable(self, IRQn):
        if IRQn >= 0:
            self.nvic.ISER[IRQn >> self.OFFSET] &= self.MASK ^ (1 << (IRQn & self.MASK))
            self.nvic.ICER[IRQn >> self.OFFSET] &= self.MASK ^ (1 << (IRQn & self.MASK))
        else:
            self.ql.hw.sysctrl.disable(IRQn)

    def get_enable(self, IRQn):
        if IRQn >= 0:
            return (self.nvic.ISER[IRQn >> self.OFFSET] >> (IRQn & self.MASK)) & 1
        else:
            return self.ql.hw.sysctrl.get_enable(IRQn)

    def set_pending(self, IRQn):
        if IRQn >= 0:
            self.nvic.ISPR[IRQn >> self.OFFSET] |= 1 << (IRQn & self.MASK)
            self.nvic.ICPR[IRQn >> self.OFFSET] |= 1 << (IRQn & self.MASK)
        else:
            self.ql.hw.sysctrl.set_pending(IRQn)
        
        if self.get_enable(IRQn):
            self.intrs.append(IRQn)

    def clear_pending(self, IRQn):
        if IRQn >= 0:
            self.nvic.ISPR[IRQn >> self.OFFSET] &= self.MASK ^ (1 << (IRQn & self.MASK))
            self.nvic.ICPR[IRQn >> self.OFFSET] &= self.MASK ^ (1 << (IRQn & self.MASK))
        else:
            self.ql.hw.sysctrl.clear_pending(IRQn)

    def get_pending(self, IRQn):
        if IRQn >= 0:
            return (self.nvic.ISER[IRQn >> self.OFFSET] >> (IRQn & self.MASK)) & 1
        else:
            return self.ql.hw.sysctrl.get_pending(IRQn)

    def get_priority(self, IRQn):
        return 0

    def save_regs(self):
        for reg in self.reg_context:
            val = self.ql.reg.read(reg)
            self.ql.arch.stack_push(val)

    def restore_regs(self):
        for reg in reversed(self.reg_context):
            val = self.ql.arch.stack_pop()
            self.ql.reg.write(reg, val)

    def handle_interupt(self, offset):
        self.ql.log.debug('Enter into interrupt')
        address = self.ql.arch.boot_space + offset
        entry = self.ql.mem.read_ptr(address)

        ## TODO: handle other exceptionreturn behavior
        EXC_RETURN = 0xFFFFFFF9

        self.ql.reg.write('pc', entry)
        self.ql.reg.write('lr', EXC_RETURN)

        try:
            self.ql.emu_start(self.ql.arch.get_pc(), EXC_RETURN)
        except UcError:
            pass

        self.ql.log.debug('Exit from interrupt')

    def step(self):
        if not self.intrs:
            return

        self.intrs.sort(key=lambda x: self.get_priority(x))
        self.save_regs()
                
        for IRQn in self.intrs:
            self.clear_pending(IRQn)
            self.handle_interupt((IRQn + 16) << 2)            

        self.intrs.clear()
        self.restore_regs()

    def read(self, offset, size):
        buf = ctypes.create_string_buffer(size)
        ctypes.memmove(buf, ctypes.addressof(self.nvic) + offset, size)
        return int.from_bytes(buf.raw, byteorder='little', signed=False)

    def write(self, offset, size, value):
        def write_byte(ofs, byte):
            for var, func in self.trigger:
                if var.offset <= ofs < var.offset + var.size:
                    for i in range(8):
                        if (byte >> i) & 1:
                            func(i + ofs - var.offset)
                    break
            else:
                ctypes.memmove(ctypes.addressof(self.nvic) + ofs, byte, 1)

        for ofs in range(offset, offset + size):
            write_byte(ofs, value & 0xff)
            value >>= 8