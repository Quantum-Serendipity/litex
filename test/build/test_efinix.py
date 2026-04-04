#
# This file is part of LiteX.
#
# SPDX-License-Identifier: BSD-2-Clause

from types import SimpleNamespace
import xml.etree.ElementTree as et

import migen
import pytest

from litex.build.efinix.efinity import EfinityToolchain, _add_verilog_include_paths
from litex.build.efinix.efinity import _get_design_file_library, build_argdict
from litex.build.efinix.common import add_gpio_block, gpio_info
from litex.build.efinix.ifacewriter import InterfaceWriter
from litex.build.efinix.toolchain import find_efinity_path, load_efinity_env
from litex.build.generic_toolchain import GenericToolchain


def test_build_argdict_wires_infer_sync_set_reset():
    args = SimpleNamespace(
        synth_mode="area",
        infer_clk_enable="2",
        infer_sync_set_reset="0",
        bram_output_regs_packing="1",
        retiming="2",
        seq_opt="0",
        mult_input_regs_packing="1",
        mult_output_regs_packing="0",
        generate_bitbin=True,
        generate_hexbin=False,
    )

    params = build_argdict(args)

    assert params["efx_map_params"]["work_dir"] == "work_syn"
    assert params["efx_map_params"]["mode"] == ["area", "e_option"]
    assert params["efx_map_params"]["infer-sync-set-reset"] == ["0", "e_option"]
    assert params["efx_pnr_params"]["work_dir"] == "work_pnr"
    assert params["efx_pgm_params"]["generate_bitbin"] is True
    assert params["efx_pgm_params"]["generate_hexbin"] is False


def test_build_merges_default_efinity_params(monkeypatch):
    sentinel = object()

    def fake_build(self, platform, fragment, **kwargs):
        return sentinel

    monkeypatch.setattr(GenericToolchain, "build", fake_build)

    toolchain = EfinityToolchain("/tmp/efinity")
    platform = SimpleNamespace(family="Titanium")
    fragment = migen.Module().get_fragment()

    assert toolchain.build(
        platform,
        fragment,
        efx_map_params={"mode": ["area2", "e_option"]},
        efx_pgm_params={"generate_bitbin": True},
        efx_full_memory_we=False,
    ) is sentinel

    assert toolchain._efx_map_params["work_dir"] == "work_syn"
    assert toolchain._efx_map_params["mode"] == ["area2", "e_option"]
    assert toolchain._efx_map_params["infer-sync-set-reset"] == ["1", "e_option"]
    assert "mult_input_regs_packing" not in toolchain._efx_map_params
    assert "mult_output_regs_packing" not in toolchain._efx_map_params
    assert toolchain._efx_pnr_params["work_dir"] == "work_pnr"
    assert toolchain._efx_pgm_params["generate_bitbin"] is True
    assert toolchain._efx_pgm_params["generate_hexbin"] is False


def test_verilog_include_paths_emit_efx_include_params():
    efx_map = et.Element("efx:synthesis", {"tool_name": "efx_map"})

    _add_verilog_include_paths(efx_map, ["/rtl/include0", "/rtl/include1"])

    params = [
        (param.get("name"), param.get("value"), param.get("value_type"))
        for param in efx_map.findall("efx:param")
    ]

    assert params == [
        ("include", "/rtl/include0", "e_string"),
        ("include", "/rtl/include1", "e_string"),
    ]


def test_design_file_library_preserves_non_header_libraries():
    assert _get_design_file_library("core.vhd", "worklib") == "worklib"
    assert _get_design_file_library("rtl/top.v", "mylib") == "mylib"
    assert _get_design_file_library("rtl/header.vh", "mylib") == "default"
    assert _get_design_file_library("rtl/header.svh", "mylib") == "default"


def test_design_file_library_uses_default_library_for_verilog_languages():
    assert _get_design_file_library("rtl/top.v", "verilog", "mylib") == "default"
    assert _get_design_file_library("rtl/header.vh", "verilog", "mylib") == "default"
    assert _get_design_file_library("rtl/header.svh", "systemverilog", "mylib") == "default"
    assert _get_design_file_library("core.vhd", "vhdl", "worklib") == "worklib"


def test_gpio_info_handles_scalar_and_vector_signals():
    class Platform:
        def get_pin_name(self, sig):
            return "scalar"

        def get_pin_location(self, sig):
            return ["P1"]

        def get_pins_name(self, sig):
            return "vector"

        def get_pins_location(self, sig):
            return ["P1", "P2"]

        def get_pin_properties(self, sig):
            return [("IO_STANDARD", "3.3_V_LVCMOS")]

    scalar = migen.Signal()
    vector = migen.Signal(2)

    assert gpio_info(Platform(), scalar) == (
        "scalar",
        ["P1"],
        [("IO_STANDARD", "3.3_V_LVCMOS")],
    )
    assert gpio_info(Platform(), vector) == (
        "vector",
        ["P1", "P2"],
        [("IO_STANDARD", "3.3_V_LVCMOS")],
    )


def test_add_gpio_block_tracks_block_and_excluded_io():
    sig = migen.Signal()
    block = {"type": "GPIO", "name": "gpio"}
    platform = SimpleNamespace(
        toolchain=SimpleNamespace(ifacewriter=SimpleNamespace(blocks=[]), excluded_ios=[]),
        get_pin=lambda sig: "resolved-pin",
    )

    add_gpio_block(platform, block, sig)

    assert platform.toolchain.ifacewriter.blocks == [block]
    assert platform.toolchain.excluded_ios == ["resolved-pin"]


def test_generate_seu_emits_wait_interval_for_auto_mode():
    def pin(name):
        return SimpleNamespace(backtrace=[(name, None)])

    writer = InterfaceWriter("/tmp/efinity")
    pins = SimpleNamespace(
        CONFIG       = pin("config"),
        DONE         = pin("done"),
        ERROR        = pin("error"),
        INJECT_ERROR = pin("inject_error"),
        RST          = pin("rst"),
    )
    block = {
        "name"          : "seu",
        "pins"          : pins,
        "enable"        : True,
        "mode"          : "auto",
        "wait_interval" : "42",
    }

    cmds = writer.generate_seu(block)

    assert 'MODE", "AUTO"' in cmds
    assert 'WAIT_INTERVAL", "42"' in cmds


def test_find_efinity_path_prefers_env(monkeypatch, tmp_path):
    efinity_root = tmp_path / "efinity"
    bin_dir = efinity_root / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "setup.sh").write_text("export TEST_EFINITY_ENV=from_env\n")
    monkeypatch.setenv("LITEX_ENV_EFINITY", str(efinity_root) + "/")

    assert find_efinity_path() == str(efinity_root)


def test_find_efinity_path_rejects_invalid_env(monkeypatch, tmp_path):
    efinity_root = tmp_path / "efinity"
    monkeypatch.setenv("LITEX_ENV_EFINITY", str(efinity_root) + "/")

    with pytest.raises(OSError):
        find_efinity_path()


def test_find_efinity_path_falls_back_to_path(monkeypatch, tmp_path):
    efinity_root = tmp_path / "efinity"
    bin_dir = efinity_root / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "setup.sh").write_text("export TEST_EFINITY_ENV=from_path\n")
    tool = bin_dir / "efx_map"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)

    monkeypatch.delenv("LITEX_ENV_EFINITY", raising=False)
    monkeypatch.setenv("PATH", str(bin_dir))

    assert find_efinity_path() == str(efinity_root)


def test_load_efinity_env_sources_setup(tmp_path):
    efinity_root = tmp_path / "efinity"
    bin_dir = efinity_root / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "setup.sh").write_text("export TEST_EFINITY_ENV=loaded\n")

    env = load_efinity_env(str(efinity_root))

    assert env["TEST_EFINITY_ENV"] == "loaded"


# GPIO generation tests ----------------------------------------------------------------------------

def test_generate_gpio_single_input():
    writer = InterfaceWriter("/tmp/efinity")
    block = {
        "name":       "my_input",
        "mode":       "INPUT",
        "location":   ["A5"],
        "properties": [],
    }
    cmds = writer.generate_gpio(block)
    assert 'create_input_gpio("my_input")' in cmds
    assert 'assign_pkg_pin("my_input","A5")' in cmds


def test_generate_gpio_multi_output():
    writer = InterfaceWriter("/tmp/efinity")
    block = {
        "name":       "led",
        "mode":       "OUTPUT",
        "location":   ["B1", "B2", "B3"],
        "size":       3,
        "properties": [],
    }
    cmds = writer.generate_gpio(block)
    assert 'create_output_gpio("led",2,0)' in cmds
    assert 'assign_pkg_pin("led[0]","B1")' in cmds
    assert 'assign_pkg_pin("led[1]","B2")' in cmds
    assert 'assign_pkg_pin("led[2]","B3")' in cmds


def test_generate_gpio_inout_with_oe_reg():
    writer = InterfaceWriter("/tmp/efinity")
    block = {
        "name":       "bidir",
        "mode":       "INOUT",
        "location":   ["C1"],
        "properties": [],
        "oe_reg":     "REG",
    }
    cmds = writer.generate_gpio(block)
    assert 'create_inout_gpio("bidir")' in cmds
    assert 'OE_REG","REG"' in cmds


def test_generate_gpio_output_with_drive_strength():
    writer = InterfaceWriter("/tmp/efinity")
    block = {
        "name":           "drv",
        "mode":           "OUTPUT",
        "location":       ["D1"],
        "properties":     [],
        "drive_strength": "4",
    }
    cmds = writer.generate_gpio(block)
    assert 'DRIVE_STRENGTH","4"' in cmds


def test_generate_gpio_output_const():
    writer = InterfaceWriter("/tmp/efinity")
    block = {
        "name":         "const_out",
        "mode":         "OUTPUT",
        "location":     ["E1"],
        "properties":   [],
        "const_output": 1,
    }
    cmds = writer.generate_gpio(block)
    assert 'CONST_OUTPUT","1"' in cmds


def test_generate_gpio_output_const_list():
    writer = InterfaceWriter("/tmp/efinity")
    block = {
        "name":         "const_bus",
        "mode":         "OUTPUT",
        "location":     ["E1", "E2"],
        "size":         2,
        "properties":   [],
        "const_output": [0, 1],
    }
    cmds = writer.generate_gpio(block)
    assert 'const_bus[0]","CONST_OUTPUT","0"' in cmds
    assert 'const_bus[1]","CONST_OUTPUT","1"' in cmds


def test_generate_gpio_input_clk():
    writer = InterfaceWriter("/tmp/efinity")
    block = {
        "name":       "clk_in",
        "mode":       "INPUT_CLK",
        "location":   "F1",
        "properties": [],
    }
    cmds = writer.generate_gpio(block)
    assert 'create_input_clock_gpio("clk_in")' in cmds
    assert 'assign_pkg_pin("clk_in","F1")' in cmds


def test_generate_gpio_output_clk():
    writer = InterfaceWriter("/tmp/efinity")
    block = {
        "name":       "clk_out",
        "mode":       "OUTPUT_CLK",
        "location":   "G1",
        "properties": [],
    }
    cmds = writer.generate_gpio(block)
    assert 'create_clockout_gpio("clk_out")' in cmds
    assert 'assign_pkg_pin("clk_out","G1")' in cmds


def test_generate_gpio_with_properties():
    writer = InterfaceWriter("/tmp/efinity")
    block = {
        "name":       "prop_pin",
        "mode":       "INPUT",
        "location":   ["H1"],
        "properties": [("SCHMITT_TRIGGER", "1"), ("PULL_OPTION", "WEAK_PULLUP")],
    }
    cmds = writer.generate_gpio(block)
    assert 'SCHMITT_TRIGGER","1"' in cmds
    assert 'PULL_OPTION","WEAK_PULLUP"' in cmds


# Special overrides test ---------------------------------------------------------------------------

def test_efinix_special_overrides_keys():
    from litex.build.efinix.common import efinix_special_overrides
    from litex.build.io import (
        DifferentialInput, DifferentialOutput,
        DDRInput, DDROutput, DDRTristate,
        SDRInput, SDROutput, SDRTristate,
        ClkInput, ClkOutput,
    )
    from migen.genlib.resetsync import AsyncResetSynchronizer
    assert DifferentialInput  in efinix_special_overrides
    assert DifferentialOutput in efinix_special_overrides
    assert SDRInput           in efinix_special_overrides
    assert SDROutput          in efinix_special_overrides
    assert SDRTristate        in efinix_special_overrides
    assert DDRInput           in efinix_special_overrides
    assert DDROutput          in efinix_special_overrides
    assert DDRTristate        in efinix_special_overrides
    assert ClkInput           in efinix_special_overrides
    assert ClkOutput          in efinix_special_overrides
    assert AsyncResetSynchronizer in efinix_special_overrides


# build_argdict additional tests -------------------------------------------------------------------

def test_build_argdict_defaults():
    args = SimpleNamespace(
        synth_mode=None,
        infer_clk_enable=None,
        infer_sync_set_reset=None,
        bram_output_regs_packing=None,
        retiming=None,
        seq_opt=None,
        mult_input_regs_packing=None,
        mult_output_regs_packing=None,
        generate_bitbin=False,
        generate_hexbin=False,
    )
    params = build_argdict(args)
    assert params["efx_map_params"]["work_dir"] == "work_syn"
    assert params["efx_map_params"]["mode"] == [None, "e_option"]
    assert params["efx_map_params"]["infer-sync-set-reset"] == [None, "e_option"]
    assert params["efx_pgm_params"]["generate_bitbin"] is False
    assert params["efx_pgm_params"]["generate_hexbin"] is False
