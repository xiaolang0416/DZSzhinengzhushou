"""
PyInstaller 打包脚本 - 小狼录制助手（独立后端）
"""
import os
import sys
import subprocess

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_DIR, "dist_recorder")
BUILD_DIR = os.path.join(PROJECT_DIR, "build_recorder")

MAIN_SCRIPT = "recorder_app.py"
APP_NAME = "小狼录制助手"


def build():
    os.makedirs(DIST_DIR, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name", APP_NAME,
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
        "--specpath", PROJECT_DIR,
        "--hidden-import", "pynput",
        "--hidden-import", "pynput.keyboard",
        "--hidden-import", "pynput.mouse",
        "--hidden-import", "pyHook",
        "--hidden-import", "pythoncom",
        "--hidden-import", "recorder_backend",
        "--hidden-import", "automation_engine",
        "--hidden-import", "screen_recognizer",
        "--hidden-import", "cv2",
        "--hidden-import", "numpy",
        "--hidden-import", "mss",
        "--hidden-import", "pyautogui",
        "--hidden-import", "pygetwindow",
        MAIN_SCRIPT,
    ]

    print(f"执行打包命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_DIR)

    if result.returncode == 0:
        exe_path = os.path.join(DIST_DIR, APP_NAME, f"{APP_NAME}.exe")
        helper_src = os.path.join(PROJECT_DIR, "ocr_helper.py")
        helper_dst = os.path.join(DIST_DIR, APP_NAME, "ocr_helper.py")
        if os.path.exists(helper_src):
            import shutil
            shutil.copy2(helper_src, helper_dst)
            print(f"已复制 OCR 辅助脚本: {helper_dst}")
        config_src = os.path.join(PROJECT_DIR, "uservar.ini")
        config_dst = os.path.join(DIST_DIR, APP_NAME, "uservar.ini")
        if os.path.exists(config_src) and not os.path.exists(config_dst):
            import shutil
            shutil.copy2(config_src, config_dst)
            print(f"已复制默认配置: {config_dst}")
        print(f"\n打包成功!")
        print(f"输出目录: {os.path.join(DIST_DIR, APP_NAME)}")
        print(f"可执行文件: {exe_path}")
    else:
        print(f"\n打包失败，返回码: {result.returncode}")

    return result.returncode


if __name__ == "__main__":
    sys.exit(build())
