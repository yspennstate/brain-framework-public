"""Read live memory pressure and disk free space; never shed other work.

Windows reads the commit charge against the commit limit. Elsewhere the fraction is the memory in use
(total less available, from psutil or /proc/meminfo); the same 98.5 pct and 3 GiB floors apply."""
import ctypes
import os
from pathlib import Path
import shutil

def _posix_fraction():
    try:
        import psutil
        vm = psutil.virtual_memory()
        return 1 - vm.available/vm.total
    except Exception:
        pass
    info = {}
    with open('/proc/meminfo') as fh:
        for line in fh:
            key, _, rest = line.partition(':')
            info[key] = int(rest.split()[0])
    return 1 - info['MemAvailable']/info['MemTotal']

def resources():
    if os.name != 'nt':
        fraction = _posix_fraction()
        free = shutil.disk_usage(Path.home()).free/(1024**3)
        return dict(commit_fraction=fraction,c_free_gib=free,allowed=int(fraction < .985 and free > 3))
    class Performance(ctypes.Structure):
        _fields_ = [('cb',ctypes.c_ulong)] + [(name,ctypes.c_size_t) for name in (
            'CommitTotal','CommitLimit','CommitPeak','PhysicalTotal','PhysicalAvailable','SystemCache',
            'KernelTotal','KernelPaged','KernelNonpaged','PageSize')] + [
            ('HandleCount',ctypes.c_ulong),('ProcessCount',ctypes.c_ulong),('ThreadCount',ctypes.c_ulong)]
    data = Performance()
    data.cb = ctypes.sizeof(data)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(data),ctypes.sizeof(data)):
        raise ctypes.WinError()
    fraction = data.CommitTotal/data.CommitLimit
    free = shutil.disk_usage(Path.home().anchor).free/(1024**3)
    return dict(commit_fraction=fraction,c_free_gib=free,allowed=int(fraction < .985 and free > 3))

def resident():
    import psutil
    mem = psutil.Process().memory_info()
    return dict(rss_bytes=mem.rss,private_bytes=getattr(mem,'private',mem.rss))
