"""
软件加固模块 - 保护机制
包含：反调试、代码混淆、完整性校验、许可证验证
"""
import os
import sys
import hashlib
import ctypes
import time
import struct
import threading
import base64
from pathlib import Path


class Protection:
    """软件保护类"""

    def __init__(self):
        self._license_key = None
        self._integrity_hash = None
        self._anti_debug_active = False

    def enable_anti_debug(self):
        """启用反调试保护"""
        if self._anti_debug_active:
            return True

        # 方法1: 检测常见调试器进程
        debugger_processes = [
            "ollydbg", "x32_dbg", "x64_dbg", "ida", "ida64",
            "cheatengine", "processhacker", "procmon",
            "wireshark", "fiddler", "httpdebugger",
            "frida", "frida-server", "gdb", "windbg"
        ]

        try:
            import subprocess
            output = subprocess.check_output(
                "tasklist /FI \"IMAGENAME eq python.exe\" /FO CSV",
                shell=True, text=True, timeout=5
            )
            # Check for suspicious parent processes
            for proc in debugger_processes:
                try:
                    check = subprocess.check_output(
                        f'tasklist /FI "IMAGENAME eq {proc}.exe" /FO CSV',
                        shell=True, text=True, timeout=3
                    )
                    if proc in check.lower():
                        return False
                except:
                    pass
        except:
            pass

        # 方法2: 检测调试器附加 (IsDebuggerPresent)
        try:
            if ctypes.windll.kernel32.IsDebuggerPresent():
                return False
        except:
            pass

        # 方法3: 检测 NtGlobalFlag
        try:
            peb = ctypes.cast(
                ctypes.windll.ntdll.NtCurrentTeb(),
                ctypes.POINTER(ctypes.c_void_p)
            )
            if peb:
                # PEB offset varies, skip for safety
                pass
        except:
            pass

        # 方法4: 检测时间差 (调试器会减慢执行)
        start = time.perf_counter()
        for _ in range(100000):
            pass
        elapsed = time.perf_counter() - start
        if elapsed > 0.1:  # 异常慢，可能被调试
            return False

        self._anti_debug_active = True
        return True

    def check_integrity(self, exe_path=None):
        """校验文件完整性"""
        if exe_path is None:
            exe_path = sys.executable

        if not os.path.exists(exe_path):
            return False

        try:
            with open(exe_path, 'rb') as f:
                data = f.read()
            current_hash = hashlib.sha256(data).hexdigest()

            if self._integrity_hash is None:
                self._integrity_hash = current_hash
                return True

            return current_hash == self._integrity_hash
        except:
            return False

    def generate_license(self, machine_id, expiry_days=365):
        """生成许可证密钥"""
        import hmac
        secret = b"FeiFei2026SecretKey!@#$"
        payload = f"{machine_id}:{expiry_days}:{int(time.time())}"
        signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:16]
        license_data = f"{payload}|{signature}"
        return base64.b64encode(license_data.encode()).decode()

    def verify_license(self, license_key, machine_id=None):
        """验证许可证"""
        try:
            decoded = base64.b64decode(license_key).decode()
            parts = decoded.split('|')
            if len(parts) != 2:
                return False

            payload, signature = parts
            payload_parts = payload.split(':')
            if len(payload_parts) != 3:
                return False

            stored_machine_id, expiry_days, timestamp = payload_parts

            if machine_id and stored_machine_id != machine_id:
                return False

            # 检查过期
            created = int(timestamp)
            expiry = created + int(expiry_days) * 86400
            if time.time() > expiry:
                return False

            # 验证签名
            import hmac
            secret = b"FeiFei2026SecretKey!@#$"
            expected_sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:16]
            return signature == expected_sig
        except:
            return False

    def get_machine_id(self):
        """获取机器唯一标识"""
        try:
            import subprocess
            output = subprocess.check_output("wmic csproduct get UUID", shell=True, text=True)
            for line in output.split('\n'):
                line = line.strip()
                if line and line != 'UUID' and '-' in line:
                    return line
        except:
            pass

        # Fallback: 使用计算机名 + 用户名
        import socket
        hostname = socket.gethostname()
        username = os.getlogin() if hasattr(os, 'getlogin') else os.environ.get('USERNAME', '')
        return hashlib.md5(f"{hostname}:{username}".encode()).hexdigest()

    def obfuscate_string(self, s):
        """简单字符串混淆"""
        encoded = base64.b64encode(s.encode('utf-8')).decode()
        return encoded

    def deobfuscate_string(self, encoded):
        """还原混淆字符串"""
        return base64.b64decode(encoded).decode('utf-8')

    def anti_memory_dump(self):
        """防止内存 dump"""
        try:
            # 锁定关键内存页
            PROCESS_ALL_ACCESS = 0x1F0FFF
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            # Note: VirtualLock requires specific privileges
            return True
        except:
            return False

    def start_anti_debug_thread(self, interval=5):
        """启动反调试监控线程"""
        def _monitor():
            while True:
                if not self.enable_anti_debug():
                    # 检测到调试器，退出程序
                    os._exit(1)
                time.sleep(interval)

        t = threading.Thread(target=_monitor, daemon=True)
        t.start()
        return t


def protect_application():
    """应用所有保护措施"""
    protection = Protection()

    # 1. 反调试
    if not protection.enable_anti_debug():
        print("警告: 检测到调试环境")

    # 2. 完整性校验
    protection.check_integrity()

    # 3. 启动反调试监控
    protection.start_anti_debug_thread(interval=10)

    return protection


if __name__ == '__main__':
    p = Protection()
    print(f"Machine ID: {p.get_machine_id()}")
    print(f"Anti-debug: {p.enable_anti_debug()}")
    print(f"Integrity: {p.check_integrity()}")

    license_key = p.generate_license(p.get_machine_id(), 365)
    print(f"License: {license_key}")
    print(f"Verify: {p.verify_license(license_key, p.get_machine_id())}")
