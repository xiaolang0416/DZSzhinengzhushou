"""
游戏画面识别模块 - 基于OpenCV
提供截图、模板匹配、颜色检测、区域识别等功能
"""
import cv2
import numpy as np
import mss
import time
import os


class ScreenRecognizer:
    def __init__(self):
        self._sct = mss.mss()
        self._monitor = self._sct.monitors[0]  # 全屏
        self._game_region = None
        self._templates = {}

    def set_game_region(self, x, y, w, h):
        """设置游戏窗口区域"""
        self._game_region = {"left": x, "top": y, "width": w, "height": h}

    def set_game_region_by_title(self, title, fixed_width=None, fixed_height=None):
        """通过窗口标题自动定位游戏区域，可选固定分辨率。
        游戏标题含实时变化的帧率(如 'fps:59 ...')，故对标题做归一化后模糊匹配，
        避免因帧率数字变化导致精确匹配失败。"""
        try:
            import pygetwindow as gw
            import re

            def normalize(t):
                # 去掉可变的帧率片段与多余空白，保留稳定部分用于匹配
                return re.sub(r"\s+", " ", re.sub(r"fps:\s*\d+", "", t or "")).strip()

            def is_valid(win):
                # 跳过最小化/无效窗口（坐标为 -32000 且尺寸异常小）
                return not (win.left <= -32000 or win.top <= -32000 or win.width < 100 or win.height < 100)

            target = normalize(title)
            candidates = []
            if target:
                for win in gw.getAllWindows():
                    if normalize(win.title) == target and is_valid(win):
                        candidates.append(win)
            # 归一化匹配失败时退回精确标题匹配
            if not candidates:
                candidates = [w for w in gw.getWindowsWithTitle(title) if is_valid(w)]
            if not candidates:
                return False

            win = candidates[0]
            w = fixed_width if fixed_width else win.width
            h = fixed_height if fixed_height else win.height
            self.set_game_region(win.left, win.top, w, h)
            return True
        except Exception:
            pass
        return False

    def capture(self, region=None):
        """截取屏幕区域，返回BGR格式的numpy数组，失败返回None"""
        try:
            if region is None:
                region = self._game_region or self._monitor
            with mss.mss() as sct:
                img = sct.grab(region)
            if img is None:
                return None
            return cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2BGR)
        except Exception:
            return None

    def save_screenshot(self, path, region=None):
        """保存截图到文件"""
        img = self.capture(region)
        cv2.imwrite(path, img)
        return path

    def load_template(self, name, path):
        """加载模板图片"""
        if os.path.exists(path):
            self._templates[name] = cv2.imread(path)
            return True
        return False

    def find_template(self, template_name, threshold=0.8, region=None):
        """
        模板匹配，返回匹配中心坐标或None
        threshold: 匹配置信度 0-1
        """
        if template_name not in self._templates:
            return None
        template = self._templates[template_name]
        screen = self.capture(region)
        if screen is None:
            return None
        try:
            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            if max_val >= threshold:
                h, w = template.shape[:2]
                cx = max_loc[0] + w // 2
                cy = max_loc[1] + h // 2
                return (cx, cy, max_val)
        except Exception:
            pass
        return None

    def find_all_templates(self, template_name, threshold=0.8, region=None):
        """查找所有匹配位置，返回坐标列表"""
        if template_name not in self._templates:
            return []
        template = self._templates[template_name]
        screen = self.capture(region)
        if screen is None:
            return []
        try:
            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= threshold)
            h, w = template.shape[:2]
            points = []
            for pt in zip(*locations[::-1]):
                points.append((pt[0] + w // 2, pt[1] + h // 2, result[pt[1], pt[0]]))
            return points
        except Exception:
            return []

    def find_color(self, color_bgr, tolerance=30, region=None):
        """
        查找特定颜色区域，返回中心坐标或None
        color_bgr: (B, G, R) 元组
        tolerance: 颜色容差
        """
        screen = self.capture(region)
        if screen is None:
            return None
        try:
            lower = np.array([max(0, c - tolerance) for c in color_bgr])
            upper = np.array([min(255, c + tolerance) for c in color_bgr])
            mask = cv2.inRange(screen, lower, upper)
            coords = np.where(mask > 0)
            if len(coords[0]) > 0:
                cy = int(np.mean(coords[0]))
                cx = int(np.mean(coords[1]))
                return (cx, cy)
        except Exception:
            pass
        return None

    def find_color_count(self, color_bgr, tolerance=30, region=None):
        """统计特定颜色像素数量"""
        screen = self.capture(region)
        if screen is None:
            return 0
        try:
            lower = np.array([max(0, c - tolerance) for c in color_bgr])
            upper = np.array([min(255, c + tolerance) for c in color_bgr])
            mask = cv2.inRange(screen, lower, upper)
            return int(cv2.countNonZero(mask))
        except Exception:
            return 0

    def detect_text_region(self, region=None):
        """检测画面中的文字区域（基于边缘密度）"""
        screen = self.capture(region)
        if screen is None:
            return None
        try:
            gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            return edges
        except Exception:
            return None

    def is_screen_changed(self, prev_frame, threshold=500):
        """检测画面是否发生变化（用于判断加载/战斗结束等）"""
        curr = self.capture()
        if prev_frame is None or curr is None:
            return True
        try:
            diff = cv2.absdiff(prev_frame, curr)
            return cv2.countNonZero(cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)) > threshold
        except Exception:
            return True

    def get_pixel(self, x, y):
        """获取指定坐标的像素颜色 (B, G, R)"""
        screen = self.capture()
        if screen is None:
            return None
        if 0 <= y < screen.shape[0] and 0 <= x < screen.shape[1]:
            return tuple(screen[y, x])
        return None

    def wait_for_template(self, template_name, timeout=30, threshold=0.8, interval=0.5):
        """等待模板出现，超时返回None"""
        start = time.time()
        while time.time() - start < timeout:
            result = self.find_template(template_name, threshold)
            if result:
                return result
            time.sleep(interval)
        return None

    def wait_for_color(self, color_bgr, timeout=30, tolerance=30, interval=0.5):
        """等待特定颜色出现"""
        start = time.time()
        while time.time() - start < timeout:
            result = self.find_color(color_bgr, tolerance)
            if result:
                return result
            time.sleep(interval)
        return None
