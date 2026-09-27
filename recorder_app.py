"""
小狼录制助手 - 独立录制后端
录制角色创建副本、进入副本、跑图、打怪、打Boss等操作
录制数据保存到"副本数据"目录，供前端自动化回放使用
"""
import sys
import os
import time
import threading
import shutil
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QGroupBox, QGridLayout,
    QMessageBox, QTextEdit, QListWidget, QListWidgetItem, QSplitter,
    QFrame, QStatusBar, QProgressBar, QCheckBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QColor, QIcon

from recorder_backend import DungeonRecorder
from automation_engine import AutomationEngine
from config_manager import ConfigManager

def _get_app_dir():
    """获取应用程序所在目录（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


_OCR_HELPER = os.path.join(_get_app_dir(), "ocr_helper.py")


def _find_system_python():
    """查找系统Python解释器路径（PyInstaller打包后sys.executable指向自身）"""
    if not getattr(sys, 'frozen', False):
        return sys.executable
    for name in ["python.exe", "python3.exe"]:
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(path_dir, name)
            if os.path.isfile(candidate):
                return candidate
    return None


def _ocr_via_subprocess(image, x=0, y=0, w=0, h=0):
    """通过子进程执行OCR，避免onnxruntime与PyQt5冲突"""
    import tempfile
    import subprocess
    python_exe = _find_system_python()
    if not python_exe:
        return []
    tmp_path = os.path.join(tempfile.gettempdir(), "_dzs_ocr_tmp.png")
    try:
        import cv2
        cv2.imwrite(tmp_path, image)
        cmd = [python_exe, _OCR_HELPER, tmp_path]
        if w > 0 and h > 0:
            cmd.extend([str(x), str(y), str(w), str(h)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return [line.strip() for line in result.stdout.strip().split("\n")
                    if line.strip() and not line.startswith("ERROR")]
    except Exception:
        pass
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return []


class RecorderApp(QMainWindow):
    """录制助手主窗口"""

    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.recorder = DungeonRecorder(save_dir="副本数据")
        self.engine = AutomationEngine()
        self.config = ConfigManager()
        self.is_recording = False
        self.auto_detect_enabled = False
        self.auto_phase_enabled = False
        self.auto_event_enabled = False
        self.detect_timer = QTimer()
        self.detect_timer.timeout.connect(self.auto_detect_dungeon)
        self._prev_frame = None
        self._last_detected_name = ""
        self._screen_change_count = 0
        self._current_phase = None
        self._hp_history = []
        self._was_in_combat = False
        self._was_in_boss = False
        self._red_bar_history = []
        self._settlement_detected = False
        self.init_ui()
        self._load_recorder_config()
        self.refresh_dungeon_list()

    def init_ui(self):
        self.setWindowTitle("小狼录制助手 - 独立录制后端")
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_dir, "app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setMinimumSize(700, 550)
        self.resize(750, 600)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # 录制控制区
        record_group = QGroupBox("副本录制控制")
        record_layout = QVBoxLayout()

        # 副本信息
        info_layout = QHBoxLayout()
        info_layout.addWidget(QLabel("副本名称:"))
        self.dungeon_name_input = QLineEdit()
        self.dungeon_name_input.setPlaceholderText("输入副本名称")
        self.dungeon_name_input.setFixedWidth(180)
        info_layout.addWidget(self.dungeon_name_input)

        info_layout.addWidget(QLabel("难度:"))
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItems(["普通", "修炼", "挑战", "赏金", "狩猎", "极道"])
        self.difficulty_combo.setFixedWidth(80)
        info_layout.addWidget(self.difficulty_combo)

        info_layout.addStretch()
        record_layout.addLayout(info_layout)

        # 自动识别选项
        auto_detect_layout = QHBoxLayout()
        self.auto_detect_checkbox = QCheckBox("自动识别副本名称（进入副本时自动检测）")
        self.auto_detect_checkbox.setChecked(False)
        self.auto_detect_checkbox.stateChanged.connect(self.toggle_auto_detect)
        auto_detect_layout.addWidget(self.auto_detect_checkbox)
        auto_detect_layout.addStretch()
        record_layout.addLayout(auto_detect_layout)

        # 录制按钮
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始录制 (F9)")
        self.start_btn.setFixedHeight(32)
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.start_btn.clicked.connect(self.start_recording)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止录制 (F10)")
        self.stop_btn.setFixedHeight(32)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
        self.stop_btn.clicked.connect(self.stop_recording)
        btn_layout.addWidget(self.stop_btn)

        self.status_label = QLabel("未录制")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        btn_layout.addWidget(self.status_label)

        btn_layout.addStretch()
        record_layout.addLayout(btn_layout)

        record_group.setLayout(record_layout)
        main_layout.addWidget(record_group)

        # 阶段控制区
        phase_group = QGroupBox("录制阶段 (录制中点击切换)")
        phase_layout_main = QVBoxLayout()

        phase_auto_layout = QHBoxLayout()
        self.auto_phase_checkbox = QCheckBox("自动识别阶段（根据战斗/跑图/结算自动切换）")
        self.auto_phase_checkbox.setChecked(False)
        self.auto_phase_checkbox.stateChanged.connect(self.toggle_auto_phase)
        phase_auto_layout.addWidget(self.auto_phase_checkbox)
        phase_auto_layout.addStretch()
        phase_layout_main.addLayout(phase_auto_layout)

        phase_btn_layout = QHBoxLayout()
        self.phase_buttons = {}
        phases = [
            ("角色创建", "character_creation"),
            ("进入副本", "dungeon_entry"),
            ("跑图", "map_running"),
            ("打怪", "combat"),
            ("打Boss", "boss_fight"),
            ("结算", "settlement"),
        ]
        for label, phase in phases:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setEnabled(False)
            btn.clicked.connect(lambda checked, p=phase: self.set_phase(p))
            phase_btn_layout.addWidget(btn)
            self.phase_buttons[phase] = btn

        phase_btn_layout.addStretch()
        phase_layout_main.addLayout(phase_btn_layout)
        phase_group.setLayout(phase_layout_main)
        main_layout.addWidget(phase_group)

        # 事件标记区
        event_group = QGroupBox("事件标记 (录制中点击标记)")
        event_layout_main = QVBoxLayout()

        event_auto_layout = QHBoxLayout()
        self.auto_event_checkbox = QCheckBox("自动标记事件（击杀/死亡/复活自动记录）")
        self.auto_event_checkbox.setChecked(False)
        self.auto_event_checkbox.stateChanged.connect(self.toggle_auto_event)
        event_auto_layout.addWidget(self.auto_event_checkbox)
        event_auto_layout.addStretch()
        event_layout_main.addLayout(event_auto_layout)

        event_btn_layout = QHBoxLayout()
        self.event_buttons = {}
        events = [
            ("击杀怪物", "monster_killed"),
            ("击杀Boss", "boss_killed"),
            ("收集物品", "item_collected"),
            ("死亡", "death"),
            ("复活", "revive"),
        ]
        for label, event in events:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setEnabled(False)
            btn.clicked.connect(lambda checked, e=event: self.mark_event(e))
            event_btn_layout.addWidget(btn)
            self.event_buttons[event] = btn

        event_btn_layout.addStretch()
        event_layout_main.addLayout(event_btn_layout)
        event_group.setLayout(event_layout_main)
        main_layout.addWidget(event_group)

        # 录制数据列表区
        data_group = QGroupBox("已录制副本数据")
        data_layout = QVBoxLayout()

        self.dungeon_list = QListWidget()
        self.dungeon_list.itemDoubleClicked.connect(self.on_dungeon_double_clicked)
        data_layout.addWidget(self.dungeon_list)

        list_btn_layout = QHBoxLayout()
        refresh_btn = QPushButton("刷新列表")
        refresh_btn.clicked.connect(self.refresh_dungeon_list)
        list_btn_layout.addWidget(refresh_btn)

        delete_btn = QPushButton("删除选中")
        delete_btn.clicked.connect(self.delete_selected)
        list_btn_layout.addWidget(delete_btn)

        open_dir_btn = QPushButton("打开数据目录")
        open_dir_btn.clicked.connect(self.open_data_directory)
        list_btn_layout.addWidget(open_dir_btn)

        list_btn_layout.addStretch()
        data_layout.addLayout(list_btn_layout)

        data_group.setLayout(data_layout)
        main_layout.addWidget(data_group)

        # 日志区
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # 状态栏
        self.statusBar().showMessage("就绪 - 输入副本名称后点击开始录制")

        self.log_signal.connect(self.append_log)
        self.append_log("[系统] 小狼录制助手已启动")
        self.append_log(f"[系统] 数据保存目录: {os.path.abspath('副本数据')}")

    def append_log(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {msg}")

    def toggle_auto_detect(self, state):
        """切换自动识别功能"""
        self.auto_detect_enabled = (state == Qt.Checked)
        self._save_recorder_config()
        if self.auto_detect_enabled:
            if not os.path.exists(_OCR_HELPER):
                self.auto_detect_checkbox.setChecked(False)
                self.auto_detect_enabled = False
                QMessageBox.warning(self, "警告",
                    "OCR辅助脚本未找到，无法自动识别副本名称\n"
                    "请确保 ocr_helper.py 存在")
                return
            if self.engine.set_game_window("斗战神"):
                self.detect_timer.start(2000)
                self._prev_frame = None
                self._screen_change_count = 0
                self._last_detected_name = ""
                self.append_log("[自动识别] 已启用，正在监控副本进入...")
            else:
                self.auto_detect_checkbox.setChecked(False)
                self.auto_detect_enabled = False
                QMessageBox.warning(self, "警告", "无法找到游戏窗口，请确保游戏已运行")
        else:
            self.detect_timer.stop()
            self._prev_frame = None
            self.append_log("[自动识别] 已禁用")

    def toggle_auto_phase(self, state):
        """切换自动阶段识别"""
        self.auto_phase_enabled = (state == Qt.Checked)
        self._save_recorder_config()
        if self.auto_phase_enabled:
            self._current_phase = None
            self._hp_history = []
            self._was_in_combat = False
            self._was_in_boss = False
            self._settlement_detected = False
            self.append_log("[阶段识别] 已启用，将自动切换录制阶段")
        else:
            self.append_log("[阶段识别] 已禁用")

    def toggle_auto_event(self, state):
        """切换自动事件标记"""
        self.auto_event_enabled = (state == Qt.Checked)
        self._save_recorder_config()
        if self.auto_event_enabled:
            self._red_bar_history = []
            self.append_log("[事件识别] 已启用，将自动标记击杀/死亡/复活事件")
        else:
            self.append_log("[事件识别] 已禁用")

    def _load_recorder_config(self):
        config_path = self.config.get_config_path()
        if os.path.exists(config_path):
            self.config.load(config_path)
        self.auto_detect_enabled = self.config.get_bool("自动识别副本名称")
        self.auto_phase_enabled = self.config.get_bool("自动识别阶段")
        self.auto_event_enabled = self.config.get_bool("自动标记事件")
        self.auto_detect_checkbox.blockSignals(True)
        self.auto_phase_checkbox.blockSignals(True)
        self.auto_event_checkbox.blockSignals(True)
        self.auto_detect_checkbox.setChecked(self.auto_detect_enabled)
        self.auto_phase_checkbox.setChecked(self.auto_phase_enabled)
        self.auto_event_checkbox.setChecked(self.auto_event_enabled)
        self.auto_detect_checkbox.blockSignals(False)
        self.auto_phase_checkbox.blockSignals(False)
        self.auto_event_checkbox.blockSignals(False)

    def _save_recorder_config(self):
        config_path = self.config.get_config_path()
        if os.path.exists(config_path):
            self.config.load(config_path)
        self.config.set_bool("自动识别副本名称", self.auto_detect_enabled)
        self.config.set_bool("自动识别阶段", self.auto_phase_enabled)
        self.config.set_bool("自动标记事件", self.auto_event_enabled)
        self.config.save(config_path)

    def closeEvent(self, event):
        self._save_recorder_config()
        super().closeEvent(event)

    def _detect_screen_change(self, screenshot):
        """检测画面是否发生显著变化（进入副本/loading等）"""
        import cv2
        if self._prev_frame is None:
            self._prev_frame = screenshot.copy()
            return False
        try:
            diff = cv2.absdiff(self._prev_frame, screenshot)
            change = cv2.countNonZero(cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY))
            self._prev_frame = screenshot.copy()
            if change > 50000:
                self._screen_change_count += 1
                return True
        except Exception:
            pass
        return False

    def _ocr_region(self, screenshot, x, y, w, h):
        """对截图指定区域执行OCR，返回识别到的文本列表"""
        return _ocr_via_subprocess(screenshot, x, y, w, h)

    def auto_detect_dungeon(self):
        """自动检测副本名称、阶段和事件"""
        if not (self.auto_detect_enabled or self.auto_phase_enabled or self.auto_event_enabled):
            return
        if not self.is_recording:
            return

        try:
            screenshot = self.engine._recognizer.capture() if self.engine._recognizer else None
            if screenshot is None:
                return

            if self.auto_detect_enabled:
                self._detect_dungeon_name(screenshot)

            if self.auto_phase_enabled:
                self._detect_phase(screenshot)

            if self.auto_event_enabled:
                self._detect_events(screenshot)

        except Exception as e:
            self.append_log(f"[自动识别] 检测错误: {e}")

    def _detect_dungeon_name(self, screenshot):
        """检测副本名称"""
        screen_changed = self._detect_screen_change(screenshot)
        if not screen_changed:
            return

        h, w = screenshot.shape[:2]
        regions = [
            (w // 4, 0, w // 2, int(h * 0.12)),
            (w // 5, int(h * 0.05), w * 3 // 5, int(h * 0.08)),
            (int(w * 0.15), int(h * 0.35), int(w * 0.7), int(h * 0.15)),
        ]

        all_text = []
        for rx, ry, rw, rh in regions:
            texts = self._ocr_region(screenshot, rx, ry, rw, rh)
            all_text.extend(texts)

        if not all_text:
            return

        dungeon_name = self._extract_dungeon_name(all_text)
        if dungeon_name and dungeon_name != self._last_detected_name:
            self._last_detected_name = dungeon_name
            self.dungeon_name_input.setText(dungeon_name)
            self.append_log(f"[自动识别] 检测到副本: {dungeon_name}")

    def _detect_phase(self, screenshot):
        """根据画面特征自动切换录制阶段"""
        import cv2
        import numpy as np

        h, w = screenshot.shape[:2]

        # 1. 检测结算界面 - 屏幕中央有大字"结算"/"奖励"/"完成"
        center_region = screenshot[int(h*0.3):int(h*0.6), int(w*0.2):int(w*0.8)]
        center_texts = self._ocr_region(screenshot, int(w*0.2), int(h*0.3), int(w*0.6), int(h*0.3))
        settlement_keywords = ["结算", "奖励", "完成", "获得", "经验", "金币"]
        is_settlement = any(any(kw in t for kw in settlement_keywords) for t in center_texts)

        if is_settlement:
            self._settlement_detected = True
            if self._current_phase != "settlement":
                self.set_phase("settlement")
                self.append_log("[阶段识别] 检测到结算界面")
            return

        # 2. 检测战斗状态 - 通过HP变化和红色区域
        current_hp = self._check_hp_color(screenshot)
        self._hp_history.append(current_hp)
        if len(self._hp_history) > 10:
            self._hp_history.pop(0)

        in_combat = self._is_in_combat(screenshot, current_hp)

        # 3. 检测Boss战 - 大红色血条（宽度较大）
        in_boss = self._detect_boss_bar(screenshot)

        if in_boss:
            if self._current_phase != "boss_fight":
                self.set_phase("boss_fight")
                self.append_log("[阶段识别] 检测到Boss战")
            self._was_in_combat = True
            self._was_in_boss = True
        elif in_combat:
            if self._current_phase != "combat":
                self.set_phase("combat")
                self.append_log("[阶段识别] 检测到战斗")
            self._was_in_combat = True
            self._was_in_boss = False
        elif self._was_in_combat and not in_combat:
            # 刚脱离战斗，回到跑图
            if self._current_phase != "map_running":
                self.set_phase("map_running")
                self.append_log("[阶段识别] 脱离战斗，进入跑图")
            self._was_in_combat = False
            self._was_in_boss = False
            self._hp_history = []
        elif self._current_phase is None:
            self.set_phase("map_running")

    def _detect_events(self, screenshot):
        """自动标记事件"""
        import cv2
        import numpy as np

        h, w = screenshot.shape[:2]

        # 检测红色区域（怪物/Boss血条）
        red_count = self._count_red_pixels(screenshot, exclude_bottom=0.15)
        self._red_bar_history.append(red_count)
        if len(self._red_bar_history) > 5:
            self._red_bar_history.pop(0)

        # 检测Boss血条消失 → Boss被击杀
        if self._was_in_boss:
            in_boss_now = self._detect_boss_bar(screenshot)
            if not in_boss_now and self._was_in_boss:
                self.mark_event("boss_killed")
                self.append_log("[事件识别] 检测到Boss被击杀")
                self._was_in_boss = False

        # 检测怪物血条消失 → 怪物被击杀
        elif self._was_in_combat:
            in_combat_now = self._is_in_combat(screenshot, self._hp_history[-1] if self._hp_history else 1.0)
            if not in_combat_now and len(self._red_bar_history) >= 3:
                avg_recent = sum(self._red_bar_history[-3:]) / 3
                if avg_recent < 500:  # 红色区域很少
                    self.mark_event("monster_killed")
                    self.append_log("[事件识别] 检测到怪物被击杀")

        # 检测死亡界面 - 屏幕中央有"死亡"文字
        death_texts = self._ocr_region(screenshot, int(w*0.3), int(h*0.35), int(w*0.4), int(h*0.15))
        if any("死亡" in t or "阵亡" in t for t in death_texts):
            self.mark_event("death")
            self.append_log("[事件识别] 检测到角色死亡")

        # 检测复活界面
        revive_texts = self._ocr_region(screenshot, int(w*0.3), int(h*0.35), int(w*0.4), int(h*0.15))
        if any("复活" in t for t in revive_texts):
            self.mark_event("revive")
            self.append_log("[事件识别] 检测到角色复活")

    def _check_hp_color(self, screenshot):
        """通过颜色检测HP比例"""
        import cv2
        import numpy as np
        h, w = screenshot.shape[:2]
        hp_region = screenshot[int(h*0.88):int(h*0.95), int(w*0.05):int(w*0.35)]
        if hp_region.size == 0:
            return 1.0
        try:
            hsv = cv2.cvtColor(hp_region, cv2.COLOR_BGR2HSV)
            lower_green = np.array([35, 100, 100])
            upper_green = np.array([85, 255, 255])
            mask = cv2.inRange(hsv, lower_green, upper_green)
            ratio = cv2.countNonZero(mask) / max(1, mask.size)
            return min(1.0, ratio * 5)
        except Exception:
            return 1.0

    def _is_in_combat(self, screenshot, current_hp):
        """检测是否处于战斗状态"""
        import cv2
        import numpy as np

        if current_hp < 0.95:
            return True

        if len(self._hp_history) >= 3:
            recent_avg = sum(self._hp_history[-3:]) / 3
            if current_hp < recent_avg - 0.03:
                return True

        h, w = screenshot.shape[:2]
        try:
            hsv = cv2.cvtColor(screenshot, cv2.COLOR_BGR2HSV)
            lower_red1 = np.array([0, 100, 100])
            upper_red1 = np.array([10, 255, 255])
            lower_red2 = np.array([160, 100, 100])
            upper_red2 = np.array([180, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
            mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
            red_mask = cv2.bitwise_or(mask1, mask2)
            red_mask[int(h*0.85):, :] = 0
            red_ratio = cv2.countNonZero(red_mask) / (h * w * 0.85)
            if red_ratio > 0.015:
                return True
        except Exception:
            pass

        return False

    def _detect_boss_bar(self, screenshot):
        """检测是否有Boss血条（宽红色长条）"""
        import cv2
        import numpy as np

        h, w = screenshot.shape[:2]
        try:
            hsv = cv2.cvtColor(screenshot, cv2.COLOR_BGR2HSV)
            lower_red1 = np.array([0, 120, 120])
            upper_red1 = np.array([12, 255, 255])
            lower_red2 = np.array([165, 120, 120])
            upper_red2 = np.array([180, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
            mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
            red_mask = cv2.bitwise_or(mask1, mask2)
            red_mask[int(h*0.85):, :] = 0

            contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, cw, ch = cv2.boundingRect(contour)
                if cw > 150 and ch > 8 and ch < 40 and cw > ch * 3:
                    return True
        except Exception:
            pass
        return False

    def _count_red_pixels(self, screenshot, exclude_bottom=0.15):
        """统计红色像素数量（排除底部HP条）"""
        import cv2
        import numpy as np
        h, w = screenshot.shape[:2]
        try:
            hsv = cv2.cvtColor(screenshot, cv2.COLOR_BGR2HSV)
            lower_red1 = np.array([0, 100, 100])
            upper_red1 = np.array([10, 255, 255])
            lower_red2 = np.array([160, 100, 100])
            upper_red2 = np.array([180, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
            mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
            red_mask = cv2.bitwise_or(mask1, mask2)
            exclude_h = int(h * exclude_bottom)
            if exclude_h > 0:
                red_mask[h-exclude_h:, :] = 0
            return int(cv2.countNonZero(red_mask))
        except Exception:
            return 0

    def _extract_dungeon_name(self, texts):
        """从OCR文本中提取副本名称"""
        skip_keywords = [
            "斗战神", "设置", "系统", "背包", "技能", "任务", "好友",
            "商城", "活动", "地图", "组队", "公会", "交易", "拍卖",
            "强化", "精炼", "重铸", "合玉", "日常", "声望",
        ]

        for text in texts:
            text = text.strip()
            if len(text) < 2:
                continue
            if any(kw in text for kw in skip_keywords):
                continue
            return text

        return None

    def start_recording(self):
        dungeon_name = self.dungeon_name_input.text().strip()
        if not dungeon_name:
            QMessageBox.warning(self, "警告", "请输入副本名称")
            return

        difficulty = self.difficulty_combo.currentText()

        if self.recorder.start_dungeon_recording(dungeon_name, difficulty):
            self.is_recording = True
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.dungeon_name_input.setEnabled(False)
            self.difficulty_combo.setEnabled(False)
            self.status_label.setText("录制中...")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")

            # 启用阶段和事件按钮
            for btn in self.phase_buttons.values():
                btn.setEnabled(True)
            for btn in self.event_buttons.values():
                btn.setEnabled(True)

            # 默认设置为"角色创建"阶段
            self.set_phase("character_creation")

            self.append_log(f"[录制] 开始录制: {dungeon_name} ({difficulty})")
            self.statusBar().showMessage(f"录制中: {dungeon_name}")

    def stop_recording(self):
        if self.recorder.stop_dungeon_recording():
            self.is_recording = False
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.dungeon_name_input.setEnabled(True)
            self.difficulty_combo.setEnabled(True)
            self.status_label.setText("录制完成")
            self.status_label.setStyleSheet("color: blue; font-weight: bold;")

            # 禁用阶段和事件按钮
            for btn in self.phase_buttons.values():
                btn.setEnabled(False)
                btn.setStyleSheet("")
            for btn in self.event_buttons.values():
                btn.setEnabled(False)

            self.append_log("[录制] 副本录制完成并保存")
            self.statusBar().showMessage("录制完成")
            self.refresh_dungeon_list()

    def set_phase(self, phase: str):
        if not self.is_recording:
            return

        self.recorder.set_phase(phase)
        self.append_log(f"[阶段] {phase}")

        # 更新按钮样式
        phase_names = {
            "character_creation": "角色创建",
            "dungeon_entry": "进入副本",
            "map_running": "跑图",
            "combat": "打怪",
            "boss_fight": "打Boss",
            "settlement": "结算",
        }
        for p, btn in self.phase_buttons.items():
            if p == phase:
                btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
            else:
                btn.setStyleSheet("")

    def mark_event(self, event: str):
        if not self.is_recording:
            return

        if event == "monster_killed":
            self.recorder.mark_monster_killed()
        elif event == "boss_killed":
            self.recorder.mark_boss_killed()
        elif event == "item_collected":
            self.recorder.mark_item_collected()
        elif event == "death":
            self.recorder.mark_death()
        elif event == "revive":
            self.recorder.mark_revive()

        self.append_log(f"[事件] {event}")

    def refresh_dungeon_list(self):
        self.dungeon_list.clear()
        dungeons = self.recorder.list_dungeons()

        for dungeon in dungeons:
            recordings = self.recorder.list_recordings(dungeon)
            item = QListWidgetItem(f"{dungeon} ({len(recordings)} 个录制)")
            item.setData(Qt.UserRole, dungeon)
            self.dungeon_list.addItem(item)

        if dungeons:
            self.append_log(f"[列表] 共 {len(dungeons)} 个副本")
        else:
            self.append_log("[列表] 暂无录制数据")

    def on_dungeon_double_clicked(self, item):
        dungeon_name = item.data(Qt.UserRole)
        if dungeon_name:
            recordings = self.recorder.list_recordings(dungeon_name)
            self.append_log(f"[查看] {dungeon_name}: {len(recordings)} 个录制文件")

    def delete_selected(self):
        current = self.dungeon_list.currentItem()
        if not current:
            QMessageBox.warning(self, "警告", "请选择要删除的副本")
            return

        dungeon_name = current.data(Qt.UserRole)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除副本 '{dungeon_name}' 的所有录制数据吗？\n此操作不可恢复！",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            dungeon_dir = self.recorder.save_dir / dungeon_name
            if dungeon_dir.exists():
                shutil.rmtree(dungeon_dir)
                self.append_log(f"[删除] {dungeon_name}")
                self.refresh_dungeon_list()

    def open_data_directory(self):
        data_dir = os.path.abspath("副本数据")
        os.makedirs(data_dir, exist_ok=True)
        os.startfile(data_dir)
        self.append_log(f"[目录] 已打开: {data_dir}")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 设置全局样式
    app.setStyleSheet("""
        QMainWindow {
            background-color: #f5f5f5;
        }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #ddd;
            border-radius: 4px;
            margin-top: 10px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }
        QPushButton {
            border: 1px solid #ccc;
            border-radius: 3px;
            padding: 5px 10px;
            background-color: white;
        }
        QPushButton:hover {
            background-color: #e8e8e8;
        }
        QPushButton:pressed {
            background-color: #d0d0d0;
        }
        QPushButton:disabled {
            background-color: #f0f0f0;
            color: #999;
        }
        QLineEdit, QComboBox {
            border: 1px solid #ccc;
            border-radius: 3px;
            padding: 4px;
            background-color: white;
        }
        QListWidget {
            border: 1px solid #ccc;
            border-radius: 3px;
            background-color: white;
        }
        QTextEdit {
            border: 1px solid #ccc;
            border-radius: 3px;
            background-color: white;
        }
    """)

    window = RecorderApp()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
