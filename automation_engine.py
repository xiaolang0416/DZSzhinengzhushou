"""
自动化引擎 - 核心游戏自动化逻辑
通过键盘/鼠标模拟和OpenCV画面识别实现精确自动化
"""
import time
import threading
import random
import os
import cv2
import numpy as np

from screen_recognizer import ScreenRecognizer


class AutomationEngine:
    def __init__(self):
        self._input_method = None
        self._recognizer = None
        self._templates_dir = "templates"
        self._init_input()
        self._init_recognizer()

    def _init_input(self):
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.05
            self._input_method = "pyautogui"
        except ImportError:
            self._input_method = None

    def _init_recognizer(self):
        try:
            self._recognizer = ScreenRecognizer()
            if os.path.isdir(self._templates_dir):
                for f in os.listdir(self._templates_dir):
                    if f.endswith((".png", ".jpg", ".bmp")):
                        name = os.path.splitext(f)[0]
                        self._recognizer.load_template(name, os.path.join(self._templates_dir, f))
        except Exception:
            self._recognizer = None

    def set_game_window(self, title):
        """通过窗口标题定位游戏区域"""
        if self._recognizer:
            return self._recognizer.set_game_region_by_title(title)
        return False

    def load_template(self, name, path):
        """加载识别模板"""
        if self._recognizer:
            return self._recognizer.load_template(name, path)
        return False

    def _press_key(self, key, duration=0.05):
        if self._input_method == "pyautogui":
            import pyautogui
            pyautogui.keyDown(key)
            time.sleep(duration)
            pyautogui.keyUp(key)

    def _click(self, x, y, button='left'):
        if self._input_method == "pyautogui":
            import pyautogui
            pyautogui.click(x, y, button=button)

    def _move_to(self, x, y, duration=0.2):
        if self._input_method == "pyautogui":
            import pyautogui
            pyautogui.moveTo(x, y, duration=duration)

    def _type_text(self, text, interval=0.05):
        if self._input_method == "pyautogui":
            import pyautogui
            pyautogui.typewrite(text, interval=interval)

    def _find_and_click(self, template_name, threshold=0.8, timeout=10):
        """找到模板并点击，返回是否成功"""
        if not self._recognizer:
            return False
        result = self._recognizer.wait_for_template(template_name, timeout, threshold)
        if result:
            self._click(result[0], result[1])
            return True
        return False

    def _find_template(self, template_name, threshold=0.8):
        """查找模板位置"""
        if not self._recognizer:
            return None
        return self._recognizer.find_template(template_name, threshold)

    def execute_task(self, task_name, params, running_flag, log_func):
        if task_name == "combat":
            self._run_combat(params, running_flag, log_func)
        elif task_name == "dungeon":
            self._run_dungeon(params, running_flag, log_func)
        elif task_name == "collection":
            self._run_collection(params, running_flag, log_func)
        elif task_name == "pvp_arena":
            self._run_pvp(params, running_flag, log_func)
        elif task_name == "equipment":
            self._run_equipment(params, running_flag, log_func)
        elif task_name == "tasks":
            self._run_tasks(params, running_flag, log_func)
        elif task_name == "auction":
            self._run_auction(params, running_flag, log_func)
        elif task_name == "auto_chat":
            self._run_auto_chat(params, running_flag, log_func)
        else:
            log_func(f"[未知任务] {task_name}")

    def _run_combat(self, params, running, log):
        keys = params.get("combat_keys", [])
        delay = params.get("delay_ms", 100) / 1000.0
        use_vision = params.get("use_vision", False)
        hp_color = params.get("hp_color")
        target_template = params.get("target_template")

        log(f"[战斗] 开始自动战斗，按键数: {len(keys)}，视觉识别: {'开' if use_vision else '关'}")
        prev_frame = None

        while running():
            if use_vision and self._recognizer:
                if self._recognizer.is_screen_changed(prev_frame):
                    prev_frame = self._recognizer.capture()

                    if hp_color:
                        hp_pos = self._recognizer.find_color(tuple(hp_color), tolerance=20)
                        if not hp_pos:
                            log("[战斗] 检测到血量低，使用恢复技能")
                            heal_key = params.get("heal_key", "1")
                            self._press_key(heal_key)
                            time.sleep(0.5)

                    if target_template:
                        target = self._find_template(target_template)
                        if target:
                            log(f"[战斗] 发现目标，切换攻击")
                            target_key = params.get("target_key", "tab")
                            self._press_key(target_key)
                            time.sleep(0.2)

            for key_info in keys:
                if not running():
                    break
                key = key_info.get("key", "")
                key_delay = key_info.get("delay", delay)
                if key:
                    self._press_key(key)
                    time.sleep(key_delay)

            target_key = params.get("target_key", "tab")
            if target_key:
                self._press_key(target_key)
                time.sleep(0.1)

            cycle_delay = params.get("cycle_delay", 0.5)
            time.sleep(cycle_delay)

    # 难度标签相对于副本选择窗口左边的偏移 (x_offset, y_offset)
    # 窗口左边 = 窗口最左侧边框, y = 难度标签行中心
    DIFFICULTY_OFFSETS = {
        "普通": (125, 170),
        "修炼": (185, 170),
        "赏金": (245, 170),
        "挑战": (305, 170),
        "活动": (365, 170),
        "狩猎": (425, 170),
        "极道": (485, 170),
    }

    def _find_dungeon_window_left(self, timeout=5):
        """动态检测副本选择窗口的左边位置（屏幕坐标）"""
        if not self._recognizer or not self._recognizer._game_region:
            return None
        gr = self._recognizer._game_region

        # 在游戏区域内截图
        screen = self._recognizer.capture()
        if screen is None:
            return None

        gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # 在标题栏区域（顶部30像素）扫描垂直边缘
        # 副本选择窗口的左边框是一条明显的深色垂直线
        title_zone = gray[5:35, :]
        edges = cv2.Canny(title_zone, 30, 100)

        # 对每一列求和，找边缘密度最高的列
        col_density = edges.sum(axis=0)

        # 从中间偏左开始找第一个高峰（窗口左边）
        # 副本选择窗口通常在游戏区域中部
        search_start = w // 4
        search_end = 3 * w // 4

        peak_col = search_start
        peak_val = 0
        for c in range(search_start, search_end):
            # 检查这个位置是否是局部高峰（左边框特征）
            window = col_density[max(0, c-3):c+4]
            if len(window) > 0 and col_density[c] > peak_val and col_density[c] > 20:
                peak_val = col_density[c]
                peak_col = c

        if peak_val > 20:
            # 转换为屏幕坐标
            screen_x = gr["left"] + peak_col
            return screen_x

        # 备用方案：尝试模板匹配"副本选择"标题
        title_pos = self._recognizer.find_template("dungeon_title_text", threshold=0.6)
        if title_pos:
            # 标题中心x减去标题宽度的一半再减去窗口左边距
            # 标题宽度约70px，窗口左边到标题左边约55px
            screen_x = title_pos[0] - 35 - 55
            return screen_x

        return None

    def select_difficulty(self, difficulty_name):
        """选择副本难度，动态检测窗口位置后点击"""
        if difficulty_name not in self.DIFFICULTY_OFFSETS:
            return False
        if not self._recognizer or not self._recognizer._game_region:
            return False

        window_left = self._find_dungeon_window_left()
        if window_left is None:
            print("[难度] 未检测到副本选择窗口")
            return False

        gr = self._recognizer._game_region
        x_off, y_off = self.DIFFICULTY_OFFSETS[difficulty_name]
        screen_x = window_left + x_off
        screen_y = gr["top"] + y_off
        self._click(screen_x, screen_y)
        print(f"[难度] 点击 {difficulty_name} at ({screen_x}, {screen_y}), 窗口左边={window_left}")
        return True

    def _run_dungeon(self, params, running, log):
        dungeons = params.get("dungeon_list", [])
        difficulty = params.get("difficulty", "普通")
        max_time = params.get("max_time_min", 9999) * 60
        start_time = time.time()
        use_vision = params.get("use_vision", False)

        log(f"[副本] 开始刷副本，共 {len(dungeons)} 个，难度: {difficulty}，视觉识别: {'开' if use_vision else '关'}")

        enter_template = "dungeon_enter" if use_vision else None
        complete_template = "dungeon_complete" if use_vision else None

        while running():
            elapsed = time.time() - start_time
            if elapsed > max_time:
                log("[副本] 达到设定时间，停止")
                break

            for dungeon in dungeons:
                if not running():
                    break
                log(f"[副本] 进入: {dungeon}")

                if use_vision and enter_template:
                    if self._find_and_click(enter_template, timeout=15):
                        log("[副本] 点击进入副本")
                    else:
                        self._press_key('enter')
                else:
                    self._press_key('enter')

                if use_vision and complete_template:
                    log("[副本] 等待副本完成...")
                    result = self._recognizer.wait_for_template(complete_template, timeout=300)
                    if result:
                        log(f"[副本] 检测到完成画面: {dungeon}")
                        self._click(result[0], result[1])
                    else:
                        log(f"[副本] 超时，继续: {dungeon}")
                else:
                    time.sleep(30)
                    log(f"[副本] 完成: {dungeon}")

            time.sleep(1)

    def enter_dungeon(self, dungeon_name, difficulty="普通", timeout=10):
        """完整副本选择流程：选副本 → 选难度 → 确定"""
        if not self._recognizer:
            return False

        # 1. 找到并点击副本名称
        template_name = f"dungeon_{dungeon_name}"
        log_msg = f"[副本] 查找副本: {dungeon_name}"
        print(log_msg)

        dungeon_pos = self._recognizer.wait_for_template(template_name, timeout, threshold=0.8)
        if not dungeon_pos:
            print(f"[副本] 未找到副本: {dungeon_name}")
            return False
        self._click(dungeon_pos[0], dungeon_pos[1])
        print(f"[副本] 已选择: {dungeon_name}")
        time.sleep(0.5)

        # 2. 选择难度
        if self.select_difficulty(difficulty):
            print(f"[副本] 难度: {difficulty}")
        else:
            print(f"[副本] 难度选择失败: {difficulty}")
        time.sleep(0.5)

        # 3. 点击确定按钮
        if self._find_and_click("dungeon_confirm_btn", threshold=0.8, timeout=5):
            print("[副本] 已确认进入")
            return True
        else:
            print("[副本] 未找到确定按钮")
            return False

    def _run_collection(self, params, running, log):
        coords = params.get("coordinates", [])
        mode = params.get("mode", "fixed_point")
        use_vision = params.get("use_vision", False)
        item_template = params.get("item_template") if use_vision else None

        log(f"[采集] 开始采集，模式: {mode}，坐标数: {len(coords)}")

        while running():
            if use_vision and item_template and self._recognizer:
                positions = self._recognizer.find_all_templates(item_template, threshold=0.7)
                if positions:
                    log(f"[采集] 发现 {len(positions)} 个采集目标")
                    for pos in positions:
                        if not running():
                            break
                        self._click(pos[0], pos[1])
                        time.sleep(0.5)
                        pickup_key = params.get("pickup_key", "z")
                        self._press_key(pickup_key)
                        time.sleep(1)
                else:
                    for coord in coords:
                        if not running():
                            break
                        x, y = coord
                        self._click(x, y)
                        time.sleep(0.5)
                        pickup_key = params.get("pickup_key", "z")
                        self._press_key(pickup_key)
                        time.sleep(2)
            else:
                for coord in coords:
                    if not running():
                        break
                    x, y = coord
                    self._click(x, y)
                    time.sleep(0.5)
                    pickup_key = params.get("pickup_key", "z")
                    self._press_key(pickup_key)
                    time.sleep(2)

            time.sleep(1)

    def _run_pvp(self, params, running, log):
        mode = params.get("pvp_mode", "solo")
        combat_keys = params.get("combat_keys", [])
        use_vision = params.get("use_vision", False)
        enemy_template = params.get("enemy_template") if use_vision else None

        log(f"[PvP] 开始斗技，模式: {mode}")

        while running():
            if use_vision and enemy_template and self._recognizer:
                enemy = self._find_template(enemy_template)
                if enemy:
                    log(f"[PvP] 发现敌人，集中攻击")

            for key_info in combat_keys:
                if not running():
                    break
                key = key_info.get("key", "")
                if key:
                    self._press_key(key)
                    time.sleep(0.1)
            time.sleep(0.5)

    def _run_equipment(self, params, running, log):
        action = params.get("action", "refine")
        target = params.get("target", "+8")
        use_vision = params.get("use_vision", False)

        log(f"[装备] 开始{action}，目标: {target}")

        success_template = f"{action}_success" if use_vision else None
        fail_template = f"{action}_fail" if use_vision else None

        while running():
            if use_vision and self._recognizer:
                if success_template:
                    result = self._recognizer.find_template(success_template, threshold=0.8)
                    if result:
                        log(f"[装备] {action}成功")
                        self._click(result[0], result[1])
                        continue

                if fail_template:
                    result = self._recognizer.find_template(fail_template, threshold=0.8)
                    if result:
                        log(f"[装备] {action}失败，重试")
                        self._click(result[0], result[1])
                        time.sleep(1)
                        continue

            log(f"[装备] 执行{action}操作...")
            time.sleep(2)

    def _run_tasks(self, params, running, log):
        tasks = params.get("task_list", [])
        use_vision = params.get("use_vision", False)

        log(f"[任务] 开始执行 {len(tasks)} 个任务")

        for task in tasks:
            if not running():
                break
            log(f"[任务] 执行: {task}")

            if use_vision and self._recognizer:
                task_template = f"task_{task}"
                if task_template in self._recognizer._templates:
                    result = self._recognizer.wait_for_template(task_template, timeout=30)
                    if result:
                        self._click(result[0], result[1])
                        log(f"[任务] 点击任务: {task}")

            time.sleep(5)
            log(f"[任务] 完成: {task}")

    def _run_auction(self, params, running, log):
        price = params.get("price", "")
        qty = params.get("qty", "")
        interval = params.get("interval", 5)
        use_vision = params.get("use_vision", False)

        log(f"[拍卖] 开始扫货，价格: {price}，数量: {qty}")

        item_template = params.get("item_template") if use_vision else None
        buy_button = "auction_buy" if use_vision else None

        while running():
            if use_vision and self._recognizer:
                if item_template:
                    items = self._recognizer.find_all_templates(item_template, threshold=0.7)
                    if items:
                        log(f"[拍卖] 发现 {len(items)} 个目标商品")
                        for item in items:
                            if not running():
                                break
                            self._click(item[0], item[1])
                            time.sleep(0.5)
                            if buy_button:
                                self._find_and_click(buy_button, timeout=3)
                            time.sleep(1)

            log("[拍卖] 搜索中...")
            time.sleep(interval)

    def _run_auto_chat(self, params, running, log):
        messages = params.get("messages", [])
        interval = params.get("interval", 90)

        log(f"[喊话] 开始自动喊话，间隔: {interval}秒")
        while running():
            for msg in messages:
                if not running():
                    break
                log(f"[喊话] {msg[:30]}...")
                time.sleep(1)
            time.sleep(interval)
