"""
PyInstaller 打包脚本
将小狼智能助手打包为独立 exe
"""
import os
import sys
import subprocess

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_DIR, "dist_new14")
BUILD_DIR = os.path.join(PROJECT_DIR, "build")
SPEC_FILE = os.path.join(PROJECT_DIR, "feifei_assistant.spec")

MAIN_SCRIPT = "feifei_assistant.py"
APP_NAME = "小狼智能助手1.0.6"


def build():
    os.makedirs(DIST_DIR, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--uac-admin",
        "--name", APP_NAME,
        "--icon", "app_icon.ico",
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
        "--specpath", PROJECT_DIR,
        "--add-data", "templates;templates",
        "--hidden-import", "cv2",
        "--hidden-import", "numpy",
        "--hidden-import", "mss",
        "--hidden-import", "pyautogui",
        "--hidden-import", "pygetwindow",
        "--hidden-import", "PyQt5.QtWidgets",
        "--hidden-import", "PyQt5.QtCore",
        "--hidden-import", "PyQt5.QtGui",
        MAIN_SCRIPT,
    ]

    print(f"执行打包命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_DIR)

    if result.returncode == 0:
        exe_path = os.path.join(DIST_DIR, APP_NAME, f"{APP_NAME}.exe")
        icon_src = os.path.join(PROJECT_DIR, "app_icon.ico")
        icon_dst = os.path.join(DIST_DIR, APP_NAME, "app_icon.ico")
        if os.path.exists(icon_src):
            import shutil
            shutil.copy2(icon_src, icon_dst)
            print(f"已复制应用图标: {icon_dst}")
        print(f"\n打包成功!")
        print(f"输出目录: {os.path.join(DIST_DIR, APP_NAME)}")
        print(f"可执行文件: {exe_path}")
    else:
        print(f"\n打包失败，返回码: {result.returncode}")

    return result.returncode


if __name__ == "__main__":
    sys.exit(build())
