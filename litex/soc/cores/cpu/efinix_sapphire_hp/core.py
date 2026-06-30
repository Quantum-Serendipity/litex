#
# This file is part of LiteX.
#
# Copyright (c) 2026 Colin Rushton <colin.rushton@quantumserendipitysoftware.com>
# SPDX-License-Identifier: BSD-2-Clause

#
# LiteX CPU wrapper for the Efinix Titanium Ti375 *hardened* quad-core RISC-V block
# (the "Sapphire HP" HRB/SLB — distinct from the *soft* VexiiRiscv Sapphire SoC).
#
# This is a PoC wrapper, modelled on the in-tree `gowin_ae350` (hard RISC-V in FPGA fabric) and
# `zynqmp`/`zynq7000` (hard processing system + fabric, "ownership inversion") wrappers.
#
# Ownership inversion (see zynqmp/zynq7000): the HRB boots its OWN OpenSBI/U-Boot/Linux from its own
# media, so LiteX is demoted from system-integrator to peripheral-provider. We declare that purely via
# attribute "absences": no LiteX BIOS/ROM (`integrated_rom_supported=False`), no LiteDRAM
# (`memory_buses=[]`), and a vestigial/unchecked reset address (the HRB's silicon boot vector).
#
# Bus model:
#   - `AXIA_*`         : 32-bit  AXI4 peripheral master  -> LiteX `periph_bus` (LiteX peripherals/CSRs
#                        become slaves in the 0xE800_0000 window; CSR bridge = AXILite2CSR).
#   - `IO_DDRMASTERS_0`: 128-bit AXI4 coherent port      -> LiteX `dma_bus` (fabric DMA masters inject
#                        coherent traffic into the HRB memory subsystem).
#   - `USERINTERRUPT`  : fabric peripheral IRQs -> HRB.
#
# !!! VALIDATION GATING !!!
# The exact Efinity peri.xml schema for the Ti375 HRB, the precise hard-block port names/widths, the
# `IO_DDRMASTERS_0` direction, and which Efinity database (Interface Designer vs IP Manager) owns the
# HRB are NOT yet confirmed (need an Efinity install + Ti375 docs). Every such spot is marked `TODO
# (Efinity)`. The RTL/bus/integration side is complete and idiomatic; only the vendor-specific emission
# detail is placeholdered. This builds and elaborates against mainline LiteX so it can be reviewed and
# unit-tested now, then validated/fixed once the toolchain is in hand.
#

from migen import *

from litex.gen import *

from litex.soc.interconnect import axi
from litex.soc.interconnect.csr import *
from litex.soc.cores.cpu import CPU, CPU_GCC_TRIPLE_RISCV32

from litex.build.efinix import InterfaceWriterXMLBlock

# Efinix Sapphire HP constants ---------------------------------------------------------------------
#
# Defaults below are PoC placeholders — confirm against the Efinity-generated Ti375 Sapphire HP IP.

# AXIA_* peripheral master (HRB -> fabric).
AXIA_ADDR_WIDTH       = 32
AXIA_ID_WIDTH         = 8     # TODO (Efinity): confirm AXIA awid/arid width.

# IO_DDRMASTERS_0 coherent port (fabric -> HRB).
IO_DDR_DATA_WIDTH     = 128   # per spec: 128-bit coherent.
IO_DDR_ADDR_WIDTH     = 32    # TODO (Efinity): confirm (32/36/40-bit DDR address space).
IO_DDR_ID_WIDTH       = 8     # TODO (Efinity): confirm IO_DDRMASTERS id width.

# USERINTERRUPT[A..X] -> HRB IRQ inputs.
USER_INTERRUPT_COUNT  = 32    # TODO (Efinity): confirm number of USERINTERRUPT lines exposed.

# Hardened-block Verilog macro name emitted by the Efinity flow.
SAPPHIRE_HP_MACRO     = "efx_sapphire_hp_soc"  # TODO (Efinity): confirm generated module name.


# Efinix Sapphire HP peri.xml block ----------------------------------------------------------------
#
# The HRB must be registered with the Efinity Interface Designer so peri.xml *places* the hard block
# and *binds* its AXI masters/slaves + clocks/resets/IRQs to the fabric signal names we drive on the
# `Instance`. Per the efinix-build-backend study, the IP-Manager channel (`ipmwriter.py`) is dormant
# and writes the project `.xml` (not peri.xml), and peri.xml is regenerated wholesale every build
# (`design.create(overwrite=True)` + `design.save()`). The ONLY collision-safe channel is an
# `InterfaceWriterXMLBlock` appended to `ifacewriter.xml_blocks`: it injects into peri.xml AFTER
# `design.save()`, so it survives the overwrite. (`fix_xml` can post-tweak attributes if needed.)

class EfinixSapphireHPBlock(InterfaceWriterXMLBlock):
    """Inject the hardened Sapphire HP block into <name>.peri.xml after design.save().

    `generate(root, namespaces)` is called by `InterfaceWriter.generate_xml_blocks()` with the parsed
    peri.xml root and the `{"efxpt": ...}` namespace map.

    TODO (Efinity): the concrete <efxpt:...> element tag, attributes, and AXI/clock/IRQ child binding
    schema for the Ti375 hardened RISC-V are not yet known (need a reference peri.xml from Efinity).
    The structure below is a documented placeholder built from the HYPERRAM/DRAM block conventions;
    fill in the real schema during validation.
    """
    def generate(self, root, namespaces):
        import xml.etree.ElementTree as et

        efxpt = namespaces["efxpt"]
        # Efinity groups hard blocks under a top-level container; HYPERRAM uses <efxpt:hyper_ram_blocks>,
        # DDR uses <efxpt:ddr_blocks>, etc. The exact container/tag for the Sapphire HRB is TODO.
        container_tag = f"{{{efxpt}}}sapphire_hp_blocks"          # TODO (Efinity): real container tag.
        block_tag     = f"{{{efxpt}}}sapphire_hp"                 # TODO (Efinity): real block tag.

        container = root.find(container_tag)
        if container is None:
            container = et.SubElement(root, container_tag)

        block = et.SubElement(container, block_tag, name=self["name"])
        # Core configuration (drives the Efinity IP wizard tabs).
        for key, value in self["config"].items():
            block.set(key, str(value))
        # AXI / clock / reset / IRQ fabric bindings: map each hard-block interface to the top-level
        # fabric signal names LiteX drives on the Instance. TODO (Efinity): real child-element schema.
        for iface_name, signals in self["bindings"].items():
            iface = et.SubElement(block, f"{{{efxpt}}}interface", name=iface_name)
            for port, net in signals.items():
                et.SubElement(iface, f"{{{efxpt}}}signal", port=port, net=net)


# Efinix Sapphire HP -------------------------------------------------------------------------------

class EfinixSapphireHP(CPU):
    variants             = ["standard"]
    category             = "hardcore"
    family               = "riscv"
    name                 = "efinix_sapphire_hp"
    human_name           = "Efinix Sapphire HP (Ti375 hard quad-core RISC-V)"
    data_width           = 32                       # RV32 hard core (RV64GC is the *soft* VexiiRiscv).
    endianness           = "little"
    reset_address        = 0xf900_0000              # HRB silicon boot vector (per spec).
    gcc_triple           = CPU_GCC_TRIPLE_RISCV32
    linker_output_format = "elf32-littleriscv"
    nop                  = "nop"
    io_regions           = {
        # Origin, Length. Uncached MMIO window the HRB reaches LiteX CSRs/peripherals through (AXIA).
        0xe800_0000: 0x1000_0000,
    }

    # Ownership inversion: the HRB owns boot + DRAM, so LiteX must NOT build a BIOS/ROM, and the HRB's
    # private boot vector must never trip the SoC integrator's reset-address region check.
    integrated_rom_supported = False
    reset_address_check      = False
    # AXI address is decoded in the AXILite2CSR bridge; CSR offset is re-added in software.
    csr_decode               = True

    @property
    def mem_map(self):
        return {
            "csr"         : 0xe800_0000,            # LiteX CSR window reached over AXIA.
            "peripherals" : 0xf000_0000,
        }

    # GCC Flags.
    @property
    def gcc_flags(self):
        flags  = " -mabi=ilp32 -march=rv32imafdc"
        flags += " -D__EFINIX_SAPPHIRE_HP__"
        return flags

    def __init__(self, platform, variant, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.platform     = platform
        self.variant      = variant
        self.reset        = Signal()
        self.interrupt    = Signal(USER_INTERRUPT_COUNT)  # Fabric peripheral IRQs -> HRB USERINTERRUPT.
        self.periph_buses = []                            # Peripheral buses (connected to main SoC bus).
        self.memory_buses = []                            # Memory buses: EMPTY (HRB owns its DDR).

        # Efinity IP configuration (drives the peri.xml block). PoC defaults; expose setters below.
        self.config = {
            "core_count"           : 4,     # Ti375 hard quad-core.
            "custom_instruction"   : 0,     # CFU / custom-instruction enable.
            "axia_enable"          : 1,
            "io_ddrmasters0_enable": 1,
        }

        # CPU <-> fabric ports accumulated here, instantiated in do_finalize (gowin_ae350 pattern).
        self.cpu_params = dict(
            # Clk/Rst. TODO (Efinity): the HRB needs dedicated core/AXI/DDR clocks from Efinix PLL cores;
            # PoC drives everything from "sys". Active-low reset, gowin idiom.
            i_CLK     = ClockSignal("sys"),
            i_RSTN    = ~(ResetSignal("sys") | self.reset),

            # USERINTERRUPT[A..X] (fabric -> HRB).
            i_USERINTERRUPT = self.interrupt,
        )

        # AXIA_* : 32-bit AXI4 peripheral master (HRB -> fabric) -> LiteX periph_bus.
        self.pbus = self.add_axia_master()
        self.periph_buses.append(self.pbus)

        # IO_DDRMASTERS_0 : 128-bit AXI4 coherent port -> LiteX dma_bus.
        self.dma_bus = self.add_io_ddrmasters_dma()

    # AXIA_* peripheral master (CPU-as-MASTER directions) ------------------------------------------
    def add_axia_master(self):
        axia = axi.AXIInterface(
            data_width    = self.data_width,
            address_width = AXIA_ADDR_WIDTH,
            id_width      = AXIA_ID_WIDTH,
        )
        # HRB is master: aw/ar/w payload + *valid are Instance outputs (o_); ready + b/r are inputs (i_).
        # TODO (Efinity): confirm the AXIA_* port names/widths against the generated IP.
        self.cpu_params.update({
            # aw.
            "o_AXIA_awid"    : axia.aw.id,
            "o_AXIA_awaddr"  : axia.aw.addr,
            "o_AXIA_awlen"   : axia.aw.len,
            "o_AXIA_awsize"  : axia.aw.size,
            "o_AXIA_awburst" : axia.aw.burst,
            "o_AXIA_awlock"  : axia.aw.lock,
            "o_AXIA_awcache" : axia.aw.cache,
            "o_AXIA_awprot"  : axia.aw.prot,
            "o_AXIA_awqos"   : axia.aw.qos,
            "o_AXIA_awvalid" : axia.aw.valid,
            "i_AXIA_awready" : axia.aw.ready,
            # w.
            "o_AXIA_wdata"   : axia.w.data,
            "o_AXIA_wstrb"   : axia.w.strb,
            "o_AXIA_wlast"   : axia.w.last,
            "o_AXIA_wvalid"  : axia.w.valid,
            "i_AXIA_wready"  : axia.w.ready,
            # b.
            "i_AXIA_bid"     : axia.b.id,
            "i_AXIA_bresp"   : axia.b.resp,
            "i_AXIA_bvalid"  : axia.b.valid,
            "o_AXIA_bready"  : axia.b.ready,
            # ar.
            "o_AXIA_arid"    : axia.ar.id,
            "o_AXIA_araddr"  : axia.ar.addr,
            "o_AXIA_arlen"   : axia.ar.len,
            "o_AXIA_arsize"  : axia.ar.size,
            "o_AXIA_arburst" : axia.ar.burst,
            "o_AXIA_arlock"  : axia.ar.lock,
            "o_AXIA_arcache" : axia.ar.cache,
            "o_AXIA_arprot"  : axia.ar.prot,
            "o_AXIA_arqos"   : axia.ar.qos,
            "o_AXIA_arvalid" : axia.ar.valid,
            "i_AXIA_arready" : axia.ar.ready,
            # r.
            "i_AXIA_rid"     : axia.r.id,
            "i_AXIA_rdata"   : axia.r.data,
            "i_AXIA_rresp"   : axia.r.resp,
            "i_AXIA_rlast"   : axia.r.last,
            "i_AXIA_rvalid"  : axia.r.valid,
            "o_AXIA_rready"  : axia.r.ready,
        })
        return axia

    # IO_DDRMASTERS_0 coherent DMA port (CPU-as-SLAVE directions) -----------------------------------
    def add_io_ddrmasters_dma(self):
        # NOTE: per the spec this "becomes a dma_bus", i.e. fabric DMA masters push coherent traffic
        # INTO the HRB -> LiteX consumes `dma_bus` as a SLAVE, so the HRB receives aw/ar/w (Instance
        # inputs i_) and drives ready + b/r (outputs o_) -- the MIRROR of the AXIA master above.
        # !!! VALIDATION GATE: if IO_DDRMASTERS_0 is actually an OUTBOUND master to DDR, flip these
        # directions to the AXIA pattern and register via `self.memory_buses.append(...)` instead. !!!
        ddr = axi.AXIInterface(
            data_width    = IO_DDR_DATA_WIDTH,
            address_width = IO_DDR_ADDR_WIDTH,
            id_width      = IO_DDR_ID_WIDTH,
        )
        # TODO (Efinity): confirm the IO_DDRMASTERS_0_* port names/widths against the generated IP.
        self.cpu_params.update({
            # aw (fabric -> HRB: Instance inputs).
            "i_IO_DDRMASTERS_0_awid"    : ddr.aw.id,
            "i_IO_DDRMASTERS_0_awaddr"  : ddr.aw.addr,
            "i_IO_DDRMASTERS_0_awlen"   : ddr.aw.len,
            "i_IO_DDRMASTERS_0_awsize"  : ddr.aw.size,
            "i_IO_DDRMASTERS_0_awburst" : ddr.aw.burst,
            "i_IO_DDRMASTERS_0_awlock"  : ddr.aw.lock,
            "i_IO_DDRMASTERS_0_awcache" : ddr.aw.cache,
            "i_IO_DDRMASTERS_0_awprot"  : ddr.aw.prot,
            "i_IO_DDRMASTERS_0_awvalid" : ddr.aw.valid,
            "o_IO_DDRMASTERS_0_awready" : ddr.aw.ready,
            # w.
            "i_IO_DDRMASTERS_0_wdata"   : ddr.w.data,
            "i_IO_DDRMASTERS_0_wstrb"   : ddr.w.strb,
            "i_IO_DDRMASTERS_0_wlast"   : ddr.w.last,
            "i_IO_DDRMASTERS_0_wvalid"  : ddr.w.valid,
            "o_IO_DDRMASTERS_0_wready"  : ddr.w.ready,
            # b (HRB -> fabric: Instance outputs).
            "o_IO_DDRMASTERS_0_bid"     : ddr.b.id,
            "o_IO_DDRMASTERS_0_bresp"   : ddr.b.resp,
            "o_IO_DDRMASTERS_0_bvalid"  : ddr.b.valid,
            "i_IO_DDRMASTERS_0_bready"  : ddr.b.ready,
            # ar.
            "i_IO_DDRMASTERS_0_arid"    : ddr.ar.id,
            "i_IO_DDRMASTERS_0_araddr"  : ddr.ar.addr,
            "i_IO_DDRMASTERS_0_arlen"   : ddr.ar.len,
            "i_IO_DDRMASTERS_0_arsize"  : ddr.ar.size,
            "i_IO_DDRMASTERS_0_arburst" : ddr.ar.burst,
            "i_IO_DDRMASTERS_0_arlock"  : ddr.ar.lock,
            "i_IO_DDRMASTERS_0_arcache" : ddr.ar.cache,
            "i_IO_DDRMASTERS_0_arprot"  : ddr.ar.prot,
            "i_IO_DDRMASTERS_0_arvalid" : ddr.ar.valid,
            "o_IO_DDRMASTERS_0_arready" : ddr.ar.ready,
            # r.
            "o_IO_DDRMASTERS_0_rid"     : ddr.r.id,
            "o_IO_DDRMASTERS_0_rdata"   : ddr.r.data,
            "o_IO_DDRMASTERS_0_rresp"   : ddr.r.resp,
            "o_IO_DDRMASTERS_0_rlast"   : ddr.r.last,
            "o_IO_DDRMASTERS_0_rvalid"  : ddr.r.valid,
            "i_IO_DDRMASTERS_0_rready"  : ddr.r.ready,
        })
        return ddr

    # The HRB boot vector is fixed in silicon; record + assert rather than silently accepting (rocket).
    def set_reset_address(self, reset_address):
        self.reset_address = reset_address
        assert reset_address == self.__class__.reset_address, (
            f"EfinixSapphireHP reset address is fixed at {self.__class__.reset_address:#010x} "
            f"(HRB silicon boot vector); got {reset_address:#010x}."
        )

    # Register the hard block with the Efinity Interface Designer (peri.xml, post-save injection).
    def add_sapphire_block(self):
        block = EfinixSapphireHPBlock()
        block["name"]     = "sapphire_hp_inst"
        block["config"]   = self.config
        # Fabric signal-name bindings per AXI/IRQ interface. Names resolved at finalize.
        block["bindings"] = {
            "AXIA"            : {p[2:]: f"{p[2:]}" for p in self.cpu_params if "AXIA_" in p},
            "IO_DDRMASTERS_0" : {p[2:]: f"{p[2:]}" for p in self.cpu_params if "IO_DDRMASTERS_0_" in p},
        }
        self.platform.toolchain.ifacewriter.xml_blocks.append(block)

    def do_finalize(self):
        # Emit the Efinity peri.xml hard-block placement, then the black-box Instance.
        if hasattr(self.platform, "toolchain") and hasattr(self.platform.toolchain, "ifacewriter"):
            self.add_sapphire_block()
        self.specials += Instance(SAPPHIRE_HP_MACRO, **self.cpu_params)
