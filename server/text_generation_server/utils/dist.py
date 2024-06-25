import os
import torch

from datetime import timedelta
from loguru import logger
from text_generation_server.utils.import_utils import IPEX_AVAIL

# Tensor Parallelism settings
RANK = int(os.getenv("RANK", "0"))
WORLD_SIZE = int(os.getenv("WORLD_SIZE", "1"))

# CUDA memory fraction
MEMORY_FRACTION = float(os.getenv("CUDA_MEMORY_FRACTION", "1.0"))


class FakeBarrier:
    def wait(self):
        pass


class FakeGroup:
    def __init__(self, rank, size):
        self._rank = rank
        self._size = size

    def allreduce(self, *args, **kwargs):
        return FakeBarrier()

    def allgather(self, inputs, local_tensor, **kwargs):
        assert (
            len(inputs[0]) == len(local_tensor) == 1
        ), f"{len(inputs[0])} != {len(local_tensor)} != 1, and the FakeGroup is supposed to join on simple tensors"
        for input_ in inputs:
            input_[0].data = local_tensor[0].data
        return FakeBarrier()

    def barrier(self, *args, **kwargs):
        return FakeBarrier()

    def size(self):
        return self._size

    def rank(self):
        return self._rank


def initialize_torch_distributed():
    if torch.cuda.is_available():
        from torch.distributed import ProcessGroupNCCL

        # Set the device id.
        assert WORLD_SIZE <= torch.cuda.device_count(), "Each process is one gpu"
        device = RANK % torch.cuda.device_count()
        torch.cuda.set_device(device)
        torch.cuda.set_per_process_memory_fraction(MEMORY_FRACTION, device)
        backend = "nccl"
        options = ProcessGroupNCCL.Options()
        options.is_high_priority_stream = True
        options._timeout = timedelta(seconds=60)
    else:
        backend = "gloo"
        options = None

    if WORLD_SIZE == 1:
        return FakeGroup(RANK, WORLD_SIZE), RANK, WORLD_SIZE
    else:
        if os.getenv("DEBUG", None) == "1":
            return FakeGroup(RANK, WORLD_SIZE), RANK, WORLD_SIZE

        if not torch.distributed.is_initialized():
            # Call the init process.
            if IPEX_AVAIL:
                import intel_extension_for_pytorch as ipex

                ipex.distributed.init_process_group(
                    backend="ccl",
                    world_size=WORLD_SIZE,
                    rank=RANK,
                    timeout=timedelta(seconds=60),
                    pg_options=options,
                )
            else:
                torch.distributed.init_process_group(
                    backend=backend,
                    world_size=WORLD_SIZE,
                    rank=RANK,
                    timeout=timedelta(seconds=60),
                    pg_options=options,
                )
        else:
            logger.warning("torch.distributed is already initialized.")

        return torch.distributed.group.WORLD, RANK, WORLD_SIZE
