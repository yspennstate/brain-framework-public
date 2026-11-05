"""Read live Windows commit charge and disk free space; never shed other work."""
import ctypes
import os
from pathlib import Path
import shutil

def resources():
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
