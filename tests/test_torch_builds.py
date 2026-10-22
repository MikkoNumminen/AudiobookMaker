"""Tests for src/torch_builds.py — which torch build the engine venv gets.

The failure these guard against: the venv always got torch 2.6.0+cu124,
whose kernels stop at sm_90, so an RTX 50-series card (compute capability
12.0) died mid-conversion with "no kernel image is available for execution
on the device" — and reinstalling only reinstalled the same wheel.
"""

from __future__ import annotations

import re

import pytest

from src.torch_builds import (
    CHATTERBOX_DECLARED_TORCH,
    CPU_BUILD,
    GPU_BUILDS,
    arch_list_covers,
    arch_runs_on,
    parse_capability,
    select_torch_build,
)

CU124, CU128 = GPU_BUILDS


# ---------------------------------------------------------------------------
# CUDA compatibility rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arch, capability, runs",
    [
        ("sm_86", (8, 6), True),
        ("sm_86", (8, 9), True),    # RTX 40 runs Ampere machine code
        ("sm_86", (8, 0), False),   # never on a LOWER minor
        ("sm_90", (12, 0), False),  # nor across a major version: the bug
        ("sm_120", (12, 0), True),
        ("sm_120", (12, 1), True),
        ("sm_100", (12, 0), False),
        ("compute_90", (12, 0), True),   # PTX is JIT-compiled forward
        ("compute_90", (8, 9), False),
        ("sm_90a", (9, 0), True),        # arch-specific: exact GPU only
        ("sm_90a", (10, 0), False),
        ("sm_100f", (10, 3), True),      # family-specific: same major
        ("sm_100f", (12, 0), False),
        ("not_an_arch", (8, 6), False),
    ],
)
def test_arch_runs_on(arch, capability, runs) -> None:
    assert arch_runs_on(arch, capability) is runs


def test_the_reported_failure_is_not_covered_by_the_old_build() -> None:
    # The field report: an RTX 50-series card under the old single pin.
    assert not arch_list_covers(CU124.arch_list, (12, 0))
    assert arch_list_covers(CU128.arch_list, (12, 0))


def test_empty_arch_list_covers_nothing() -> None:
    assert not arch_list_covers((), (8, 6))


@pytest.mark.parametrize(
    "text, expected",
    [("12.0", (12, 0)), (" 8.6 ", (8, 6)), ("10.3", (10, 3)),
     ("", None), ("[N/A]", None), ("12", None)],
)
def test_parse_capability(text, expected) -> None:
    assert parse_capability(text) == expected


# ---------------------------------------------------------------------------
# Capability -> build table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "capability, build",
    [
        ((5, 0), CU124), ((5, 2), CU124),   # Maxwell (GTX 900)
        ((6, 1), CU124),                    # Pascal (GTX 10)
        ((7, 0), CU124), ((7, 5), CU124),   # Volta, Turing (RTX 20)
        ((8, 0), CU124), ((8, 6), CU124),   # Ampere (A100, RTX 30)
        ((8, 9), CU124),                    # Ada (RTX 40)
        ((9, 0), CU124),                    # Hopper (H100)
        ((10, 0), CU128),                   # Blackwell datacenter (B200)
        ((12, 0), CU128),                   # Blackwell consumer (RTX 50)
        ((12, 1), CU128),
        ((3, 7), CPU_BUILD),                # Kepler: too old for any build
        ((11, 0), CPU_BUILD),               # no row has sm_110 kernels
        ((13, 0), CPU_BUILD),               # a generation newer than the table
    ],
)
def test_capability_selects_build(capability, build) -> None:
    assert select_torch_build(True, [capability]) == build


def test_no_nvidia_gpu_gets_the_cpu_build() -> None:
    assert select_torch_build(False, []) == CPU_BUILD


def test_unknown_capability_keeps_the_validated_build() -> None:
    # nvidia-smi too old to report compute_cap: a pre-Blackwell driver, so a
    # pre-Blackwell GPU.
    assert select_torch_build(True, []) == CU124


@pytest.mark.parametrize(
    "capabilities, installed, build",
    [
        # Probe failed or timed out this once: keep the GPU build that is
        # there rather than "repairing" a working cu128 venv back to cu124.
        ([], "2.8.0+cu128", CU128),
        ([], "2.6.0+cpu", CU124),
        # An installed build that still covers the GPU is kept (no re-download
        # when an RTX 50 is swapped back for an RTX 40)...
        ([(8, 6)], "2.8.0+cu128", CU128),
        # ...but one that does not is replaced: the field case.
        ([(12, 0)], "2.6.0+cu124", CU128),
        ([(5, 2)], "2.8.0+cu128", CU124),   # 2.8 dropped Maxwell
        ([(13, 0)], "2.8.0+cu128", CPU_BUILD),
    ],
)
def test_installed_build_is_kept_while_it_fits(capabilities, installed, build) -> None:
    assert select_torch_build(True, capabilities, installed) == build


@pytest.mark.parametrize(
    "capabilities, build",
    [
        ([(12, 0), (8, 6)], CU128),  # one build covers both: prefer it
        ([(8, 6), (12, 0)], CU128),
        # None covers both: serve the newest, which CUDA numbers first by
        # default — whatever order nvidia-smi lists them in.
        ([(12, 0), (5, 2)], CU128),
        ([(5, 2), (12, 0)], CU128),
        ([(13, 0), (8, 6)], CPU_BUILD),
    ],
)
def test_mixed_gpus(capabilities, build) -> None:
    assert select_torch_build(True, capabilities) == build


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


def test_validated_build_comes_first_and_is_what_chatterbox_declares() -> None:
    # Existing users must keep the build their engine was validated on.
    assert CU124.local_version == "2.6.0+cu124"
    assert CU124.version == CHATTERBOX_DECLARED_TORCH
    assert not CU124.overrides_chatterbox_pin


def test_blackwell_build() -> None:
    assert CU128.local_version == "2.8.0+cu128"
    assert CU128.index_url == "https://download.pytorch.org/whl/cu128"
    assert CU128.overrides_chatterbox_pin


def test_every_gpu_build_keeps_torchaudio_info_and_save() -> None:
    # torchaudio 2.9 removed torchaudio.info and moved torchaudio.save onto
    # torchcodec; the runner and silero-vad call both.
    for build in GPU_BUILDS:
        major, minor = (int(x) for x in build.version.split(".")[:2])
        assert (major, minor) < (2, 9), build


def test_every_gpu_build_has_an_arch_list() -> None:
    for build in GPU_BUILDS:
        assert build.is_cuda
        assert build.arch_list
        for arch in build.arch_list:
            # A typo here would silently drop a GPU generation.
            assert re.fullmatch(r"(sm|compute)_\d+[af]?", arch), arch


def test_cpu_build() -> None:
    assert not CPU_BUILD.is_cuda
    assert CPU_BUILD.local_version == f"{CHATTERBOX_DECLARED_TORCH}+cpu"
    assert CPU_BUILD.index_url == "https://download.pytorch.org/whl/cpu"
