"""Scoped best-effort scheduling hint, never a real-time scheduling guarantee.

Apple documents QoS as affecting CPU scheduling and timer latency. Use its
user-initiated class only for this controller thread; never change another
process, request real-time priority, disable power management, or hide overruns.
The temporary override restores the prior classification when the loop exits.
Other operating systems retain their normal scheduler in this implementation.
"""
from __future__ import annotations

import ctypes
import sys


class ControlThreadQoS:
    def __init__(self):
        self._lib = None
        self._override = None
        self._status = {"platform": sys.platform, "mode": "platform_default",
                        "applied": False, "released": None,
                        "hard_real_time": False, "error": None}

    def __enter__(self):
        if sys.platform != "darwin":
            return self
        try:
            lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            lib.pthread_self.argtypes = []
            lib.pthread_self.restype = ctypes.c_void_p
            lib.pthread_override_qos_class_start_np.argtypes = [ctypes.c_void_p,
                                                               ctypes.c_uint, ctypes.c_int]
            lib.pthread_override_qos_class_start_np.restype = ctypes.c_void_p
            lib.pthread_override_qos_class_end_np.argtypes = [ctypes.c_void_p]
            lib.pthread_override_qos_class_end_np.restype = ctypes.c_int
            # sys/qos.h: QOS_CLASS_USER_INITIATED = 0x19, below USER_INTERACTIVE.
            token = lib.pthread_override_qos_class_start_np(lib.pthread_self(), 0x19, 0)
            if not token:
                self._status["error"] = "pthread QoS override was not accepted"
                return self
            self._lib, self._override = lib, token
            self._status.update(mode="darwin_user_initiated_thread_override", applied=True)
        except (OSError, AttributeError) as exc:
            self._status["error"] = f"{type(exc).__name__}: {exc}"
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._override is not None:
            code = self._lib.pthread_override_qos_class_end_np(self._override)
            self._override = None
            self._status["released"] = code == 0
            if code:
                self._status["error"] = f"pthread QoS override release failed: {code}"
        return False

    def report(self) -> dict:
        return dict(self._status)
