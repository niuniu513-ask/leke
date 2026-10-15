"""Protect the LeKe bearer token at rest with per-user Windows DPAPI.

This does not defend against code running as the logged-in Windows user.
Other platforms keep the existing storage behavior for development.
"""
import base64
import ctypes
import os
from ctypes import wintypes

PREFIX = 'dpapi:v1:'


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _transform(data: bytes, decrypt: bool) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def protect(value: str) -> str:
    if not value or os.name != 'nt':
        return value
    return PREFIX + base64.b64encode(_transform(value.encode('utf-8'), False)).decode('ascii')


def unprotect(value: str) -> str:
    if not value.startswith(PREFIX):
        return value  # Legacy cleartext: protected on the next settings save.
    try:
        if os.name != 'nt':
            return ''
        return _transform(base64.b64decode(value[len(PREFIX):], validate=True), True).decode('utf-8')
    except (ValueError, OSError, UnicodeError):
        return ''  # Different Windows user or corruption requires login again.
