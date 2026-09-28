"""
在线更新检查模块
检查远程版本并与本地版本对比，提示用户更新
"""
import json
import urllib.request
import urllib.error
import ssl
import os
import hashlib

APP_VERSION = "1.0.6"
APP_NAME = "小狼智能助手"


class Updater:
    def __init__(self, version_url=None, download_url=None):
        self.version_url = version_url or "https://example.com/feifei/version.json"
        self.download_url = download_url or "https://example.com/feifei/latest.exe"
        self._ctx = ssl.create_default_context()

    def check_update(self):
        """
        检查更新，返回字典:
        {
            "has_update": bool,
            "current_version": str,
            "latest_version": str,
            "download_url": str,
            "changelog": str,
            "error": str or None
        }
        """
        result = {
            "has_update": False,
            "current_version": APP_VERSION,
            "latest_version": APP_VERSION,
            "download_url": self.download_url,
            "changelog": "",
            "error": None,
        }

        try:
            req = urllib.request.Request(self.version_url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=10, context=self._ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            latest = data.get("version", APP_VERSION)
            result["latest_version"] = latest
            result["download_url"] = data.get("download_url", self.download_url)
            result["changelog"] = data.get("changelog", "")
            result["has_update"] = self._compare_versions(APP_VERSION, latest) < 0

        except urllib.error.URLError as e:
            result["error"] = f"网络错误: {e.reason}"
        except json.JSONDecodeError:
            result["error"] = "版本信息格式错误"
        except Exception as e:
            result["error"] = str(e)

        return result

    def download_update(self, dest_path, progress_callback=None):
        """
        下载更新文件到 dest_path
        progress_callback(current, total) 报告进度
        返回 True 表示成功
        """
        try:
            req = urllib.request.Request(self.download_url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=60, context=self._ctx) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total > 0:
                            progress_callback(downloaded, total)
            return True
        except Exception:
            if os.path.exists(dest_path):
                os.remove(dest_path)
            return False

    @staticmethod
    def _compare_versions(v1, v2):
        """比较版本号，返回 -1/0/1"""
        def parse(v):
            parts = []
            for p in v.split("."):
                try:
                    parts.append(int(p))
                except ValueError:
                    parts.append(0)
            return parts

        p1, p2 = parse(v1), parse(v2)
        while len(p1) < len(p2):
            p1.append(0)
        while len(p2) < len(p1):
            p2.append(0)
        for a, b in zip(p1, p2):
            if a < b:
                return -1
            if a > b:
                return 1
        return 0
