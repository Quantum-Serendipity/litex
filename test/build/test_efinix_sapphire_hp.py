#
# This file is part of LiteX.
#
# Copyright (c) 2026 Colin Rushton <colin.rushton@quantumserendipitysoftware.com>
# SPDX-License-Identifier: BSD-2-Clause

#
# PoC unit tests for the EfinixSapphireHP hardcore CPU wrapper (Ti375 hardened quad-core RISC-V).
# These exercise the RTL/bus/integration side, which is complete against mainline LiteX. The
# vendor-specific Efinity peri.xml emission is placeholdered in the wrapper (marked TODO (Efinity))
# and is NOT exercised here — it is validated once the Efinity toolchain is available.
#

import unittest

from migen import *
from migen.fhdl.verilog import convert

from litex.soc.cores.cpu import CPUS
from litex.soc.cores.cpu.efinix_sapphire_hp import EfinixSapphireHP


class _MockPlatform:
    # do_finalize() guards on platform.toolchain.ifacewriter, so a bare object skips peri.xml emission.
    pass


class TestEfinixSapphireHP(unittest.TestCase):
    def test_registration(self):
        # collect_cpus() only registers a class whose name lowercase-stripped == dir name stripped.
        self.assertIn("efinix_sapphire_hp", CPUS)
        self.assertIs(CPUS["efinix_sapphire_hp"], EfinixSapphireHP)

    def test_class_attributes(self):
        cpu = EfinixSapphireHP(_MockPlatform(), "standard")
        self.assertEqual(cpu.category, "hardcore")
        self.assertEqual(cpu.family, "riscv")
        self.assertEqual(cpu.data_width, 32)            # RV32 hard core.
        self.assertEqual(cpu.endianness, "little")
        self.assertEqual(cpu.reset_address, 0xf900_0000)
        # Ownership inversion: HRB owns boot + DRAM.
        self.assertFalse(cpu.integrated_rom_supported)
        self.assertFalse(cpu.reset_address_check)
        self.assertTrue(cpu.csr_decode)
        self.assertEqual(cpu.mem_map["csr"], 0xe800_0000)

    def test_bus_shape(self):
        cpu = EfinixSapphireHP(_MockPlatform(), "standard")
        # AXIA_* -> single 32-bit periph master; IO_DDRMASTERS_0 -> 128-bit dma_bus; no LiteDRAM bus.
        self.assertEqual(len(cpu.periph_buses), 1)
        self.assertEqual(cpu.pbus.data_width, 32)
        self.assertEqual(cpu.memory_buses, [])
        self.assertTrue(hasattr(cpu, "dma_bus"))
        self.assertEqual(cpu.dma_bus.data_width, 128)

    def test_set_reset_address(self):
        cpu = EfinixSapphireHP(_MockPlatform(), "standard")
        cpu.set_reset_address(0xf900_0000)              # The silicon vector: ok.
        with self.assertRaises(AssertionError):         # Anything else: rejected.
            cpu.set_reset_address(0x1234_5678)

    def test_verilog_elaboration(self):
        class Top(Module):
            def __init__(self):
                self.clock_domains.cd_sys = ClockDomain()
                self.submodules.cpu = EfinixSapphireHP(_MockPlatform(), "standard")

        v = convert(Top()).main_source
        # Black-box hard-block Instance + the three port groups must be present and wired.
        for needle in (
            "efx_sapphire_hp_soc",
            "AXIA_awaddr", "AXIA_rdata",
            "IO_DDRMASTERS_0_awaddr", "IO_DDRMASTERS_0_rdata",
            "USERINTERRUPT",
        ):
            self.assertIn(needle, v)


if __name__ == "__main__":
    unittest.main()
