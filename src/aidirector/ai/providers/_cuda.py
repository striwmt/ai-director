"""Deterministic GPU memory release for in-process providers.

`model = None` alone is not enough: torch models commonly sit in
reference cycles, so the object survives until a later GC pass — after
`empty_cache()` already ran — and its blocks stay in the process's
caching allocator forever. That residue (multiple GB) then OOMs any
separate llama-server process sharing the GPU.
"""

from __future__ import annotations

import gc


def free_cuda_memory() -> None:
    """Collect cycles first, then return cached blocks to the driver."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
