"""Which torch build the Chatterbox engine gets, chosen by the user's GPU.

A CUDA build of torch only carries GPU code ("kernels") for the GPU
generations it was compiled for. torch reports that list as
``torch.cuda.get_arch_list()`` — entries like ``sm_86`` (compute capability
8.6). On a GPU outside the list torch still imports, still says CUDA is
available and can even copy tensors to the card, then dies on the first real
computation with::

    RuntimeError: CUDA error: no kernel image is available for execution on the device

Reinstalling cannot fix that: the pin reinstalls the very same wheel with the
very same list. The only fix is a different build, so the installer picks one
from the table below by the GPU's compute capability, and the synthesis runner
checks the same rule at startup (falling back to the CPU for that run) in case
the GPU changed after the install.

This module is pure stdlib on purpose: the installer imports it in the app,
and the runner imports it inside the engine venv.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

# (major, minor), e.g. (12, 0) for an RTX 50-series card.
Capability = tuple[int, int]

# chatterbox-tts==0.1.7 declares torch==2.6.0 and torchaudio==2.6.0 (for
# Python < 3.14, which covers our 3.11 venv). A build with any other version
# has to install chatterbox-tts with --no-deps, or pip "fixes" the conflict by
# pulling torch 2.6.0 back from PyPI — the CPU-only wheel on Windows.
CHATTERBOX_DECLARED_TORCH = "2.6.0"


@dataclass(frozen=True)
class TorchBuild:
    """One installable torch + torchaudio pair (they release in lockstep)."""

    version: str
    # The wheel index name, which is also the "+local" version tag:
    # "cu124" -> https://download.pytorch.org/whl/cu124, torch 2.6.0+cu124.
    variant: str
    # torch.cuda.get_arch_list() of the win_amd64 cp311 wheel, verbatim.
    arch_list: tuple[str, ...]

    @property
    def index_url(self) -> str:
        return f"https://download.pytorch.org/whl/{self.variant}"

    @property
    def local_version(self) -> str:
        """What ``torch.__version__`` reports once this build is installed."""
        return f"{self.version}+{self.variant}"

    @property
    def is_cuda(self) -> bool:
        return self.variant != "cpu"

    @property
    def overrides_chatterbox_pin(self) -> bool:
        return self.version != CHATTERBOX_DECLARED_TORCH


# GPU builds, in order of preference. The FIRST row whose arch list runs on
# the GPU wins, so every GPU the validated 2.6.0 build already handles keeps
# it, and only GPUs it cannot run move to the newer build. That works out to:
#
#   compute capability   build            GPUs
#   5.0 - 9.x            2.6.0 + cu124    Maxwell .. Hopper (GTX 900 .. RTX 40, H100)
#   10.x, 12.x           2.8.0 + cu128    Blackwell (B200, RTX 50)
#   anything else        CPU_BUILD        no build here has kernels for it yet
#
# Why 2.8.0 and not newer: torchaudio 2.9 removed torchaudio.info and moved
# torchaudio.save onto torchcodec, and the runner (and silero-vad) use both.
# 2.8.0 is the newest torch whose torchaudio still has them.
#
# To support a new GPU generation:
#   1. Find the newest torch that has a cuXYZ wheel for cp311 win_amd64, still
#      works with the runner's torchaudio calls, and resolves against
#      installer/requirements-chatterbox.txt (chatterbox-tts itself excluded).
#   2. Read its arch list in a throwaway venv. get_arch_list() returns [] when
#      the machine has no GPU, so read the compiled-in flags directly:
#          python -c "import torch; print(torch._C._cuda_getArchFlags())"
#   3. Append a row below, copying that list verbatim.
#   4. Smoke-synthesize on a GPU of the new generation, and add it to the
#      capability cases in tests/test_torch_builds.py.
GPU_BUILDS: tuple[TorchBuild, ...] = (
    TorchBuild(
        "2.6.0", "cu124",
        ("sm_50", "sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86",
         "sm_90"),
    ),
    TorchBuild(
        "2.8.0", "cu128",
        ("sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90", "sm_100",
         "sm_120"),
    ),
)

# No NVIDIA GPU, or none of GPU_BUILDS runs on it. Chatterbox then synthesizes
# on the CPU: slow, but it finishes instead of crashing.
CPU_BUILD = TorchBuild(CHATTERBOX_DECLARED_TORCH, "cpu", ())

_ARCH_RE = re.compile(r"(sm|compute)_(\d+)([af]?)")


def parse_capability(text: str) -> Optional[Capability]:
    """``"12.0"`` (as nvidia-smi prints it) -> ``(12, 0)``; junk -> None."""
    m = re.fullmatch(r"\s*(\d+)\.(\d+)\s*", text or "")
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2))


def arch_runs_on(arch: str, capability: Capability) -> bool:
    """True when code compiled for ``arch`` runs on a GPU of ``capability``.

    CUDA's compatibility rules:

    * ``sm_XY`` machine code runs on the same major version with an equal or
      higher minor — ``sm_86`` code runs on an 8.9 card (RTX 40), but nothing
      crosses a major version: ``sm_90`` code does not run on 12.0.
    * ``compute_XY`` (PTX) is compiled on the fly for any equal or newer GPU.
    * An ``a`` suffix (``sm_90a``) targets exactly that GPU and nothing else.
    """
    m = _ARCH_RE.fullmatch(arch)
    if m is None:
        return False
    kind, digits, suffix = m.groups()
    built = (int(digits) // 10, int(digits) % 10)
    if suffix == "a":
        return built == capability
    if kind == "compute" and suffix != "f":
        return built <= capability
    return built[0] == capability[0] and built[1] <= capability[1]


def arch_list_covers(arch_list: Sequence[str], capability: Capability) -> bool:
    """True when a build with ``arch_list`` has kernels for ``capability``."""
    return any(arch_runs_on(arch, capability) for arch in arch_list)


def select_torch_build(
    has_nvidia: bool,
    capabilities: Sequence[Capability],
    installed: Optional[str] = None,
) -> TorchBuild:
    """Pick the torch build for this machine.

    ``capabilities`` holds one entry per NVIDIA GPU. A build that covers all
    of them is preferred. On a mixed machine that no single build covers, the
    newest GPU is served: CUDA numbers the fastest GPU first by default, so
    that is the ``cuda`` device the runner (and the install smoke test) uses.
    If it is not, the runner's startup check falls back to the CPU.

    ``installed`` is the venv's current ``torch.__version__``, if any. A GPU
    build already installed that still covers the GPUs is kept, so a repair
    never swaps one working multi-GB build for another.
    """
    if not has_nvidia:
        return CPU_BUILD
    current = next((b for b in GPU_BUILDS if b.local_version == installed), None)
    if not capabilities:
        # The capability is unknown: nvidia-smi too old to report it (drivers
        # before ~R510), or it failed or timed out this once. Keep a GPU build
        # that is already there; it was chosen when the probe worked.
        # Otherwise take the first row: every GPU that needs a newer row also
        # needs a much newer driver than ~R510. The runner's startup check
        # still catches it if this guess is wrong.
        return current or GPU_BUILDS[0]
    if current is not None and all(
        arch_list_covers(current.arch_list, c) for c in capabilities
    ):
        return current
    for build in GPU_BUILDS:
        if all(arch_list_covers(build.arch_list, c) for c in capabilities):
            return build
    # Mixed GPUs no single build covers: serve the one synthesis runs on.
    newest = max(capabilities)
    for build in GPU_BUILDS:
        if arch_list_covers(build.arch_list, newest):
            return build
    return CPU_BUILD


@dataclass(frozen=True)
class GpuSpec:
    """One CUDA device as torch reports it (``torch.cuda.get_device_properties``)."""

    index: int
    name: str
    capability: Capability
    sm_count: int
    memory_bytes: int

    def describe(self) -> str:
        return (
            f"{self.name} (compute capability {self.capability[0]}."
            f"{self.capability[1]}, {self.memory_bytes / 2**30:.0f} GB, "
            f"{self.sm_count} SMs)"
        )


def pick_gpu(gpus: Sequence[GpuSpec], arch_list: Sequence[str]) -> Optional[GpuSpec]:
    """The GPU synthesis should run on, or None when the build runs on none.

    Plain ``"cuda"`` means device 0, and CUDA's own ordering only guesses
    which card is fastest. On a machine that kept its old card next to the
    new one (a GTX 1080 beside an RTX 5060 Ti, both covered by the cu128
    build), that guess decides whether the new card is used at all. So pick
    explicitly: among the GPUs this build has kernels for, the one with the
    most streaming multiprocessors (the units that do the work), then the
    most memory, then the lowest index.
    """
    usable = [g for g in gpus if arch_list_covers(arch_list, g.capability)]
    if not usable:
        return None
    return max(usable, key=lambda g: (g.sm_count, g.memory_bytes, -g.index))
