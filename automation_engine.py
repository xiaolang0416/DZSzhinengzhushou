"""
自动化引擎 - 核心游戏自动化逻辑
通过键盘/鼠标模拟和OpenCV画面识别实现精确自动化
"""
import time
import threading
import random
import os
import math
import cv2
import numpy as np

from screen_recognizer import ScreenRecognizer


class AutomationEngine:
    def __init__(self):
        self._input_method = None
        self._recognizer = None
        self._templates_dir = "templates"
        self._held_keys = []
        self._move_direction = None
        self._hp_region = None
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

    def set_game_window(self, title, fixed_width=None, fixed_height=None):
        if self._recognizer:
            return self._recognizer.set_game_region_by_title(title, fixed_width, fixed_height)
        return False

    def load_template(self, name, path):
        if self._recognizer:
            return self._recognizer.load_template(name, path)
        return False

    def _press_key(self, key, duration=0.05):
        if self._input_method == "pyautogui":
            import pyautogui
            pyautogui.keyDown(key)
            time.sleep(duration)
            pyautogui.keyUp(key)

    def _hold_key(self, key):
        if self._input_method == "pyautogui" and key not in self._held_keys:
            import pyautogui
            pyautogui.keyDown(key)
            self._held_keys.append(key)

    def _release_key(self, key):
        if self._input_method == "pyautogui" and key in self._held_keys:
            import pyautogui
            pyautogui.keyUp(key)
            self._held_keys.remove(key)

    def _release_all_keys(self):
        for key in list(self._held_keys):
            self._release_key(key)

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
        if not self._recognizer:
            return False
        result = self._recognizer.wait_for_template(template_name, timeout, threshold)
        if result:
            self._click(result[0], result[1])
            return True
        return False

    def _find_template(self, template_name, threshold=0.8):
        if not self._recognizer:
            return None
        return self._recognizer.find_template(template_name, threshold)

    def _move_character(self, direction, duration=1.0):
        """通过WASD方向键移动角色"""
        direction_keys = {
            "up": "up", "down": "down", "left": "left", "right": "right",
            "w": "w", "s": "s", "a": "a", "d": "d",
        }
        key = direction_keys.get(direction)
        if key:
            self._hold_key(key)
            time.sleep(duration)
            self._release_key(key)

    def _circle_walk(self, size=2, duration=4.0):
        """原地绕圈行走，用于聚怪或捡物"""
        step_time = duration / 4
        size_map = {0: 0.3, 1: 0.5, 2: 0.8, 3: 1.2, 4: 1.6}
        hold_time = size_map.get(size, 0.5)

        start = time.time()
        while time.time() - start < duration:
            self._move_character("up", hold_time)
            self._move_character("right", hold_time)
            self._move_character("down", hold_time)
            self._move_character("left", hold_time)

    def _random_jitter(self):
        """随机微小移动，防止角色卡住"""
        directions = ["up", "down", "left", "right"]
        direction = random.choice(directions)
        duration = random.uniform(0.2, 0.5)
        self._move_character(direction, duration)

    def _check_stuck(self, prev_frame, threshold=100):
        """检测角色是否卡住（画面长时间无变化）"""
        if not self._recognizer:
            return False
        curr = self._recognizer.capture()
        if prev_frame is None or curr is None:
            return False
        try:
            diff = cv2.absdiff(prev_frame, curr)
            change = cv2.countNonZero(cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY))
            return change < threshold
        except Exception:
            return False

    def set_hp_region(self, region):
        """设置角色HP条区域 (x, y, w, h)，相对游戏窗口；None或w<=0表示全屏检测"""
        if region and len(region) == 4 and region[2] > 0 and region[3] > 0:
            self._hp_region = tuple(int(v) for v in region)
        else:
            self._hp_region = None

    def calibrate_hp_region(self, log=None):
        """
        自动校准角色HP条区域：在游戏窗口左下角查找红色水平血条
        返回 (x, y, w, h) 相对游戏窗口坐标，失败返回 None
        """
        if not self._recognizer:
            return None
        try:
            screen = self._recognizer.capture()
            if screen is None:
                return None

            sh, sw = screen.shape[:2]
            # 角色HP条位于左下角：左45%宽、下30%高
            zone = screen[int(sh * 0.70):sh, 0:int(sw * 0.45)]
            hsv = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV)
            lower_red1 = np.array([0, 100, 80])
            upper_red1 = np.array([12, 255, 255])
            lower_red2 = np.array([168, 100, 80])
            upper_red2 = np.array([180, 255, 255])
            mask = cv2.bitwise_or(
                cv2.inRange(hsv, lower_red1, upper_red1),
                cv2.inRange(hsv, lower_red2, upper_red2)
            )
            # 纵向膨胀，把细红带补成完整血条高度
            mask = cv2.dilate(mask, np.ones((9, 1), np.uint8))

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            best = None
            best_w = 0
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                # HP条为水平长条：宽明显大于高，宽度在合理范围
                if w > h * 3 and 60 < w < sw * 0.4 and 2 <= h < 40:
                    if w > best_w:
                        best_w = w
                        best = (x, y + int(sh * 0.70), w, h)

            if best:
                self._hp_region = best
                if log:
                    log(f"[校准] HP条区域: x={best[0]}, y={best[1]}, w={best[2]}, h={best[3]}")
                return best
            if log:
                log("[校准] 未检测到HP条，请确认角色界面可见或手动填写区域")
            return None
        except Exception:
            return None

    def _check_hp(self, hp_color=(0, 0, 255), hp_region=None, threshold=0.3):
        """
        检测HP血量，返回当前HP比例 (0.0-1.0)
        使用已校准/指定区域时按"列占比"计算：血条从右向左消耗，含红色的列数/总列数=HP%
        无区域时退化为全屏红色像素占比（仅供参考）
        """
        if not self._recognizer:
            return 1.0

        try:
            screen = self._recognizer.capture()
            if screen is None:
                return 1.0

            region = hp_region or self._hp_region
            if not region:
                hp_bar = screen
                hsv = cv2.cvtColor(hp_bar, cv2.COLOR_BGR2HSV)
                mask = cv2.bitwise_or(
                    cv2.inRange(hsv, np.array([0, 100, 100]), np.array([12, 255, 255])),
                    cv2.inRange(hsv, np.array([168, 100, 100]), np.array([180, 255, 255]))
                )
                total = hp_bar.shape[0] * hp_bar.shape[1]
                return min(1.0, cv2.countNonZero(mask) / max(1, total))

            x, y, w, h = region
            hp_bar = screen[y:y+h, x:x+w]
            if hp_bar.size == 0:
                return 1.0

            hsv = cv2.cvtColor(hp_bar, cv2.COLOR_BGR2HSV)
            mask = cv2.bitwise_or(
                cv2.inRange(hsv, np.array([0, 100, 80]), np.array([12, 255, 255])),
                cv2.inRange(hsv, np.array([168, 100, 80]), np.array([180, 255, 255]))
            )
            # 列占比：每一列只要有红色就算有血
            col_has_red = np.any(mask > 0, axis=0)
            total_cols = col_has_red.shape[0]
            red_cols = int(np.count_nonzero(col_has_red))
            return min(1.0, red_cols / max(1, total_cols))
        except Exception:
            return 1.0

    def _auto_heal(self, hp_key="1", hp_threshold=0.3, log=None):
        """自动喝药恢复HP"""
        hp = self._check_hp()
        if hp < hp_threshold:
            if log:
                log(f"[恢复] HP过低 ({hp:.0%})，使用药品 {hp_key}")
            self._press_key(hp_key)
            time.sleep(0.5)
            return True
        return False

    def _recover_from_stuck(self, log):
        """从卡住状态恢复"""
        log("[恢复] 检测到卡住，尝试脱困")
        self._release_all_keys()

        self._press_key('space')
        time.sleep(0.3)

        directions = [("up", 1.0), ("down", 1.0), ("left", 1.5), ("right", 1.5)]
        for d, t in directions:
            if not self._is_stuck_resolved(log):
                self._move_character(d, t)
                time.sleep(0.2)
            else:
                break

        if self._is_stuck_resolved(log):
            log("[恢复] 脱困成功")
        else:
            log("[恢复] 脱困失败，尝试传送")
            self._press_key('enter')
            time.sleep(1)
            self._press_key('enter')
            time.sleep(1)

    def _is_stuck_resolved(self, log, threshold=500):
        """检测是否已脱困"""
        if not self._recognizer:
            return True
        try:
            frame1 = self._recognizer.capture()
            time.sleep(0.5)
            frame2 = self._recognizer.capture()
            if frame1 is None or frame2 is None:
                return True
            diff = cv2.absdiff(frame1, frame2)
            change = cv2.countNonZero(cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY))
            return change > threshold
        except Exception:
            return True

    def _check_combat_state(self, hp_threshold=0.95, recent_hp_history=None):
        """
        检测角色是否处于战斗状态
        通过HP变化和画面特征判断
        返回: (in_combat: bool, confidence: float)
        """
        if not self._recognizer:
            return False, 0.0

        try:
            current_hp = self._check_hp()

            # 方法1: HP低于阈值说明在战斗
            if current_hp < hp_threshold:
                return True, 0.8

            # 方法2: HP快速下降说明在战斗
            if recent_hp_history and len(recent_hp_history) >= 3:
                recent_avg = sum(recent_hp_history[-3:]) / 3
                if current_hp < recent_avg - 0.05:  # HP下降超过5%
                    return True, 0.7

            # 方法3: 检测画面中的战斗特征（怪物血条、战斗特效等）
            screen = self._recognizer.capture()
            if screen is not None:
                # 检测红色/橙色区域（怪物血条通常是红色）
                hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
                # 红色范围
                lower_red1 = np.array([0, 100, 100])
                upper_red1 = np.array([10, 255, 255])
                lower_red2 = np.array([160, 100, 100])
                upper_red2 = np.array([180, 255, 255])
                mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
                mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
                red_mask = cv2.bitwise_or(mask1, mask2)

                # 计算红色像素比例（排除HP条区域）
                h, w = red_mask.shape
                # 排除底部HP条区域（大约底部10%）
                red_mask[:int(h*0.85), :] = red_mask[:int(h*0.85), :]
                red_ratio = cv2.countNonZero(red_mask) / (h * w * 0.85)

                if red_ratio > 0.02:  # 红色区域超过2%
                    return True, 0.6

            return False, 0.0
        except Exception:
            return False, 0.0

    def _detect_monster_with_aggro(self, threshold=0.7):
        """
        检测屏幕上是否有仇恨怪物（正在攻击玩家的怪物）
        返回怪物位置 (x, y) 或 None
        """
        if not self._recognizer:
            return None

        try:
            screen = self._recognizer.capture()
            if screen is None:
                return None

            # 检测怪物血条（通常是红色长条）
            hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
            lower_red1 = np.array([0, 150, 150])
            upper_red1 = np.array([15, 255, 255])
            lower_red2 = np.array([165, 150, 150])
            upper_red2 = np.array([180, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
            mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
            red_mask = cv2.bitwise_or(mask1, mask2)

            # 排除底部HP条区域
            h, w = red_mask.shape
            red_mask[int(h * 0.85):, :] = 0
            # 排除左下角聊天框（红/橙色世界、系统文字会被误判为血条）
            red_mask[int(h * 0.55):, :int(w * 0.24)] = 0
            # 排除最左侧 HUD 竖条
            red_mask[:, :int(w * 0.03)] = 0
            # 排除右上角小地图区域
            red_mask[:int(h * 0.22), int(w * 0.82):] = 0

            # 查找红色区域（怪物血条）
            contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # 收集所有符合血条形状的候选，再挑选最优：优先靠近画面中心(角色附近)且面积较大的
            cx_screen, cy_screen = w / 2.0, h / 2.0
            best = None
            best_score = None
            for contour in contours:
                x, y, cw, ch = cv2.boundingRect(contour)
                # 怪物血条通常是长方形：宽度>高度，且宽度在合理范围
                if cw > ch and 30 < cw < 200 and 5 < ch < 30:
                    center_x = x + cw // 2
                    center_y = y + ch // 2
                    dist = ((center_x - cx_screen) ** 2 + (center_y - cy_screen) ** 2) ** 0.5
                    # 分数：越靠近中心、面积越大越优
                    score = cw * ch - dist * 2.0
                    if best_score is None or score > best_score:
                        best_score = score
                        best = (center_x, center_y)

            return best
        except Exception:
            return None

    def _move_toward_monster(self, monster_pos, log, move_duration=0.5):
        """
        根据怪物在屏幕上的位置，使用WASD方向键移动角色靠近怪物
        monster_pos: (x, y) 怪物在屏幕上的位置
        move_duration: 每个方向的移动持续时间（秒）
        """
        if not self._recognizer or not monster_pos:
            return

        try:
            screen = self._recognizer.capture()
            if screen is None:
                return

            screen_h, screen_w = screen.shape[:2]
            center_x = screen_w // 2
            center_y = screen_h // 2

            mon_x, mon_y = monster_pos

            dx = mon_x - center_x
            dy = mon_y - center_y

            # 判断水平方向
            if abs(dx) > screen_w * 0.1:
                if dx < 0:
                    log(f"[移动] 怪物在左侧，按A向左移动")
                    self._move_character("a", move_duration)
                else:
                    log(f"[移动] 怪物在右侧，按D向右移动")
                    self._move_character("d", move_duration)

            # 判断垂直方向
            if abs(dy) > screen_h * 0.1:
                if dy < 0:
                    log(f"[移动] 怪物在上方，按W向前移动")
                    self._move_character("w", move_duration)
                else:
                    log(f"[移动] 怪物在下方，按S向后移动")
                    self._move_character("s", move_duration)

            time.sleep(0.2)
        except Exception as e:
            log(f"[移动] 移动异常: {e}")

    def _auto_fight_monster(self, params, running, log, max_fight_time=60):
        """
        自动攻击怪物直到死亡
        params: 战斗参数
        running: 运行标志函数
        log: 日志函数
        max_fight_time: 最大战斗时间（秒）
        返回: True表示怪物已死亡，False表示超时或失败
        """
        combat_keys = params.get("combat_keys", [])
        hold_keys = params.get("hold_keys", [])
        normal_attack = params.get("normal_attack", "")
        target_key = params.get("target_key", "tab")
        hp_key = params.get("hp_key", "1")
        hp_threshold = params.get("hp_threshold", 0.3)

        fight_start = time.time()
        attack_count = 0
        last_hp = self._check_hp()

        log("[战斗] 开始攻击怪物")

        while running() and (time.time() - fight_start) < max_fight_time:
            # 自动喝药
            self._auto_heal(hp_key, hp_threshold, log)

            # 执行攻击循环
            for key in hold_keys:
                self._hold_key(key)

            for key_info in combat_keys:
                if not running():
                    break
                key = key_info.get("key", "")
                key_delay = key_info.get("delay", 0)
                if key:
                    self._press_key(key)
                    delay_sec = key_delay / 1000.0 if key_delay > 0 else 0.08
                    time.sleep(delay_sec)

            for key in hold_keys:
                self._release_key(key)

            if normal_attack:
                self._press_key(normal_attack)
                time.sleep(0.05)

            # 定期切换目标（确保攻击当前仇恨怪物）
            if attack_count % 10 == 0 and target_key:
                self._press_key(target_key)
                time.sleep(0.1)

            attack_count += 1

            # 检测怪物是否死亡（通过HP恢复或红色区域消失）
            current_hp = self._check_hp()
            if current_hp > last_hp + 0.05:  # HP恢复说明战斗结束
                log("[战斗] 怪物已死亡，HP恢复中")
                time.sleep(1)
                return True

            last_hp = current_hp

            # 检测是否还有怪物血条
            if attack_count % 20 == 0:
                monster_pos = self._detect_monster_with_aggro()
                if monster_pos is None:
                    # 再等一会确认
                    time.sleep(2)
                    monster_pos = self._detect_monster_with_aggro()
                    if monster_pos is None:
                        log("[战斗] 未检测到怪物血条，战斗结束")
                        return True

            time.sleep(0.3)

        if not running():
            log("[战斗] 收到停止指令，结束战斗")
        else:
            log(f"[战斗] 战斗超时 ({max_fight_time}秒)")
        return False

    def _run_auto_combat(self, params, running, log):
        """
        自动战斗模式：检测战斗状态，自动攻击仇恨怪物直到脱战
        """
        hp_key = params.get("hp_key", "1")
        hp_threshold = params.get("hp_threshold", 0.3)
        combat_keys = params.get("combat_keys", [])
        use_vision = params.get("use_vision", False)
        idle_check_interval = params.get("idle_check_interval", 2)  # 空闲检测间隔（秒）

        log("[自动战斗] 启动自动战斗模式，等待进入战斗...")
        log("[自动战斗] 按Tab切换目标，自动攻击直到脱战")

        recent_hp_history = []
        in_combat = False
        combat_count = 0

        try:
            while running():
                # 检测战斗状态
                current_hp = self._check_hp()
                recent_hp_history.append(current_hp)
                if len(recent_hp_history) > 10:
                    recent_hp_history.pop(0)

                combat_detected, confidence = self._check_combat_state(
                    hp_threshold=0.95,
                    recent_hp_history=recent_hp_history
                )

                if combat_detected and not in_combat:
                    # 进入战斗
                    in_combat = True
                    combat_count += 1
                    log(f"[自动战斗] 检测到战斗状态 (置信度: {confidence:.0%})，开始攻击！")

                    # 先检测仇恨怪物位置并移动靠近（血条会闪烁，多次采样直到捕捉到）
                    monster_pos = None
                    for _ in range(6):
                        if not running():
                            break
                        monster_pos = self._detect_monster_with_aggro()
                        if monster_pos:
                            break
                        time.sleep(0.25)
                    if monster_pos:
                        log(f"[自动战斗] 发现仇恨怪物 {monster_pos}，移动到怪物位置...")
                        self._move_toward_monster(monster_pos, log, move_duration=0.6)
                        time.sleep(0.3)
                        # 再次检测，确保靠近后继续移动
                        monster_pos2 = self._detect_monster_with_aggro()
                        if monster_pos2:
                            self._move_toward_monster(monster_pos2, log, move_duration=0.4)

                    # 自动攻击直到怪物死亡
                    monster_killed = self._auto_fight_monster(
                        params, running, log, max_fight_time=120
                    )

                    if monster_killed:
                        log(f"[自动战斗] 第{combat_count}次战斗结束，等待脱战...")
                    else:
                        log(f"[自动战斗] 第{combat_count}次战斗超时")

                    # 等待脱战（HP恢复）
                    wait_start = time.time()
                    while running() and (time.time() - wait_start) < 10:
                        current_hp = self._check_hp()
                        if current_hp > 0.95:  # HP恢复到95%以上
                            break
                        time.sleep(1)

                    in_combat = False
                    log("[自动战斗] 已脱战，继续监控...")

                elif not combat_detected and in_combat:
                    # 战斗结束
                    in_combat = False
                    log("[自动战斗] 战斗状态消失，已脱战")

                # 空闲时定期检测
                time.sleep(idle_check_interval)

        finally:
            self._release_all_keys()
            log(f"[自动战斗] 自动战斗模式结束，共完成{combat_count}次战斗")

    def execute_task(self, task_name, params, running_flag, log_func):
        hp_region = params.get("hp_region")
        if hp_region:
            self.set_hp_region(hp_region)
        if task_name == "combat":
            self._run_combat(params, running_flag, log_func)
        elif task_name == "auto_combat":
            self._run_auto_combat(params, running_flag, log_func)
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
        elif task_name == "one_click":
            self._run_one_click(params, running_flag, log_func)
        else:
            log_func(f"[未知任务] {task_name}")

    def _do_combat_cycle(self, params, running, log):
        """执行一次战斗循环：按键轮转 + 切换目标 + 拾取"""
        combat_keys = params.get("combat_keys", [])
        hold_keys = params.get("hold_keys", [])
        normal_attack = params.get("normal_attack", "")
        target_key = params.get("target_key", "tab")
        pickup_key = params.get("pickup_key", "z")

        for key in hold_keys:
            self._hold_key(key)

        for key_info in combat_keys:
            if not running():
                break
            key = key_info.get("key", "")
            key_delay = key_info.get("delay", 0)
            if key:
                self._press_key(key)
                delay_sec = key_delay / 1000.0 if key_delay > 0 else 0.08
                time.sleep(delay_sec)

        for key in hold_keys:
            self._release_key(key)

        if normal_attack:
            self._press_key(normal_attack)
            time.sleep(0.05)

        if target_key:
            self._press_key(target_key)
            time.sleep(0.1)

        if pickup_key:
            self._press_key(pickup_key)
            time.sleep(0.2)

    def _run_combat(self, params, running, log):
        combat_keys = params.get("combat_keys", [])
        hold_keys = params.get("hold_keys", [])
        use_vision = params.get("use_vision", False)
        hp_key = params.get("hp_key", "1")
        hp_threshold = params.get("hp_threshold", 0.3)

        log(f"[战斗] 开始自动战斗，按键数: {len(combat_keys)}，按住键: {hold_keys}，视觉: {'开' if use_vision else '关'}")

        prev_frame = None
        stuck_counter = 0
        cycle_count = 0

        try:
            while running():
                if use_vision and self._recognizer:
                    curr_frame = self._recognizer.capture()
                    if self._check_stuck(prev_frame):
                        stuck_counter += 1
                        if stuck_counter > 10:
                            self._recover_from_stuck(log)
                            stuck_counter = 0
                    else:
                        stuck_counter = 0
                    prev_frame = curr_frame

                self._auto_heal(hp_key, hp_threshold, log)

                self._do_combat_cycle(params, running, log)
                cycle_count += 1

                if cycle_count % 50 == 0:
                    self._random_jitter()

                cycle_delay = params.get("cycle_delay", 0.3)
                time.sleep(cycle_delay)
        finally:
            self._release_all_keys()

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
        if not self._recognizer or not self._recognizer._game_region:
            return None
        gr = self._recognizer._game_region

        screen = self._recognizer.capture()
        if screen is None:
            return None

        try:
            gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            h, w = gray.shape

            title_zone = gray[5:35, :]
            edges = cv2.Canny(title_zone, 30, 100)
            col_density = edges.sum(axis=0)

            search_start = w // 4
            search_end = 3 * w // 4

            peak_col = search_start
            peak_val = 0
            for c in range(search_start, search_end):
                window = col_density[max(0, c-3):c+4]
                if len(window) > 0 and col_density[c] > peak_val and col_density[c] > 20:
                    peak_val = col_density[c]
                    peak_col = c

            if peak_val > 20:
                screen_x = gr["left"] + peak_col
                return screen_x

            title_pos = self._recognizer.find_template("dungeon_title_text", threshold=0.6)
            if title_pos:
                screen_x = title_pos[0] - 35 - 55
                return screen_x
        except Exception:
            pass

        return None

    def select_difficulty(self, difficulty_name):
        if difficulty_name not in self.DIFFICULTY_OFFSETS:
            return False
        if not self._recognizer or not self._recognizer._game_region:
            return False

        window_left = self._find_dungeon_window_left()
        if window_left is None:
            return False

        gr = self._recognizer._game_region
        x_off, y_off = self.DIFFICULTY_OFFSETS[difficulty_name]
        screen_x = window_left + x_off
        screen_y = gr["top"] + y_off
        self._click(screen_x, screen_y)
        return True

    def enter_dungeon(self, dungeon_name, difficulty="普通", timeout=10):
        if not self._recognizer:
            return False

        template_name = f"dungeon_{dungeon_name}"
        dungeon_pos = self._recognizer.wait_for_template(template_name, timeout, threshold=0.8)
        if not dungeon_pos:
            return False
        self._click(dungeon_pos[0], dungeon_pos[1])
        time.sleep(0.5)

        self.select_difficulty(difficulty)
        time.sleep(0.5)

        if self._find_and_click("dungeon_confirm_btn", threshold=0.8, timeout=5):
            return True
        return False

    def _run_dungeon(self, params, running, log):
        dungeons = params.get("dungeon_list", [])
        max_time = params.get("max_time_min", 9999) * 60
        start_time = time.time()
        use_vision = params.get("use_vision", False)
        combat_keys = params.get("combat_keys", [])
        pickup_key = params.get("pickup_key", "z")
        target_key = params.get("target_key", "tab")
        hp_key = params.get("hp_key", "1")
        hp_threshold = params.get("hp_threshold", 0.3)

        log(f"[副本] 开始刷副本，共 {len(dungeons)} 个，视觉识别: {'开' if use_vision else '关'}")

        while running():
            elapsed = time.time() - start_time
            if elapsed > max_time:
                log("[副本] 达到设定时间，停止")
                break

            for dungeon_info in dungeons:
                if not running():
                    break

                name = dungeon_info.get("name", "")
                difficulty = dungeon_info.get("difficulty", "普通")
                count = dungeon_info.get("count", 1)

                for run_idx in range(count):
                    if not running():
                        break

                    log(f"[副本] 进入 {name} ({difficulty}) 第{run_idx+1}/{count}次")

                    if use_vision and self._recognizer:
                        success = self.enter_dungeon(name, difficulty)
                        if not success:
                            log(f"[副本] 进入失败: {name}，尝试键盘进入")
                            self._press_key('enter')
                    else:
                        self._press_key('enter')

                    time.sleep(3)

                    in_dungeon_time = time.time()
                    max_dungeon_time = 300
                    stuck_counter = 0
                    prev_frame = None

                    while running() and (time.time() - in_dungeon_time) < max_dungeon_time:
                        self._auto_heal(hp_key, hp_threshold, log)

                        if use_vision and self._recognizer:
                            curr_frame = self._recognizer.capture()
                            if self._check_stuck(prev_frame):
                                stuck_counter += 1
                                if stuck_counter > 10:
                                    self._recover_from_stuck(log)
                                    stuck_counter = 0
                            else:
                                stuck_counter = 0
                            prev_frame = curr_frame

                            if self._check_dungeon_complete():
                                log(f"[副本] 检测到副本完成: {name}")
                                break

                        self._do_combat_cycle(params, running, log)
                        time.sleep(0.3)

                    log(f"[副本] 完成: {name}")
                    self._exit_dungeon(log)
                    time.sleep(2)

                    if pickup_key:
                        self._press_key(pickup_key)
                        time.sleep(0.5)

            time.sleep(1)

    def _check_dungeon_complete(self):
        """检测副本是否完成（通过模板匹配或颜色检测）"""
        if not self._recognizer:
            return False

        result = self._recognizer.find_template("dungeon_complete", threshold=0.8)
        if result:
            return True

        result = self._recognizer.find_template("dungeon_exit_btn", threshold=0.8)
        if result:
            return True

        return False

    def _exit_dungeon(self, log):
        """退出副本"""
        log("[副本] 退出副本")
        if self._recognizer:
            if self._find_and_click("dungeon_exit_btn", timeout=3):
                time.sleep(1)
                self._find_and_click("dungeon_confirm_exit", timeout=3)
                time.sleep(1)
                return

        self._press_key('enter')
        time.sleep(1)
        self._press_key('enter')
        time.sleep(1)

    def _run_collection(self, params, running, log):
        coords = params.get("coordinates", [])
        mode = params.get("mode", "fixed_point")
        use_vision = params.get("use_vision", False)
        item_template = params.get("item_template") if use_vision else None
        pickup_key = params.get("pickup_key", "z")
        combat_keys = params.get("combat_keys", [])
        hp_key = params.get("hp_key", "1")
        hp_threshold = params.get("hp_threshold", 0.3)

        log(f"[采集] 开始采集，模式: {mode}，坐标数: {len(coords)}")

        if mode == "circle":
            circle_size = params.get("circle_size", 1)
            while running():
                self._auto_heal(hp_key, hp_threshold, log)
                self._circle_walk(size=circle_size, duration=6.0)
                if pickup_key:
                    self._press_key(pickup_key)
                    time.sleep(0.5)
                if combat_keys:
                    self._do_combat_cycle(params, running, log)
                time.sleep(1)

        elif mode == "coord_farm":
            sub_modes = params.get("sub_modes", ["monster"])
            circle_pickup = params.get("circle_pickup", False)
            while running():
                for coord in coords:
                    if not running():
                        break
                    x, y = coord
                    log(f"[采集] 移动到坐标 ({x}, {y})")

                    self._move_to_coordinate(x, y, log)
                    time.sleep(1)

                    if "monster" in sub_modes and combat_keys:
                        fight_start = time.time()
                        while running() and (time.time() - fight_start) < 30:
                            self._auto_heal(hp_key, hp_threshold, log)
                            self._do_combat_cycle(params, running, log)
                            time.sleep(0.3)

                    if pickup_key:
                        self._press_key(pickup_key)
                        time.sleep(0.5)

                    if circle_pickup:
                        self._circle_walk(size=1, duration=3.0)
                        if pickup_key:
                            self._press_key(pickup_key)

                time.sleep(1)

        else:
            while running():
                self._auto_heal(hp_key, hp_threshold, log)

                if use_vision and item_template and self._recognizer:
                    positions = self._recognizer.find_all_templates(item_template, threshold=0.7)
                    if positions:
                        log(f"[采集] 发现 {len(positions)} 个采集目标")
                        for pos in positions:
                            if not running():
                                break
                            self._click(pos[0], pos[1])
                            time.sleep(0.5)
                            if pickup_key:
                                self._press_key(pickup_key)
                            time.sleep(1)
                    else:
                        for coord in coords:
                            if not running():
                                break
                            x, y = coord
                            self._move_to_coordinate(x, y, log)
                            time.sleep(0.5)
                            if pickup_key:
                                self._press_key(pickup_key)
                            time.sleep(2)
                else:
                    for coord in coords:
                        if not running():
                            break
                        x, y = coord
                        self._move_to_coordinate(x, y, log)
                        time.sleep(0.5)
                        if pickup_key:
                            self._press_key(pickup_key)
                        time.sleep(2)

                if combat_keys:
                    self._do_combat_cycle(params, running, log)
                time.sleep(1)

    def _move_to_coordinate(self, x, y, log, timeout=10):
        """
        移动到指定坐标（相对于游戏窗口）
        通过点击地面实现移动
        """
        if not self._recognizer or not self._recognizer._game_region:
            self._click(x, y)
            return

        gr = self._recognizer._game_region
        screen_x = gr["left"] + x
        screen_y = gr["top"] + y

        self._click(screen_x, screen_y)
        log(f"[移动] 点击坐标 ({screen_x}, {screen_y})")

    def _run_pvp(self, params, running, log):
        pvp_mode = params.get("pvp_mode", "solo")
        arena_mode = params.get("arena_mode", "mode1")
        is_1v1 = params.get("is_1v1", False)
        combat_keys = params.get("combat_keys", [])
        hold_keys = params.get("hold_keys", [])
        target_key = params.get("target_key", "tab")
        block_key = params.get("block_key", "r")
        dodge_key = params.get("dodge_key", "e")
        use_vision = params.get("use_vision", False)
        hp_key = params.get("hp_key", "1")
        hp_threshold = params.get("hp_threshold", 0.3)

        log(f"[PvP] 开始斗技，模式: {pvp_mode}，{arena_mode}，1v1: {is_1v1}")

        cycle_count = 0
        try:
            while running():
                self._auto_heal(hp_key, hp_threshold, log)

                if use_vision and self._recognizer:
                    enemy = self._find_template("enemy_player")
                    if enemy:
                        log("[PvP] 发现对手，集中攻击")

                for key in hold_keys:
                    self._hold_key(key)

                for key_info in combat_keys:
                    if not running():
                        break
                    key = key_info.get("key", "")
                    key_delay = key_info.get("delay", 0)
                    if key:
                        self._press_key(key)
                        delay_sec = key_delay / 1000.0 if key_delay > 0 else 0.08
                        time.sleep(delay_sec)

                for key in hold_keys:
                    self._release_key(key)

                if target_key:
                    self._press_key(target_key)
                    time.sleep(0.1)

                cycle_count += 1
                if cycle_count % 20 == 0:
                    if block_key:
                        self._press_key(block_key)
                        time.sleep(0.2)
                    if dodge_key:
                        self._press_key(dodge_key)
                        time.sleep(0.2)

                time.sleep(0.3)
        finally:
            self._release_all_keys()

    def _run_equipment(self, params, running, log):
        action = params.get("action", "refine")
        target = params.get("target", "+8")
        use_vision = params.get("use_vision", False)

        log(f"[装备] 开始{action}，目标: {target}")

        if action == "refine":
            retreat = params.get("retreat", "2")
            refine_count = 0
            while running():
                log(f"[精炼] 第{refine_count+1}次精炼，目标: {target}")

                if use_vision and self._recognizer:
                    if self._find_and_click("refine_btn", timeout=5):
                        time.sleep(1)
                        success = self._find_template("refine_success", threshold=0.8)
                        if success:
                            refine_count += 1
                            log(f"[精炼] 成功! 次数: {refine_count}")
                        else:
                            fail = self._find_template("refine_fail", threshold=0.8)
                            if fail:
                                log("[精炼] 失败，重试")
                                time.sleep(1)
                    else:
                        log("[精炼] 未找到精炼按钮")
                        time.sleep(3)
                else:
                    self._press_key('enter')
                    time.sleep(2)
                    refine_count += 1

                time.sleep(1)

        elif action == "recast":
            attr1 = params.get("attr1", "攻击")
            attr2 = params.get("attr2", "防御")
            attr3 = params.get("attr3", "生命")
            skill_keep = params.get("skill_keep", "3")
            stop_count = params.get("stop_count", "2")
            recast_count = 0

            while running():
                log(f"[重铸] 第{recast_count+1}次，目标: {attr1}/{attr2}/{attr3}")
                if use_vision and self._recognizer:
                    if self._find_and_click("recast_btn", timeout=5):
                        time.sleep(1)
                        if self._find_and_click(f"recast_skill_{skill_keep}", timeout=3):
                            time.sleep(0.5)
                        if self._find_and_click("recast_confirm", timeout=3):
                            time.sleep(1)
                    else:
                        time.sleep(3)
                else:
                    self._press_key('enter')
                    time.sleep(2)
                recast_count += 1
                time.sleep(1)

        elif action == "jade_combine":
            jades = params.get("jades", [2, 3, 4, 5])
            for jade_level in jades:
                if not running():
                    break
                log(f"[合玉] 开始合成{jade_level}阶玉晶石")
                combine_count = 0
                while running() and combine_count < 50:
                    if use_vision and self._recognizer:
                        if self._find_and_click("jade_combine_btn", timeout=5):
                            time.sleep(1)
                            self._find_and_click("jade_confirm", timeout=3)
                            time.sleep(1)
                        else:
                            break
                    else:
                        self._press_key('enter')
                        time.sleep(2)
                    combine_count += 1
                log(f"[合玉] {jade_level}阶合成完成，共{combine_count}次")

    def _run_tasks(self, params, running, log):
        tasks = params.get("task_list", [])
        use_vision = params.get("use_vision", False)
        combat_keys = params.get("combat_keys", [])
        pickup_key = params.get("pickup_key", "z")
        follow_key = params.get("follow_key", "f5")
        team_key = params.get("team_key", "j")

        log(f"[任务] 开始执行 {len(tasks)} 个日常任务")

        for task_name in tasks:
            if not running():
                break
            log(f"[任务] 执行: {task_name}")

            handler = self._get_task_handler(task_name)
            if handler:
                handler(params, running, log)
            else:
                self._run_generic_task(task_name, params, running, log)

            if running():
                log(f"[任务] 完成: {task_name}")
                time.sleep(2)

    def _get_task_handler(self, task_name):
        handlers = {
            "声望任务": self._task_reputation,
            "三次斗技胜利": self._task_arena_wins,
            "好友对话": self._task_friend_chat,
            "商店购买": self._task_shop_buy,
            "刷活跃": self._task_activity,
            "切换角色": self._task_switch_char,
        }
        return handlers.get(task_name)

    def _run_generic_task(self, task_name, params, running, log):
        """通用任务执行：打开任务面板 → 寻找任务 → 自动寻路 → 完成"""
        use_vision = params.get("use_vision", False)
        combat_keys = params.get("combat_keys", [])
        pickup_key = params.get("pickup_key", "z")

        if use_vision and self._recognizer:
            task_template = f"task_{task_name}"
            result = self._recognizer.wait_for_template(task_template, timeout=10, threshold=0.7)
            if result:
                self._click(result[0], result[1])
                log(f"[任务] 找到任务NPC: {task_name}")
                time.sleep(1)
                self._find_and_click("task_accept_btn", timeout=5)
                time.sleep(1)

        if combat_keys:
            fight_start = time.time()
            while running() and (time.time() - fight_start) < 60:
                self._do_combat_cycle(params, running, log)
                time.sleep(0.3)

        if pickup_key:
            self._press_key(pickup_key)
        time.sleep(2)

    def _task_reputation(self, params, running, log):
        log("[声望] 打开声望面板")
        self._press_key('enter')
        time.sleep(2)
        if self._recognizer and params.get("use_vision"):
            self._find_and_click("reputation_complete", timeout=10)
        time.sleep(3)

    def _task_arena_wins(self, params, running, log):
        log("[斗技] 开始三次斗技胜利任务")
        wins = 0
        combat_keys = params.get("combat_keys", [])
        while running() and wins < 3:
            log(f"[斗技] 当前胜利: {wins}/3")
            self._press_key('enter')
            time.sleep(3)
            fight_start = time.time()
            while running() and (time.time() - fight_start) < 120:
                self._do_combat_cycle(params, running, log)
                time.sleep(0.3)
            wins += 1
            time.sleep(2)

    def _task_friend_chat(self, params, running, log):
        log("[好友] 执行好友对话任务")
        self._press_key('enter')
        time.sleep(1)
        for i in range(3):
            if not running():
                break
            self._press_key('enter')
            time.sleep(2)

    def _task_shop_buy(self, params, running, log):
        log("[商店] 执行商店购买任务")
        self._press_key('enter')
        time.sleep(2)
        if self._recognizer and params.get("use_vision"):
            self._find_and_click("shop_buy_btn", timeout=5)
        time.sleep(1)
        self._press_key('enter')
        time.sleep(2)

    def _task_activity(self, params, running, log):
        log("[活跃] 执行刷活跃任务")
        self._press_key('enter')
        time.sleep(3)

    def _task_switch_char(self, params, running, log):
        log("[切换] 执行切换角色")
        self._press_key('enter')
        time.sleep(5)

    def _run_auction(self, params, running, log):
        price = params.get("price", "")
        qty = params.get("qty", "")
        single_count = params.get("single_count", "")
        total_count = params.get("total_count", "")
        interval = params.get("interval", 5)
        use_vision = params.get("use_vision", False)

        log(f"[拍卖] 开始扫货，价格≤{price}，数量≥{qty}")

        scan_count = 0
        max_scan = int(total_count) if total_count else 999

        while running() and scan_count < max_scan:
            if use_vision and self._recognizer:
                items = self._recognizer.find_all_templates("auction_item", threshold=0.7)
                if items:
                    log(f"[拍卖] 发现 {len(items)} 个商品")
                    for item in items:
                        if not running():
                            break
                        self._click(item[0], item[1])
                        time.sleep(0.5)
                        if self._find_and_click("auction_buy", timeout=3):
                            scan_count += 1
                            log(f"[拍卖] 购买成功 ({scan_count})")
                        time.sleep(1)
                else:
                    log("[拍卖] 未找到目标商品")
            else:
                log("[拍卖] 搜索中...")

            time.sleep(interval)

        log(f"[拍卖] 扫货结束，共购买 {scan_count} 次")

    ONE_CLICK_HANDLERS = {
        "三次斗技1V1胜利": "arena_1v1_wins",
        "幻虚特产": "phantom_specialty",
        "末日任务": "doomsday_quest",
        "末日任务只采集": "doomsday_collect_only",
        "挖宝任务": "treasure_hunt",
        "秒2.8倍神之盘丝抢亲取经": "silk_road_event",
        "血战到底": "blood_battle",
        "智能战场": "smart_battlefield",
        "坐标打怪/采集": "coord_farm_collect",
        "混沌入侵刷经验": "chaos_invasion_exp",
        "古兽刷经验": "ancient_beast_exp",
        "行宫刷经验": "palace_exp",
        "混沌刻碑之眼": "chaos_stele_eye",
        "除夕炸年兽": "new_year_beast",
        "元宵猜灯谜": "lantern_riddles",
    }

    def _run_one_click(self, params, running, log):
        one_click_tasks = params.get("one_click_tasks", [])
        log(f"[一键任务] 开始执行 {len(one_click_tasks)} 个一键任务")

        for idx, task_name in enumerate(one_click_tasks):
            if not running():
                break
            log(f"[一键任务] ({idx+1}/{len(one_click_tasks)}) {task_name}")

            handler_key = self.ONE_CLICK_HANDLERS.get(task_name)
            if handler_key:
                handler = getattr(self, f"_one_click_{handler_key}", None)
                if handler:
                    handler(params, running, log)
                else:
                    log(f"[一键任务] {task_name} 暂未实现具体逻辑，使用通用流程")
                    self._one_click_generic(task_name, params, running, log)
            else:
                log(f"[一键任务] {task_name} 未知任务，使用通用流程")
                self._one_click_generic(task_name, params, running, log)

            if running():
                log(f"[一键任务] 完成: {task_name}")
                time.sleep(2)

    def _one_click_generic(self, task_name, params, running, log):
        combat_keys = params.get("combat_keys", [])
        pickup_key = params.get("pickup_key", "z")
        self._press_key('enter')
        time.sleep(3)
        if combat_keys:
            fight_start = time.time()
            while running() and (time.time() - fight_start) < 120:
                self._do_combat_cycle(params, running, log)
                time.sleep(0.3)
        if pickup_key:
            self._press_key(pickup_key)
        time.sleep(2)

    def _one_click_arena_1v1_wins(self, params, running, log):
        log("[一键] 三次斗技1V1胜利")
        wins = 0
        while running() and wins < 3:
            log(f"[一键] 斗技胜利 {wins}/3")
            self._press_key('enter')
            time.sleep(3)
            fight_start = time.time()
            while running() and (time.time() - fight_start) < 120:
                self._do_combat_cycle(params, running, log)
                time.sleep(0.3)
            wins += 1
            time.sleep(2)

    def _one_click_treasure_hunt(self, params, running, log):
        log("[一键] 挖宝任务")
        self._press_key('enter')
        time.sleep(2)
        pickup_key = params.get("pickup_key", "z")
        dig_start = time.time()
        while running() and (time.time() - dig_start) < 300:
            self._press_key('enter')
            time.sleep(1)
            if pickup_key:
                self._press_key(pickup_key)
            time.sleep(2)

    def _one_click_blood_battle(self, params, running, log):
        log("[一键] 血战到底")
        self._press_key('enter')
        time.sleep(3)
        fight_start = time.time()
        while running() and (time.time() - fight_start) < 600:
            self._do_combat_cycle(params, running, log)
            time.sleep(0.3)

    def _one_click_smart_battlefield(self, params, running, log):
        log("[一键] 智能战场")
        self._press_key('enter')
        time.sleep(3)
        fight_start = time.time()
        while running() and (time.time() - fight_start) < 600:
            self._do_combat_cycle(params, running, log)
            self._random_jitter()
            time.sleep(0.3)

    def _one_click_chaos_invasion_exp(self, params, running, log):
        log("[一键] 混沌入侵刷经验")
        self._press_key('enter')
        time.sleep(3)
        farm_start = time.time()
        while running() and (time.time() - farm_start) < 600:
            self._do_combat_cycle(params, running, log)
            time.sleep(0.3)
        pickup_key = params.get("pickup_key", "z")
        if pickup_key:
            self._press_key(pickup_key)

    def _one_click_ancient_beast_exp(self, params, running, log):
        log("[一键] 古兽刷经验")
        self._press_key('enter')
        time.sleep(3)
        farm_start = time.time()
        while running() and (time.time() - farm_start) < 600:
            self._do_combat_cycle(params, running, log)
            time.sleep(0.3)

    def _one_click_palace_exp(self, params, running, log):
        log("[一键] 行宫刷经验")
        self._press_key('enter')
        time.sleep(3)
        farm_start = time.time()
        while running() and (time.time() - farm_start) < 600:
            self._do_combat_cycle(params, running, log)
            time.sleep(0.3)

    def _one_click_doomsday_quest(self, params, running, log):
        log("[一键] 末日任务")
        self._press_key('enter')
        time.sleep(3)
        self._do_combat_cycle(params, running, log)
        time.sleep(2)
        self._press_key('enter')
        time.sleep(2)

    def _one_click_doomsday_collect_only(self, params, running, log):
        log("[一键] 末日任务只采集")
        pickup_key = params.get("pickup_key", "z")
        self._press_key('enter')
        time.sleep(3)
        collect_start = time.time()
        while running() and (time.time() - collect_start) < 300:
            if pickup_key:
                self._press_key(pickup_key)
            self._circle_walk(size=1, duration=3.0)
            time.sleep(1)

    def _one_click_phantom_specialty(self, params, running, log):
        log("[一键] 幻虚特产")
        self._press_key('enter')
        time.sleep(2)
        pickup_key = params.get("pickup_key", "z")
        collect_start = time.time()
        while running() and (time.time() - collect_start) < 300:
            if pickup_key:
                self._press_key(pickup_key)
            time.sleep(2)

    def _one_click_silk_road_event(self, params, running, log):
        log("[一键] 盘丝抢亲取经")
        self._press_key('enter')
        time.sleep(3)
        fight_start = time.time()
        while running() and (time.time() - fight_start) < 300:
            self._do_combat_cycle(params, running, log)
            time.sleep(0.3)

    def _one_click_coord_farm_collect(self, params, running, log):
        log("[一键] 坐标打怪/采集")
        self._run_collection(params, running, log)

    def _one_click_chaos_stele_eye(self, params, running, log):
        log("[一键] 混沌刻碑之眼")
        self._press_key('enter')
        time.sleep(3)
        fight_start = time.time()
        while running() and (time.time() - fight_start) < 300:
            self._do_combat_cycle(params, running, log)
            time.sleep(0.3)

    def _one_click_new_year_beast(self, params, running, log):
        log("[一键] 除夕炸年兽")
        self._press_key('enter')
        time.sleep(3)
        fight_start = time.time()
        while running() and (time.time() - fight_start) < 300:
            self._do_combat_cycle(params, running, log)
            time.sleep(0.3)

    def _one_click_lantern_riddles(self, params, running, log):
        log("[一键] 元宵猜灯谜")
        self._press_key('enter')
        time.sleep(2)
        time.sleep(3)

    def _run_auto_chat(self, params, running, log):
        messages = params.get("messages", [])
        interval = params.get("interval", 90)

        log(f"[喊话] 开始自动喊话，间隔: {interval}秒")
        while running():
            for msg in messages:
                if not running():
                    break
                log(f"[喊话] {msg[:30]}...")
                try:
                    import pyautogui
                    pyautogui.press('enter')
                    time.sleep(0.5)
                    # 使用剪贴板粘贴中文，typewrite不支持中文
                    import subprocess
                    subprocess.run(['clip'], input=msg.encode('utf-16le'), capture_output=True,
                                   shell=False, timeout=2)
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(0.3)
                    pyautogui.press('enter')
                except Exception as e:
                    log(f"[喊话] 发送失败: {e}")
                time.sleep(1)
            time.sleep(interval)
