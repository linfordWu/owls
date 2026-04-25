#!/usr/bin/env python3
"""
Memory-pressure test program — allocates a percentage of system RAM
and never frees it, simulating a leaky long-running service.

Usage:
    python memory_pressure_program.py [PERCENT] [DURATION_SEC]

Arguments:
    PERCENT       Percentage of total system memory to allocate (default: 30)
    DURATION_SEC  Seconds to keep running before entering infinite sleep (default: 60)

The program reads /proc/meminfo to determine total RAM, allocates
PERCENT% of that memory in large bytearray chunks, fills them with
pseudo-random data, and then sleeps forever so the memory stays
resident until the process is killed.
"""

import os
import random
import sys
import time


def get_system_total_memory() -> int:
    """Read total system memory in bytes from /proc/meminfo."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    return int(parts[1]) * 1024
    except Exception:
        pass
    return 0


def main() -> int:
    percent = 30
    duration_sec = 60

    if len(sys.argv) > 1:
        percent = int(sys.argv[1])
    if len(sys.argv) > 2:
        duration_sec = int(sys.argv[2])

    if percent <= 0 or percent > 99:
        print("Error: memory percentage must be 1-99", file=sys.stderr)
        return 1

    mem_total = get_system_total_memory()
    if mem_total == 0:
        print("Error: cannot read system memory", file=sys.stderr)
        return 1

    target_bytes = mem_total * percent // 100
    chunk_size = 16 * 1024 * 1024  # 16 MiB per chunk

    print(f"[memory_pressure] System total: {mem_total / (1024**3):.2f} GB")
    print(f"[memory_pressure] Target allocation: {percent}% = {target_bytes / (1024**3):.2f} GB")
    print(f"[memory_pressure] Chunk size: {chunk_size / (1024**2):.1f} MiB")

    allocated = []
    total_allocated = 0
    rng = random.Random(42)

    while total_allocated < target_bytes:
        need = min(chunk_size, target_bytes - total_allocated)
        chunk = bytearray(need)
        # Touch every page so RSS tracks the allocation
        for i in range(0, need, 4096):
            chunk[i] = rng.randint(0, 255)
        allocated.append(chunk)
        total_allocated += need
        if len(allocated) % 10 == 0:
            print(f"[memory_pressure] Allocated {len(allocated)} chunks, {total_allocated / (1024**3):.2f} GB so far")

    print(f"[memory_pressure] Total allocated: {total_allocated / (1024**3):.2f} GB in {len(allocated)} chunks")
    print(f"[memory_pressure] Simulating workload for {duration_sec} seconds...")

    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed >= duration_sec:
            break
        # Periodic churn to keep pages resident
        for chunk in allocated:
            chunk[0] = (chunk[0] + 1) % 256
        time.sleep(1)

    print(f"[memory_pressure] Work phase complete. Entering infinite sleep (memory retained).")
    while True:
        time.sleep(3600)

    return 0


if __name__ == "__main__":
    sys.exit(main())
