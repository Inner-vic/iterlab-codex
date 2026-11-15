"""Fixed CUDA allocation probe; no framework install, dataset access or fitting."""
import ctypes
import json
import os
import subprocess
import sys
import uuid


class CudaUnavailable(ValueError):
    pass


def selected_devices(rows, count, mask):
    if type(count) is not int or not 0 <= count <= 128:
        raise CudaUnavailable('invalid_gpu_limit')
    if not count:
        return []
    allowed = rows if mask is None else []
    if mask is not None:
        for value in mask.split(','):
            token = value.strip()
            matches = [r for r in rows if str(r['index']) == token or
                       token.startswith('GPU-') and r['uuid'].startswith(token)]
            if len(matches) != 1:
                raise CudaUnavailable('cuda_device_mask_unresolved')
            allowed.append(matches[0])
    chosen = allowed[:count]
    if len(chosen) != count or len({r['uuid'] for r in chosen}) != count:
        raise CudaUnavailable('cuda_device_count_insufficient')
    return chosen


def probe(rows, count, mask, driver):
    chosen = selected_devices(rows, count, mask)
    result = []
    if not chosen:
        return result
    def call(name, *args):
        status = getattr(driver, name)(*args)
        if status:
            raise CudaUnavailable(name + ':' + str(status))
    call('cuInit', 0)
    for row in chosen:
        device = ctypes.c_int()
        identity = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(row['uuid'][4:]).bytes)
        call('cuDeviceGetByUuid', ctypes.byref(device), ctypes.byref(identity))
        context = ctypes.c_void_p()
        call('cuDevicePrimaryCtxRetain', ctypes.byref(context), device)
        pointer = ctypes.c_uint64()
        retained = True
        try:
            call('cuCtxSetCurrent', context)
            free, total = ctypes.c_size_t(), ctypes.c_size_t()
            call('cuMemGetInfo_v2', ctypes.byref(free), ctypes.byref(total))
            # Four bytes prove an actual allocation, not just nvidia-smi presence.
            # Creating a CUDA context itself may require driver-managed memory.
            call('cuMemAlloc_v2', ctypes.byref(pointer), ctypes.c_size_t(4))
            call('cuMemFree_v2', pointer)
            pointer = ctypes.c_uint64()
            result.append({'index': row['index'], 'uuid': row['uuid'],
                           'free_bytes': free.value, 'total_bytes': total.value,
                           'allocation_bytes': 4})
        finally:
            if pointer.value:
                call('cuMemFree_v2', pointer)
            if retained:
                call('cuCtxSetCurrent', ctypes.c_void_p())
                call('cuDevicePrimaryCtxRelease', device)
    return result


def main():
    count = int(sys.argv[1])
    report = {'schema': 'iterlab-cuda-preflight/v1', 'ready': False,
              'requested_gpus': count, 'fitting': False}
    try:
        if not count:
            devices = []
        else:
            response = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid', '--format=csv,noheader,nounits'],
                                      capture_output=True, text=True, timeout=10, check=True)
            rows = []
            for line in response.stdout.splitlines():
                index, identity = [v.strip() for v in line.split(',')]
                if not identity.startswith('GPU-'):
                    raise CudaUnavailable('unsupported_gpu_identity')
                rows.append({'index': int(index), 'uuid': identity})
            driver = ctypes.WinDLL('nvcuda.dll') if os.name == 'nt' else ctypes.CDLL('libcuda.so.1')
            devices = probe(rows, count, os.environ.get('CUDA_VISIBLE_DEVICES'), driver)
        report.update(ready=True, devices=devices)
    except (ValueError, OSError, AttributeError, subprocess.SubprocessError) as error:
        report['reason'] = str(error) if isinstance(error, CudaUnavailable) else 'cuda_preflight_unavailable'
    print(json.dumps(report, separators=(',', ':')))
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
