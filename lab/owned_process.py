"""Own a Windows job or a POSIX session group, including surviving descendants."""
import ctypes
from ctypes import wintypes
import os
import signal

if os.name=='nt':
    _kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    SIZE_T=ctypes.c_size_t
    class BasicLimits(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',wintypes.DWORD),
                  ('min_working',SIZE_T),('max_working',SIZE_T),('active_processes',wintypes.DWORD),
                  ('affinity',SIZE_T),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IOCounters(ctypes.Structure):
        _fields_=[(name,ctypes.c_uint64) for name in ['read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes']]
    class ExtendedLimits(ctypes.Structure):
        _fields_=[('basic',BasicLimits),('io',IOCounters),('process_memory',SIZE_T),('job_memory',SIZE_T),('peak_process',SIZE_T),('peak_job',SIZE_T)]
    _kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
    _kernel.CreateJobObjectW.restype=wintypes.HANDLE
    _kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    _kernel.SetInformationJobObject.restype=wintypes.BOOL
    _kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    _kernel.AssignProcessToJobObject.restype=wintypes.BOOL
    _kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
    _kernel.TerminateJobObject.restype=wintypes.BOOL
    _kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    _kernel.CloseHandle.restype=wintypes.BOOL

class OwnedJob:
    def __init__(self,process):
        self.handle=None;self.group=None
        if os.name!='nt':
            # Research.run uses start_new_session=True. Never adopt or kill the
            # application's group, even if construction is called incorrectly.
            try:group=os.getpgid(process.pid)
            except ProcessLookupError:return
            if group!=process.pid:raise ValueError('计算子进程未形成独立进程组，不能接管其他进程')
            self.group=group
            return
        handle=_kernel.CreateJobObjectW(None,None)
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits=ExtendedLimits()
            # Windows venv redirectors start the real interpreter as a child.
            # Bound the complete owned tree instead of preventing that handoff.
            limits.basic.flags=0x2000|0x8
            limits.basic.active_processes=4
            if not _kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not _kernel.AssignProcessToJobObject(handle,wintypes.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle=handle
        except BaseException:
            _kernel.CloseHandle(handle)
            if process.poll() is None:process.kill();process.wait(timeout=5)
            raise
    def terminate(self):
        if self.group is not None:
            try:os.killpg(self.group,signal.SIGKILL)
            except ProcessLookupError:pass # This exact owned group is already gone.
        if self.handle and not _kernel.TerminateJobObject(self.handle,124):
            raise ctypes.WinError(ctypes.get_last_error())
    def close(self):
        if self.group is not None:
            self.terminate();self.group=None
        if self.handle:
            _kernel.CloseHandle(self.handle);self.handle=None
