"""
小狼智能助手1.0.6-斗战神
基于原始 uservar.ini 配置结构和功能说明重新实现
"""
import sys
import os
import json
import time
import threading
import configparser
import ctypes
import ctypes.wintypes
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QCheckBox, QLineEdit, QPushButton, QComboBox,
    QSpinBox, QListWidget, QListWidgetItem, QGroupBox, QGridLayout,
    QMessageBox, QSystemTrayIcon, QMenu, QAction, QFileDialog,
    QTextEdit, QRadioButton, QButtonGroup, QHeaderView, QTableWidget,
    QTableWidgetItem, QSplitter, QFrame, QStatusBar
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, QSize
from PyQt5.QtGui import QIcon, QFont, QColor, QPalette

from automation_engine import AutomationEngine
from config_manager import ConfigManager
from protection import Protection
from updater import Updater, APP_VERSION

WM_HOTKEY = 0x0312
MOD_NONE = 0x0000
VK_F10 = 0x79
VK_F11 = 0x7A
HOTKEY_START = 1
HOTKEY_STOP = 2

user32 = ctypes.windll.user32


class GameOverlay(QWidget):
    """游戏吸附悬浮窗，显示在游戏窗口左上角"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
            Qt.Tool | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(5, 5, 5, 5)
        self._layout.setSpacing(2)

        self.version_label = QLabel(f"Ver:{APP_VERSION}")
        self.version_label.setStyleSheet("color: #00ff00; font-size: 13px; font-weight: bold;")

        self.time_label = QLabel("")
        self.time_label.setStyleSheet("color: #ff3333; font-size: 13px; font-weight: bold;")

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #ff69b4; font-size: 13px; font-weight: bold;")

        self.task_label = QLabel("")
        self.task_label.setStyleSheet("color: #ff3333; font-size: 13px; font-weight: bold;")

        self._layout.addWidget(self.version_label)
        self._layout.addWidget(self.time_label)
        self._layout.addWidget(self.status_label)
        self._layout.addWidget(self.task_label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_time)

        self._pos_timer = QTimer(self)
        self._pos_timer.timeout.connect(self._reposition)
        self._game_hwnd = None

    def start_overlay(self, game_hwnd=None):
        self._game_hwnd = game_hwnd
        self._update_time()
        self.status_label.setText("正常运行中")
        self.task_label.setText("")
        self._timer.start(1000)
        self._pos_timer.start(500)
        self._reposition()
        self.show()

    def stop_overlay(self):
        self._timer.stop()
        self._pos_timer.stop()
        self.hide()

    def set_task_text(self, text):
        self.task_label.setText(text)

    def set_status_text(self, text):
        self.status_label.setText(text)

    def _update_time(self):
        self.time_label.setText(time.strftime("%H:%M:%S"))

    def _reposition(self):
        if self._game_hwnd:
            rect = ctypes.wintypes.RECT()
            if user32.GetWindowRect(self._game_hwnd, ctypes.byref(rect)):
                self.move(rect.left + 10, rect.top + 10)
                return
        try:
            import pygetwindow as gw
            game_title = "斗战神"
            if self.parent() and hasattr(self.parent(), 'game_title_input'):
                t = self.parent().game_title_input.text().strip()
                if t:
                    game_title = t
            wins = gw.getWindowsWithTitle(game_title)
            if wins:
                w = wins[0]
                self.move(w.left + 10, w.top + 10)
        except Exception:
            pass


class WorkerThread(QThread):
    """后台工作线程，执行自动化任务"""
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)

    def __init__(self, engine, task_name, params):
        super().__init__()
        self.engine = engine
        self.task_name = task_name
        self.params = params
        self._running = True

    def run(self):
        try:
            self.log_signal.emit(f"[启动] {self.task_name}")
            self.status_signal.emit(f"正在执行: {self.task_name}")
            self.engine.execute_task(self.task_name, self.params, lambda: self._running, self.log_signal.emit)
            self.finished_signal.emit(True)
        except Exception as e:
            self.log_signal.emit(f"[错误] {e}")
            self.finished_signal.emit(False)

    def stop(self):
        self._running = False


class TaskChainWorker(QThread):
    """按顺序执行多个自动化任务"""
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)

    def __init__(self, engine, task_chain):
        super().__init__()
        self.engine = engine
        self.task_chain = task_chain  # [(task_name, params), ...]
        self._running = True

    def run(self):
        try:
            total = len(self.task_chain)
            for idx, (task_name, params) in enumerate(self.task_chain):
                if not self._running:
                    break
                self.log_signal.emit(f"[任务链] ({idx+1}/{total}) 开始: {task_name}")
                self.status_signal.emit(f"({idx+1}/{total}) {task_name}")
                self.engine.execute_task(task_name, params, lambda: self._running, self.log_signal.emit)
                if self._running:
                    self.log_signal.emit(f"[任务链] 完成: {task_name}")
            self.finished_signal.emit(True)
        except Exception as e:
            self.log_signal.emit(f"[任务链错误] {e}")
            self.finished_signal.emit(False)

    def stop(self):
        self._running = False


class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.config = ConfigManager()
        self.engine = AutomationEngine()
        self.protection = Protection()
        self.worker = None
        self.running_tasks = {}
        self.overlay = GameOverlay(self)

        self.log_signal.connect(self.append_log)
        self.init_ui()
        self.load_config()
        self._apply_config_to_gui()

        if not self.protection.enable_anti_debug():
            self.log_signal.emit("[安全] 警告: 检测到调试环境")
        self.protection.start_anti_debug_thread(interval=10)
        self.log_signal.emit("[安全] 保护已启用")

        self._register_hotkeys()

        QTimer.singleShot(1500, self._startup_auto_detect)

    def _startup_auto_detect(self):
        self.auto_detect_game_window()

    def init_ui(self):
        self.setWindowTitle("小狼智能助手1.0.6-斗战神")
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_dir, "app_icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setMinimumSize(750, 580)
        self.resize(850, 650)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self.create_combat_tab()
        self.create_dungeon_tab()
        self.create_pvp_tab()
        self.create_equipment_tab()
        self.create_task_tab()
        self.create_misc_tab()
        self.create_auction_tab()
        self.create_chat_tab()
        self.create_settings_tab()

        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("启动 (F10)")
        self.start_btn.setFixedHeight(35)
        self.start_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        self.start_btn.clicked.connect(self.toggle_start)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止 (F11)")
        self.stop_btn.setFixedHeight(35)
        self.stop_btn.setFont(QFont("Microsoft YaHei", 11))
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white;")
        self.stop_btn.clicked.connect(self.stop_all)
        btn_layout.addWidget(self.stop_btn)

        self.save_btn = QPushButton("保存配置")
        self.save_btn.setFixedHeight(35)
        self.save_btn.setFont(QFont("Microsoft YaHei", 11))
        self.save_btn.setStyleSheet("background-color: #2196F3; color: white;")
        self.save_btn.clicked.connect(self.save_config_auto)
        btn_layout.addWidget(self.save_btn)

        self.status_label = QLabel("就绪")
        self.status_label.setAlignment(Qt.AlignCenter)
        btn_layout.addWidget(self.status_label)
        main_layout.addLayout(btn_layout)

        self.statusBar().showMessage("小狼智能助手1.0.6-斗战神 - 就绪")

    def create_combat_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # 点击按键设置
        click_group = QGroupBox("在正方形中设置战斗中点击一下的按键")
        click_layout = QVBoxLayout()
        click_layout.setSpacing(2)
        click_layout.setContentsMargins(4, 4, 4, 4)

        click_keys_layout = QGridLayout()
        click_keys_layout.setHorizontalSpacing(0)
        click_keys_layout.setContentsMargins(0, 0, 0, 0)
        self.click_keys = {}
        for i in range(1, 6):
            label = QLabel(f"按键:")
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            click_keys_layout.addWidget(label, 0, (i-1)*2)
            key_edit = QLineEdit()
            key_edit.setFixedSize(30, 30)
            click_keys_layout.addWidget(key_edit, 0, (i-1)*2+1)
            self.click_keys[i] = key_edit
        for i in range(5):
            click_keys_layout.setColumnStretch(i*2, 1)
            click_keys_layout.setColumnStretch(i*2+1, 0)
        click_layout.addLayout(click_keys_layout)

        click_delay_layout = QGridLayout()
        click_delay_layout.setHorizontalSpacing(0)
        click_delay_layout.setContentsMargins(0, 0, 0, 0)
        self.click_delays = {}
        for i in range(1, 6):
            label = QLabel(f"套秒:")
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            click_delay_layout.addWidget(label, 0, (i-1)*2)
            delay_edit = QLineEdit("0")
            delay_edit.setFixedSize(30, 30)
            click_delay_layout.addWidget(delay_edit, 0, (i-1)*2+1)
            self.click_delays[i] = delay_edit
        for i in range(5):
            click_delay_layout.setColumnStretch(i*2, 1)
            click_delay_layout.setColumnStretch(i*2+1, 0)
        click_layout.addLayout(click_delay_layout)

        click_group.setLayout(click_layout)
        layout.addWidget(click_group)

        # 绕圈设置
        circle_group = QGroupBox("打怪后捡东西绕圈设置")
        circle_layout = QHBoxLayout()
        circle_layout.addWidget(QLabel("绕圈大小(1最小):"))
        self.circle_size_combo = QComboBox()
        self.circle_size_combo.addItems(["1", "2", "3", "4", "5"])
        self.circle_size_combo.setCurrentIndex(1)
        self.circle_size_combo.setFixedWidth(80)
        circle_layout.addWidget(self.circle_size_combo)
        circle_layout.addWidget(QLabel("1.5W移速选2即可(可根据自己移速调节)"))
        circle_layout.addWidget(QLabel("尽量1.35W-1.75W移速之间,别太快。"))
        circle_group.setLayout(circle_layout)
        layout.addWidget(circle_group)

        # 按住按键设置
        hold_group = QGroupBox("在正方形中设置战斗中按住不放的按键")
        hold_layout = QVBoxLayout()
        hold_layout.setSpacing(2)
        hold_layout.setContentsMargins(4, 4, 4, 4)

        hold_keys_layout = QGridLayout()
        hold_keys_layout.setHorizontalSpacing(0)
        hold_keys_layout.setContentsMargins(0, 0, 0, 0)
        self.hold_keys = {}
        for i in range(1, 6):
            label = QLabel(f"按键:")
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            hold_keys_layout.addWidget(label, 0, (i-1)*2)
            key_edit = QLineEdit()
            key_edit.setFixedSize(30, 30)
            hold_keys_layout.addWidget(key_edit, 0, (i-1)*2+1)
            self.hold_keys[i] = key_edit
        pu_gong_label = QLabel("普攻:")
        pu_gong_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hold_keys_layout.addWidget(pu_gong_label, 0, 10)
        self.normal_attack_key = QLineEdit()
        self.normal_attack_key.setFixedSize(30, 30)
        hold_keys_layout.addWidget(self.normal_attack_key, 0, 11)
        for i in range(6):
            hold_keys_layout.setColumnStretch(i*2, 1)
            hold_keys_layout.setColumnStretch(i*2+1, 0)
        hold_layout.addLayout(hold_keys_layout)

        hold_group.setLayout(hold_layout)
        layout.addWidget(hold_group)

        # 功能快捷键
        hotkey_group = QGroupBox("功能快捷键")
        hotkey_layout = QGridLayout()
        hotkey_layout.setHorizontalSpacing(20)
        hotkey_layout.setVerticalSpacing(6)

        hotkey_items = [
            ("上:", "w", "格挡键:", "r", "跟随键:", "f5", "秒杀技1:", "2"),
            ("左:", "a", "受身键:", "e", "拣物键:", "z", "秒杀技2:", "f"),
            ("下:", "s", "打断后摇键:", "", "组队键:", "j", "秒杀技3:", "q"),
            ("右:", "d", "切换目标:", "tab", "", "", "秒杀技4:", ""),
        ]
        for r, (l0, v0, l1, v1, l2, v2, l3, v3) in enumerate(hotkey_items):
            hotkey_layout.addWidget(QLabel(l0), r, 0, Qt.AlignRight)
            e0 = QLineEdit(v0); e0.setFixedSize(30, 30)
            hotkey_layout.addWidget(e0, r, 1)
            hotkey_layout.addWidget(QLabel(l1), r, 2, Qt.AlignRight)
            e1 = QLineEdit(v1); e1.setFixedSize(30, 30)
            hotkey_layout.addWidget(e1, r, 3)
            if l2:
                hotkey_layout.addWidget(QLabel(l2), r, 4, Qt.AlignRight)
                e2 = QLineEdit(v2); e2.setFixedSize(30, 30)
                hotkey_layout.addWidget(e2, r, 5)
            if l3:
                hotkey_layout.addWidget(QLabel(l3), r, 6, Qt.AlignRight)
                e3 = QLineEdit(v3); e3.setFixedSize(30, 30)
                hotkey_layout.addWidget(e3, r, 7)

        self.hotkey_up = hotkey_layout.itemAtPosition(0, 1).widget()
        self.hotkey_left = hotkey_layout.itemAtPosition(1, 1).widget()
        self.hotkey_down = hotkey_layout.itemAtPosition(2, 1).widget()
        self.hotkey_right = hotkey_layout.itemAtPosition(3, 1).widget()
        self.hotkey_block = hotkey_layout.itemAtPosition(0, 3).widget()
        self.hotkey_follow = hotkey_layout.itemAtPosition(0, 5).widget()
        self.hotkey_kill1 = hotkey_layout.itemAtPosition(0, 7).widget()
        self.hotkey_dodge = hotkey_layout.itemAtPosition(1, 3).widget()
        self.hotkey_pickup = hotkey_layout.itemAtPosition(1, 5).widget()
        self.hotkey_kill2 = hotkey_layout.itemAtPosition(1, 7).widget()
        self.hotkey_interrupt = hotkey_layout.itemAtPosition(2, 3).widget()
        self.hotkey_team = hotkey_layout.itemAtPosition(2, 5).widget()
        self.hotkey_kill3 = hotkey_layout.itemAtPosition(2, 7).widget()
        self.hotkey_target = hotkey_layout.itemAtPosition(3, 3).widget()
        self.hotkey_kill4 = hotkey_layout.itemAtPosition(3, 7).widget()

        hotkey_group.setLayout(hotkey_layout)
        layout.addWidget(hotkey_group)

        # 秒杀技4选项
        kill4_group = QGroupBox("秒杀技4选项")
        kill4_layout = QGridLayout()
        kill4_layout.setHorizontalSpacing(12)
        kill4_layout.setVerticalSpacing(4)
        kill4_items = [
            ("隐身:", "kill4_stealth"),
            ("九品:", "kill4_jiupin"),
            ("小招1:", "kill4_small1"),
            ("大招1:", "kill4_big1"),
            ("小招2:", "kill4_small2"),
            ("大招2:", "kill4_big2"),
            ("刷新技:", "kill4_refresh"),
            ("等待技:", "kill4_wait"),
        ]
        self.kill4_inputs = {}
        for idx, (label, attr) in enumerate(kill4_items):
            col = idx * 2
            kill4_layout.addWidget(QLabel(label), 0, col, Qt.AlignRight)
            edit = QLineEdit()
            edit.setFixedSize(30, 30)
            kill4_layout.addWidget(edit, 0, col + 1)
            self.kill4_inputs[attr] = edit
        for i in range(len(kill4_items)):
            kill4_layout.setColumnStretch(i * 2, 0)
            kill4_layout.setColumnStretch(i * 2 + 1, 0)
        kill4_group.setLayout(kill4_layout)
        layout.addWidget(kill4_group)

        auto_combat_group = QGroupBox("自动战斗")
        auto_combat_layout = QVBoxLayout()
        self.chk_auto_combat = QCheckBox("自动识别战斗状态（检测仇恨怪物并自动攻击至脱战）")
        auto_combat_layout.addWidget(self.chk_auto_combat)
        auto_combat_info = QLabel("开启后将自动检测角色是否进入战斗，对有仇恨的怪物持续攻击直到怪物死亡脱战")
        auto_combat_info.setStyleSheet("color: gray; font-size: 11px;")
        auto_combat_info.setWordWrap(True)
        auto_combat_layout.addWidget(auto_combat_info)
        auto_combat_group.setLayout(auto_combat_layout)
        layout.addWidget(auto_combat_group)

        layout.addStretch()
        self.tabs.addTab(tab, "技能按键设置")

    def create_dungeon_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)

        # 左侧设置
        left_layout = QVBoxLayout()

        # 副本选项
        options_group = QGroupBox("副本选项")
        opt_layout = QVBoxLayout()

        self.chk_dungeon = QCheckBox("刷副本")
        opt_layout.addWidget(self.chk_dungeon)

        spirit_layout = QHBoxLayout()
        spirit_layout.addWidget(QLabel("喂灵设置"))
        spirit_layout.addWidget(QLabel("看设置"))
        opt_layout.addLayout(spirit_layout)

        opt_layout.addWidget(QLabel("单个副本死亡3次以上会自动停止并弹窗提示。"))

        store_layout = QHBoxLayout()
        store_layout.addWidget(QLabel("蓝紫存仓:"))
        self.chk_store1 = QCheckBox("仓1")
        self.chk_store2 = QCheckBox("仓2")
        self.chk_store3 = QCheckBox("仓3")
        store_layout.addWidget(self.chk_store1)
        store_layout.addWidget(self.chk_store2)
        store_layout.addWidget(self.chk_store3)
        opt_layout.addLayout(store_layout)

        dungeon_type_layout = QHBoxLayout()
        self.chk_palace_exp = QCheckBox("行宫刷经验")
        self.chk_beast_exp = QCheckBox("古兽刷经验")
        dungeon_type_layout.addWidget(self.chk_palace_exp)
        dungeon_type_layout.addWidget(self.chk_beast_exp)
        opt_layout.addLayout(dungeon_type_layout)

        team_layout = QHBoxLayout()
        self.chk_team_leader = QCheckBox("团长")
        self.chk_team_member = QCheckBox("团员")
        team_layout.addWidget(self.chk_team_leader)
        team_layout.addWidget(self.chk_team_member)
        team_layout.addWidget(QLabel("团长团员说明"))
        opt_layout.addLayout(team_layout)

        chaos_layout = QHBoxLayout()
        self.chk_chaos_exp = QCheckBox("混沌入侵刷经验")
        chaos_layout.addWidget(self.chk_chaos_exp)
        opt_layout.addLayout(chaos_layout)

        tower_layout = QHBoxLayout()
        self.chk_tower_dungeon = QCheckBox("刷镇妖")
        tower_layout.addWidget(self.chk_tower_dungeon)
        self.chk_floor_1_12 = QCheckBox("1-12层")
        self.chk_floor_1_12.setChecked(True)
        self.chk_floor_13_24 = QCheckBox("13-24层")
        self.chk_floor_13_24.setChecked(True)
        self.chk_floor_25_36 = QCheckBox("25-36层")
        self.chk_floor_25_36.setChecked(True)
        tower_layout.addWidget(self.chk_floor_1_12)
        tower_layout.addWidget(self.chk_floor_13_24)
        tower_layout.addWidget(self.chk_floor_25_36)
        opt_layout.addLayout(tower_layout)

        coord_layout = QHBoxLayout()
        self.chk_coord_farm = QCheckBox("坐标打怪/采集")
        coord_layout.addWidget(self.chk_coord_farm)
        self.chk_circle_pickup = QCheckBox("野外打怪后绕圈捡物")
        coord_layout.addWidget(self.chk_circle_pickup)
        self.chk_farm_monster = QCheckBox("打怪")
        self.chk_farm_mine = QCheckBox("挖矿")
        self.chk_farm_herb = QCheckBox("挖草")
        coord_layout.addWidget(self.chk_farm_monster)
        coord_layout.addWidget(self.chk_farm_mine)
        coord_layout.addWidget(self.chk_farm_herb)
        opt_layout.addLayout(coord_layout)

        custom_coord_layout = QHBoxLayout()
        custom_coord_layout.addWidget(QLabel("自定义坐标:"))
        self.custom_coord = QLineEdit("527, 356")
        custom_coord_layout.addWidget(self.custom_coord)
        opt_layout.addLayout(custom_coord_layout)

        options_group.setLayout(opt_layout)
        left_layout.addWidget(options_group)

        # 镇妖设置
        tower_group = QGroupBox("镇妖设置")
        tower_layout = QVBoxLayout()
        tower_layout.addWidget(QLabel("镇妖真君:"))
        self.tower_boss_skills = {}
        for i in range(1, 5):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"技能{i}:"))
            skill_edit = QLineEdit()
            skill_edit.setFixedSize(30, 30)
            row.addWidget(skill_edit)
            tower_layout.addLayout(row)
            self.tower_boss_skills[i] = skill_edit
        self.chk_tower_small = QCheckBox("镇妖小号")
        tower_layout.addWidget(self.chk_tower_small)
        tower_group.setLayout(tower_layout)
        left_layout.addWidget(tower_group)

        left_layout.addStretch()

        layout.addLayout(left_layout)

        # 中间副本执行列表
        center_layout = QVBoxLayout()

        list_group = QGroupBox("副本执行列表:")
        list_layout = QVBoxLayout()

        self.dungeon_queue = QListWidget()
        list_layout.addWidget(self.dungeon_queue)

        add_layout = QHBoxLayout()
        add_layout.addWidget(QLabel("副本:"))
        self.dungeon_name_combo = QComboBox()
        self.dungeon_name_combo.addItems([
            "堕龙坑", "五行山顶", "高老庄", "流沙河底", "鹰愁涧", "金山寺",
            "断妄府", "上清观", "天坑树洞", "五庄观", "女国宫", "鸡鸣关",
            "血之东都", "天殁冢", "白骨洞外", "白骨洞一层", "白骨洞二层", "黑风寨",
            "黄风阵", "莲花洞", "宝林寺", "琵琶洞", "波月谷", "金兕崖",
            "通天河底", "火云禁地", "三仙道场", "东天马", "太乙试炼场", "黄泉路",
            "鬼门关", "枉死城", "孽镜台", "背阴山", "南天门", "银河战舰",
            "黑水迷城", "七绝岭", "小雷音寺", "封魔牢狱", "楞伽遗址", "圣天门口",
            "灵官殿", "化神池", "五行残界", "斩妖台", "三神宫", "二王庙",
            "兜率宫", "夜莺谷", "灾厄悬崖", "夜之黑风寨", "九曲盘桓洞", "小阎罗殿",
        ])
        self.dungeon_name_combo.setFixedWidth(120)
        add_layout.addWidget(self.dungeon_name_combo)

        add_layout.addWidget(QLabel("难度:"))
        self.dungeon_diff_combo = QComboBox()
        self.dungeon_diff_combo.addItems(["普通", "修炼", "挑战", "赏金", "狩猎", "极道"])
        self.dungeon_diff_combo.setFixedWidth(80)
        add_layout.addWidget(self.dungeon_diff_combo)

        add_layout.addWidget(QLabel("次数:"))
        self.dungeon_count = QLineEdit("10")
        self.dungeon_count.setFixedWidth(60)
        add_layout.addWidget(self.dungeon_count)

        list_layout.addLayout(add_layout)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("添加副本")
        add_btn.clicked.connect(self.add_dungeon_to_queue)
        btn_layout.addWidget(add_btn)

        insert_btn = QPushButton("插入副本")
        insert_btn.clicked.connect(self.insert_dungeon_to_queue)
        btn_layout.addWidget(insert_btn)

        clear_btn = QPushButton("清空副本")
        clear_btn.clicked.connect(self.clear_dungeon_queue)
        btn_layout.addWidget(clear_btn)

        del_btn = QPushButton("双击删除")
        del_btn.clicked.connect(self.delete_selected_dungeon)
        btn_layout.addWidget(del_btn)

        list_layout.addLayout(btn_layout)
        list_group.setLayout(list_layout)
        center_layout.addWidget(list_group)

        layout.addLayout(center_layout)

        # 右侧混沌模式设置
        right_layout = QVBoxLayout()

        chaos_group = QGroupBox("混沌模式设置说明!")
        chaos_layout = QVBoxLayout()

        chaos_layout.addWidget(QLabel("关底结算翻倍:"))
        flip_layout = QHBoxLayout()
        self.chk_flip1 = QCheckBox("第1个")
        self.chk_flip2 = QCheckBox("第2个")
        self.chk_flip3 = QCheckBox("第3个")
        self.chk_flip4 = QCheckBox("第4个")
        flip_layout.addWidget(self.chk_flip1)
        flip_layout.addWidget(self.chk_flip2)
        flip_layout.addWidget(self.chk_flip3)
        flip_layout.addWidget(self.chk_flip4)
        chaos_layout.addLayout(flip_layout)

        extra_layout = QHBoxLayout()
        self.chk_commerce = QCheckBox("选修商令")
        self.chk_chaos_half = QCheckBox("混沌半图")
        extra_layout.addWidget(self.chk_commerce)
        extra_layout.addWidget(self.chk_chaos_half)
        chaos_layout.addLayout(extra_layout)

        baby_layout = QHBoxLayout()
        self.chk_train_baby = QCheckBox("练宝宝")
        baby_layout.addWidget(self.chk_train_baby)
        baby_layout.addWidget(QLabel("练宝宝说明"))
        chaos_layout.addLayout(baby_layout)

        small_layout = QHBoxLayout()
        self.chk_small_mode = QCheckBox("小号")
        self.chk_small_mode.setChecked(True)
        small_layout.addWidget(self.chk_small_mode)
        small_layout.addWidget(QLabel("小号说明"))
        self.chk_no_boss = QCheckBox("队员不进boss房")
        small_layout.addWidget(self.chk_no_boss)
        chaos_layout.addLayout(small_layout)

        spider_layout = QHBoxLayout()
        self.chk_spider_hero = QCheckBox("全自动大蜘蛛英雄任务")
        spider_layout.addWidget(self.chk_spider_hero)
        spider_layout.addWidget(QLabel("→说明"))
        chaos_layout.addLayout(spider_layout)

        chaos_layout.addWidget(QCheckBox("队员不R点"))
        chaos_layout.addWidget(QCheckBox("对比背包仓库，相同物品存仓"))

        king_layout = QHBoxLayout()
        self.chk_four_kings = QCheckBox("四大天王刷经验")
        self.chk_four_kings.setChecked(True)
        king_layout.addWidget(self.chk_four_kings)
        king_layout.addWidget(QLabel("四大天王设置说明"))
        chaos_layout.addLayout(king_layout)

        king2_layout = QHBoxLayout()
        self.chk_four_kings_dual = QCheckBox("四大天王主副双修")
        self.chk_four_kings_dual.setChecked(True)
        king2_layout.addWidget(self.chk_four_kings_dual)
        chaos_layout.addLayout(king2_layout)

        chaos_group.setLayout(chaos_layout)
        right_layout.addWidget(chaos_group)
        right_layout.addStretch()

        layout.addLayout(right_layout)

        self.tabs.addTab(tab, "打怪采集刷本")

    def create_pvp_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        arena_group = QGroupBox("斗技场")
        arena_layout = QVBoxLayout()

        self.chk_smart_arena = QCheckBox("智能斗技")
        self.chk_smart_arena.setChecked(True)
        arena_layout.addWidget(self.chk_smart_arena)

        self.chk_smart_arena2 = QCheckBox("智能斗技二")
        self.chk_smart_arena2.setChecked(True)
        arena_layout.addWidget(self.chk_smart_arena2)

        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(8)
        self.radio_arena_solo = QRadioButton("单排")
        self.radio_arena_team = QRadioButton("组队")
        self.radio_arena_1v1 = QRadioButton("1V1")
        self.radio_arena_mode1 = QRadioButton("模式1")
        self.radio_arena_mode2 = QRadioButton("模式2")
        self.radio_arena_solo.setChecked(True)
        self.radio_arena_mode1.setChecked(True)
        mode_layout.addWidget(self.radio_arena_solo)
        mode_layout.addWidget(self.radio_arena_team)
        mode_layout.addWidget(self.radio_arena_1v1)
        mode_layout.addWidget(self.radio_arena_mode1)
        mode_layout.addWidget(self.radio_arena_mode2)
        mode_layout.addStretch()
        arena_layout.addLayout(mode_layout)

        arena_group.setLayout(arena_layout)
        layout.addWidget(arena_group)

        battlefield_group = QGroupBox("战场")
        bf_layout = QHBoxLayout()
        bf_layout.setSpacing(10)

        self.radio_bf_solo = QRadioButton("单排")
        self.radio_bf_team = QRadioButton("组队")
        self.radio_bf_solo.setChecked(True)
        bf_layout.addWidget(self.radio_bf_solo)
        bf_layout.addWidget(self.radio_bf_team)

        self.chk_capture_flag = QCheckBox("抢旗")
        self.chk_capture_flag.setChecked(True)
        bf_layout.addWidget(self.chk_capture_flag)

        bf_layout.addStretch()
        battlefield_group.setLayout(bf_layout)
        layout.addWidget(battlefield_group)

        guild_war_group = QGroupBox("宗战")
        gw_layout = QVBoxLayout()
        self.chk_guild_war = QCheckBox("智能宗战")
        gw_layout.addWidget(self.chk_guild_war)
        guild_war_group.setLayout(gw_layout)
        layout.addWidget(guild_war_group)

        score_group = QGroupBox("斗技积分")
        score_layout = QHBoxLayout()
        self.chk_stop_500 = QCheckBox("满500分停止")
        self.chk_stop_500.setChecked(True)
        score_layout.addWidget(self.chk_stop_500)
        self.chk_stop_silver = QCheckBox("白银停止")
        score_layout.addWidget(self.chk_stop_silver)
        self.chk_stop_gold = QCheckBox("黄金停止")
        score_layout.addWidget(self.chk_stop_gold)
        score_group.setLayout(score_layout)
        layout.addWidget(score_group)

        layout.addStretch()
        self.tabs.addTab(tab, "PvP")

    def create_equipment_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        refine_group = QGroupBox("精炼装备")
        refine_layout = QGridLayout()

        self.chk_refine = QCheckBox("启用精炼")
        self.chk_refine.setChecked(True)
        refine_layout.addWidget(self.chk_refine, 0, 0, 1, 2)

        refine_layout.addWidget(QLabel("精炼目标:"), 1, 0)
        self.refine_target = QComboBox()
        self.refine_target.addItems(["+1", "+2", "+3", "+4", "+5", "+6", "+7", "+8", "+9", "+10", "+11", "+12"])
        self.refine_target.setCurrentIndex(7)
        refine_layout.addWidget(self.refine_target, 1, 1)

        refine_layout.addWidget(QLabel("回退值:"), 2, 0)
        self.refine_retreat = QLineEdit("2")
        self.refine_retreat.setFixedWidth(60)
        refine_layout.addWidget(self.refine_retreat, 2, 1)

        refine_group.setLayout(refine_layout)
        layout.addWidget(refine_group)

        recast_group = QGroupBox("重铸装备")
        recast_layout = QGridLayout()

        recast_layout.addWidget(QLabel("目标属性1:"), 0, 0)
        self.recast_attr1 = QComboBox()
        self.recast_attr1.addItems(["攻击", "防御", "生命", "暴击", "命中", "闪避"])
        recast_layout.addWidget(self.recast_attr1, 0, 1)

        recast_layout.addWidget(QLabel("目标属性2:"), 1, 0)
        self.recast_attr2 = QComboBox()
        self.recast_attr2.addItems(["攻击", "防御", "生命", "暴击", "命中", "闪避"])
        self.recast_attr2.setCurrentIndex(2)
        recast_layout.addWidget(self.recast_attr2, 1, 1)

        recast_layout.addWidget(QLabel("目标属性3:"), 2, 0)
        self.recast_attr3 = QComboBox()
        self.recast_attr3.addItems(["攻击", "防御", "生命", "暴击", "命中", "闪避"])
        self.recast_attr3.setCurrentIndex(3)
        recast_layout.addWidget(self.recast_attr3, 2, 1)

        recast_layout.addWidget(QLabel("保留第几个技能:"), 3, 0)
        self.recast_skill_keep = QLineEdit("3")
        self.recast_skill_keep.setFixedWidth(60)
        recast_layout.addWidget(self.recast_skill_keep, 3, 1)

        recast_layout.addWidget(QLabel("出几条目标停止:"), 4, 0)
        self.recast_stop_count = QLineEdit("2")
        self.recast_stop_count.setFixedWidth(60)
        recast_layout.addWidget(self.recast_stop_count, 4, 1)

        recast_group.setLayout(recast_layout)
        layout.addWidget(recast_group)

        self.chk_batch_craft = QCheckBox("批量合成、重铸装备")
        layout.addWidget(self.chk_batch_craft)

        jade_group = QGroupBox("合玉晶石")
        jade_layout = QVBoxLayout()
        self.chk_jade = QCheckBox("合玉晶石")
        jade_layout.addWidget(self.chk_jade)
        jade_checks = QHBoxLayout()
        self.chk_jade2 = QCheckBox("二阶")
        self.chk_jade2.setChecked(True)
        self.chk_jade3 = QCheckBox("三阶")
        self.chk_jade3.setChecked(True)
        self.chk_jade4 = QCheckBox("四阶")
        self.chk_jade4.setChecked(True)
        self.chk_jade5 = QCheckBox("五阶")
        self.chk_jade5.setChecked(True)
        jade_checks.addWidget(self.chk_jade2)
        jade_checks.addWidget(self.chk_jade3)
        jade_checks.addWidget(self.chk_jade4)
        jade_checks.addWidget(self.chk_jade5)
        jade_layout.addLayout(jade_checks)
        jade_group.setLayout(jade_layout)
        layout.addWidget(jade_group)

        layout.addStretch()
        self.tabs.addTab(tab, "装备")

    def create_task_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        tasks_group = QGroupBox("日常任务")
        tasks_layout = QVBoxLayout()

        self.task_checks = {}
        tasks = [
            ("声望任务", True), ("三次斗技胜利", True), ("好友对话", True),
            ("末日任务", True), ("兽灵山谷", True), ("军备押送", True),
            ("抓残魄", True), ("挖宝", True), ("矿洞挖矿", True),
            ("月光宝盒", False), ("决战昆仑", False), ("商店购买", False),
            ("精炼装备", False), ("切换角色", False), ("血战到底", False),
            ("情人节任务", False), ("除尘任务", False), ("宝宝开洞", False),
            ("练宝宝", False), ("刷活跃", False), ("宗派建设", False),
            ("门派赛", False), ("妖王赛", False), ("购买偶人", False),
            ("请愿钻风", False), ("智能接英雄任务", False),
            ("刷行宫", False), ("刷古兽", False), ("刷行宫四大天王", False),
            ("十级号刷官银", False), ("混沌半图", False),
        ]

        grid = QGridLayout()
        for i, (name, default) in enumerate(tasks):
            row = i // 3
            col = i % 3
            chk = QCheckBox(name)
            chk.setChecked(default)
            grid.addWidget(chk, row, col)
            self.task_checks[name] = chk

        tasks_layout.addLayout(grid)
        tasks_group.setLayout(tasks_layout)
        layout.addWidget(tasks_group)

        layout.addStretch()
        self.tabs.addTab(tab, "任务")

    def create_misc_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)

        # 左侧任务选项
        left_layout = QVBoxLayout()

        baby_group = QGroupBox("宝宝开洞")
        baby_layout = QVBoxLayout()
        baby_layout.addWidget(QLabel("1、需要开洞的宝宝出战。"))
        baby_layout.addWidget(QLabel("2、背包准备好技能书和精元丹。"))
        baby_layout.addWidget(QLabel("3、长安NPC旁边启动。"))
        baby_layout.addWidget(QLabel("4、变速器倍数和变速精灵保持一致。"))

        baby_input_layout = QHBoxLayout()
        baby_input_layout.addWidget(QLabel("变数器倍数:"))
        self.baby_speed_mult = QLineEdit()
        self.baby_speed_mult.setFixedWidth(60)
        baby_input_layout.addWidget(self.baby_speed_mult)
        baby_layout.addLayout(baby_input_layout)

        lock_layout = QHBoxLayout()
        lock_layout.addWidget(QLabel("锁技能:"))
        self.lock_skills_misc = QLineEdit("例:1,4")
        lock_layout.addWidget(self.lock_skills_misc)
        baby_layout.addLayout(lock_layout)

        baby_group.setLayout(baby_layout)
        left_layout.addWidget(baby_group)

        jade_group = QGroupBox("合玉晶石、普通真言、幸运石")
        jade_layout = QVBoxLayout()
        jade_layout.addWidget(QLabel("1、合成界面启动"))
        jade_layout.addWidget(QLabel("2、全选轮流合完"))
        jade_layout.addWidget(QLabel("3、不要√自动设置"))

        jade_checks = QHBoxLayout()
        self.chk_jade2_misc = QCheckBox("合二阶")
        self.chk_jade2_misc.setChecked(True)
        self.chk_jade3_misc = QCheckBox("合三阶")
        self.chk_jade3_misc.setChecked(True)
        self.chk_jade4_misc = QCheckBox("合四阶")
        self.chk_jade4_misc.setChecked(True)
        self.chk_jade5_misc = QCheckBox("合五阶")
        self.chk_jade5_misc.setChecked(True)
        jade_checks.addWidget(self.chk_jade2_misc)
        jade_checks.addWidget(self.chk_jade3_misc)
        jade_checks.addWidget(self.chk_jade4_misc)
        jade_checks.addWidget(self.chk_jade5_misc)
        jade_layout.addLayout(jade_checks)

        jade_group.setLayout(jade_layout)
        left_layout.addWidget(jade_group)

        run_group = QGroupBox("跑环任务")
        run_layout = QVBoxLayout()
        self.chk_dust_run = QCheckBox("五一除尘跑环")
        self.chk_valentine_run = QCheckBox("情人节跑环")
        self.chk_pray_run = QCheckBox("请愿钻风")
        run_layout.addWidget(self.chk_dust_run)
        run_layout.addWidget(self.chk_valentine_run)
        run_layout.addWidget(self.chk_pray_run)
        run_group.setLayout(run_layout)
        left_layout.addWidget(run_group)

        poison_group = QGroupBox("端午艾草避毒")
        poison_layout = QHBoxLayout()
        self.chk_poison = QCheckBox("端午艾草避毒")
        poison_layout.addWidget(self.chk_poison)
        poison_layout.addWidget(QLabel("显示其他玩家名字不能打勾"))
        poison_group.setLayout(poison_layout)
        left_layout.addWidget(poison_group)

        left_layout.addStretch()
        layout.addLayout(left_layout)

        # 右侧
        right_layout = QVBoxLayout()

        guild_group = QGroupBox("宗派建设任务")
        guild_layout = QHBoxLayout()
        self.chk_guild_build = QCheckBox("宗派建设任务")
        guild_layout.addWidget(self.chk_guild_build)
        guild_layout.addWidget(QLabel("打怪任务暂时只有40-45的黑风山"))
        guild_group.setLayout(guild_layout)
        right_layout.addWidget(guild_group)

        silver_group = QGroupBox("新区10级号刷100W官银袋子")
        silver_layout = QVBoxLayout()
        silver_layout.addWidget(QLabel("此为单独付费功能"))
        silver_layout.addWidget(QLabel("收钱人不要是10级狐狸!角色界面启动"))
        silver_layout.addWidget(QLabel("15分钟一轮，一天96轮，能刷1920元"))

        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("收钱人名字:"))
        self.silver_receiver = QLineEdit()
        name_layout.addWidget(self.silver_receiver)
        silver_layout.addLayout(name_layout)

        self.chk_train_receiver = QCheckBox("先练收钱号")
        silver_layout.addWidget(self.chk_train_receiver)
        silver_layout.addWidget(QLabel("√这个，需要你创建好牛魔收钱号，然后进入游戏后启动，练完后会自动开始刷官银"))

        silver_group.setLayout(silver_layout)
        right_layout.addWidget(silver_group)

        one_click_group = QGroupBox("一键任务")
        one_click_layout = QVBoxLayout()

        count_layout = QHBoxLayout()
        count_layout.addWidget(QLabel("一键任务"))
        self.one_click_count = QLabel("71")
        count_layout.addWidget(self.one_click_count)
        count_layout.addWidget(QLabel("启动倒计时(分):"))
        self.countdown_input = QLineEdit()
        self.countdown_input.setFixedWidth(60)
        count_layout.addWidget(self.countdown_input)
        one_click_layout.addLayout(count_layout)

        list_layout = QHBoxLayout()

        available_group = QGroupBox("可选任务")
        available_layout = QVBoxLayout()
        self.available_tasks = QListWidget()
        available_tasks_list = [
            "三次斗技1V1胜利", "幻虚特产", "末日任务", "末日任务只采集",
            "挖宝任务", "秒2.8倍神之盘丝抢亲取经", "血战到底", "智能战场",
            "坐标打怪/采集", "混沌入侵刷经验", "古兽刷经验", "行宫刷经验",
            "混沌刻碑之眼", "除夕炸年兽", "元宵猜灯谜",
        ]
        for task in available_tasks_list:
            self.available_tasks.addItem(task)
        available_layout.addWidget(self.available_tasks)
        available_group.setLayout(available_layout)
        list_layout.addWidget(available_group)

        btn_layout = QVBoxLayout()
        add_task_btn = QPushButton("已选")
        add_task_btn.clicked.connect(self.add_selected_task)
        btn_layout.addWidget(add_task_btn)

        task_btn = QPushButton("任务")
        task_btn.clicked.connect(self._show_one_click_task_info)
        btn_layout.addWidget(task_btn)

        adjust_btn = QPushButton("调整")
        adjust_btn.clicked.connect(self._adjust_one_click_task)
        btn_layout.addWidget(adjust_btn)

        order_btn = QPushButton("顺序")
        order_btn.clicked.connect(self._sort_selected_tasks)
        btn_layout.addWidget(order_btn)

        down_btn = QPushButton("↓")
        down_btn.clicked.connect(self._move_selected_task_down)
        btn_layout.addWidget(down_btn)

        clear_task_btn = QPushButton("清空")
        clear_task_btn.clicked.connect(self.clear_selected_tasks)
        btn_layout.addWidget(clear_task_btn)

        list_layout.addLayout(btn_layout)

        selected_group = QGroupBox("已选任务")
        selected_layout = QVBoxLayout()
        self.selected_tasks = QListWidget()
        selected_layout.addWidget(self.selected_tasks)
        selected_group.setLayout(selected_layout)
        list_layout.addWidget(selected_group)

        one_click_layout.addLayout(list_layout)
        one_click_group.setLayout(one_click_layout)
        right_layout.addWidget(one_click_group)

        right_layout.addStretch()
        layout.addLayout(right_layout)

        self.tabs.addTab(tab, "灵兽节日日常")

    def create_auction_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        scan_group = QGroupBox("拍卖扫货")
        scan_layout = QGridLayout()

        self.chk_auction_scan = QCheckBox("启用拍卖扫货")
        scan_layout.addWidget(self.chk_auction_scan, 0, 0, 1, 2)

        scan_layout.addWidget(QLabel("扫货价格:"), 1, 0)
        self.auction_price = QLineEdit()
        scan_layout.addWidget(self.auction_price, 1, 1)

        scan_layout.addWidget(QLabel("扫货数量:"), 2, 0)
        self.auction_qty = QLineEdit()
        scan_layout.addWidget(self.auction_qty, 2, 1)

        scan_layout.addWidget(QLabel("单一扫货次数:"), 3, 0)
        self.auction_single_count = QLineEdit()
        scan_layout.addWidget(self.auction_single_count, 3, 1)

        scan_layout.addWidget(QLabel("扫货总次数:"), 4, 0)
        self.auction_total_count = QLineEdit()
        scan_layout.addWidget(self.auction_total_count, 4, 1)

        scan_layout.addWidget(QLabel("搜索间隔:"), 5, 0)
        self.auction_interval = QLineEdit()
        scan_layout.addWidget(self.auction_interval, 5, 1)

        scan_group.setLayout(scan_layout)
        layout.addWidget(scan_group)

        slots_group = QGroupBox("购买格位")
        slots_layout = QGridLayout()
        self.auction_slots = {}
        for i in range(1, 9):
            row = (i - 1) // 4
            col = (i - 1) % 4 * 2
            chk = QCheckBox(f"第{i}格")
            slots_layout.addWidget(chk, row, col)
            self.auction_slots[i] = chk
        slots_group.setLayout(slots_layout)
        layout.addWidget(slots_group)

        self.chk_auction_sell = QCheckBox("拍卖卖出")
        layout.addWidget(self.chk_auction_sell)

        layout.addStretch()
        self.tabs.addTab(tab, "拍卖")

    def create_chat_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        for ch_num in range(1, 4):
            group = QGroupBox(f"频道{ch_num}")
            g_layout = QVBoxLayout()

            ch_layout = QHBoxLayout()
            ch_layout.addWidget(QLabel("频道:"))
            combo = QComboBox()
            combo.addItems(["世界", "队伍", "宗派", "私聊", "系统"])
            ch_layout.addWidget(combo)
            g_layout.addLayout(ch_layout)

            delay_layout = QHBoxLayout()
            delay_layout.addWidget(QLabel("延时(秒):"))
            delay_edit = QLineEdit("90" if ch_num == 1 else "")
            delay_edit.setFixedWidth(60)
            delay_layout.addWidget(delay_edit)
            g_layout.addLayout(delay_layout)

            g_layout.addWidget(QLabel("喊话内容(多行用|分隔):"))
            content_edit = QTextEdit()
            content_edit.setMaximumHeight(80)
            if ch_num == 1:
                content_edit.setPlainText("出小狼智能助手：1、刷1-80副本所有模式，镇妖，100活跃...")
            g_layout.addWidget(content_edit)

            chk = QCheckBox(f"启用频道{ch_num}自动喊话")
            chk.setChecked(ch_num <= 2)
            g_layout.addWidget(chk)

            group.setLayout(g_layout)
            layout.addWidget(group)

        layout.addStretch()
        self.tabs.addTab(tab, "喊话")

    def create_settings_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        game_window_group = QGroupBox("游戏窗口设置")
        game_window_layout = QVBoxLayout()

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("游戏窗口标题:"))
        self.game_title_input = QLineEdit("斗战神")
        self.game_title_input.setFixedWidth(200)
        title_row.addWidget(self.game_title_input)
        self.detect_window_btn = QPushButton("识别窗口")
        self.detect_window_btn.setFixedWidth(80)
        self.detect_window_btn.clicked.connect(self.auto_detect_game_window)
        title_row.addWidget(self.detect_window_btn)
        title_row.addStretch()
        game_window_layout.addLayout(title_row)

        from PyQt5.QtWidgets import QShortcut, QComboBox
        from PyQt5.QtGui import QKeySequence
        shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        shortcut.activated.connect(self.auto_detect_game_window)

        # Multi-window selection row
        window_list_row = QHBoxLayout()
        window_list_row.addWidget(QLabel("游戏窗口列表:"))
        self.window_list_combo = QComboBox()
        self.window_list_combo.setMinimumWidth(300)
        window_list_row.addWidget(self.window_list_combo)
        self.refresh_window_btn = QPushButton("刷新列表")
        self.refresh_window_btn.setFixedWidth(80)
        self.refresh_window_btn.clicked.connect(self.refresh_window_list)
        window_list_row.addWidget(self.refresh_window_btn)
        window_list_row.addStretch()
        game_window_layout.addLayout(window_list_row)

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("窗口状态:"))
        self.window_status_label = QLabel("未检测")
        self.window_status_label.setStyleSheet("color: gray;")
        status_row.addWidget(self.window_status_label)
        status_row.addStretch()
        game_window_layout.addLayout(status_row)

        resolution_row = QHBoxLayout()
        resolution_row.addWidget(QLabel("固定分辨率:"))
        self.fixed_width_input = QLineEdit("1600")
        self.fixed_width_input.setFixedWidth(60)
        resolution_row.addWidget(self.fixed_width_input)
        resolution_row.addWidget(QLabel("x"))
        self.fixed_height_input = QLineEdit("900")
        self.fixed_height_input.setFixedWidth(60)
        resolution_row.addWidget(self.fixed_height_input)
        self.use_fixed_resolution_chk = QCheckBox("启用固定分辨率")
        self.use_fixed_resolution_chk.setChecked(True)
        resolution_row.addWidget(self.use_fixed_resolution_chk)
        resolution_row.addStretch()
        game_window_layout.addLayout(resolution_row)

        game_window_group.setLayout(game_window_layout)
        layout.addWidget(game_window_group)

        spirit_group = QGroupBox("喂灵设置 (主身)")
        spirit_layout = QGridLayout()
        spirit_layout.setHorizontalSpacing(5)
        spirit_layout.setVerticalSpacing(2)
        spirit_layout.setContentsMargins(4, 4, 4, 4)
        self.spirit_checks = {}
        equipment_slots = ["帽子", "肩膀", "胸甲", "腰带", "裤子", "护腕", "手套", "鞋子", "武器"]
        for i, slot in enumerate(equipment_slots):
            chk = QCheckBox(slot)
            chk.setChecked(True)
            spirit_layout.addWidget(chk, i // 5, i % 5)
            self.spirit_checks[f"主身_{slot}"] = chk
        spirit_group.setLayout(spirit_layout)
        layout.addWidget(spirit_group)

        spirit2_group = QGroupBox("喂灵设置 (元神)")
        spirit2_layout = QGridLayout()
        spirit2_layout.setHorizontalSpacing(5)
        spirit2_layout.setVerticalSpacing(2)
        spirit2_layout.setContentsMargins(4, 4, 4, 4)
        for i, slot in enumerate(equipment_slots):
            chk = QCheckBox(slot)
            spirit2_layout.addWidget(chk, i // 5, i % 5)
            self.spirit_checks[f"元神_{slot}"] = chk
        spirit2_group.setLayout(spirit2_layout)
        layout.addWidget(spirit2_group)

        spirit3_group = QGroupBox("喂灵设置 (材料)")
        spirit3_layout = QGridLayout()
        spirit3_layout.setHorizontalSpacing(5)
        spirit3_layout.setVerticalSpacing(2)
        spirit3_layout.setContentsMargins(4, 4, 4, 4)
        for i, slot in enumerate(equipment_slots):
            chk = QCheckBox(slot)
            spirit3_layout.addWidget(chk, i // 5, i % 5)
            self.spirit_checks[f"材料_{slot}"] = chk
        spirit3_group.setLayout(spirit3_layout)
        layout.addWidget(spirit3_group)

        misc_group = QGroupBox("其他设置")
        misc_layout = QVBoxLayout()
        misc_layout.setSpacing(4)
        misc_layout.setContentsMargins(4, 4, 4, 4)

        chk_row = QHBoxLayout()
        chk_row.setSpacing(10)
        self.chk_auto_settings = QCheckBox("自动设置")
        self.chk_auto_settings.setChecked(True)
        chk_row.addWidget(self.chk_auto_settings)

        self.chk_auto_shutdown = QCheckBox("完成后自动关机")
        chk_row.addWidget(self.chk_auto_shutdown)

        self.chk_small_no_boss = QCheckBox("小号不去打BOSS")
        self.chk_small_no_boss.setChecked(True)
        chk_row.addWidget(self.chk_small_no_boss)

        self.chk_compare_store = QCheckBox("对比存仓")
        self.chk_compare_store.setChecked(True)
        chk_row.addWidget(self.chk_compare_store)

        self.chk_stop_no_star = QCheckBox("无锁定星停止")
        self.chk_stop_no_star.setChecked(True)
        chk_row.addWidget(self.chk_stop_no_star)
        chk_row.addStretch()
        misc_layout.addLayout(chk_row)

        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(10)
        grid_layout.setVerticalSpacing(4)

        grid_layout.addWidget(QLabel("加速倍数:"), 0, 0)
        self.speed_mult = QLineEdit("8")
        self.speed_mult.setFixedWidth(60)
        grid_layout.addWidget(self.speed_mult, 0, 1)

        grid_layout.addWidget(QLabel("目标数值:"), 0, 2)
        self.target_value = QLineEdit("15")
        self.target_value.setFixedWidth(60)
        grid_layout.addWidget(self.target_value, 0, 3)

        grid_layout.addWidget(QLabel("锁技能(例:1,4):"), 1, 0)
        self.lock_skills = QLineEdit("例:1,4")
        grid_layout.addWidget(self.lock_skills, 1, 1)

        misc_layout.addLayout(grid_layout)

        misc_group.setLayout(misc_layout)
        layout.addWidget(misc_group)

        config_group = QGroupBox("配置管理")
        config_layout = QHBoxLayout()
        self.config_path = QLineEdit()
        self.config_path.setReadOnly(True)
        config_layout.addWidget(self.config_path)
        load_btn = QPushButton("加载配置")
        load_btn.clicked.connect(self.load_config_file)
        config_layout.addWidget(load_btn)
        save_btn = QPushButton("保存配置")
        save_btn.clicked.connect(self.save_config_file)
        config_layout.addWidget(save_btn)
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        update_group = QGroupBox("软件更新")
        update_layout = QVBoxLayout()

        version_row = QHBoxLayout()
        version_row.addWidget(QLabel(f"当前版本: {APP_VERSION}"))
        version_row.addStretch()
        self.update_status = QLabel("未检查")
        version_row.addWidget(self.update_status)
        update_layout.addLayout(version_row)

        btn_row = QHBoxLayout()
        self.check_update_btn = QPushButton("检查更新")
        self.check_update_btn.clicked.connect(self.check_update)
        btn_row.addWidget(self.check_update_btn)
        btn_row.addStretch()
        update_layout.addLayout(btn_row)

        update_group.setLayout(update_layout)
        layout.addWidget(update_group)

        layout.addStretch()
        self.tabs.addTab(tab, "设置")

    def add_dungeon_to_queue(self):
        name = self.dungeon_name_combo.currentText()
        diff = self.dungeon_diff_combo.currentText()
        count = self.dungeon_count.text()
        self.dungeon_queue.addItem(f"{name}→{diff}→{count}")

    def insert_dungeon_to_queue(self):
        name = self.dungeon_name_combo.currentText()
        diff = self.dungeon_diff_combo.currentText()
        count = self.dungeon_count.text()
        current_row = self.dungeon_queue.currentRow()
        if current_row >= 0:
            self.dungeon_queue.insertItem(current_row, f"{name}→{diff}→{count}")
        else:
            self.dungeon_queue.addItem(f"{name}→{diff}→{count}")

    def clear_dungeon_queue(self):
        self.dungeon_queue.clear()

    def delete_selected_dungeon(self):
        current_row = self.dungeon_queue.currentRow()
        if current_row >= 0:
            self.dungeon_queue.takeItem(current_row)

    def add_selected_task(self):
        items = self.available_tasks.selectedItems()
        for item in items:
            self.selected_tasks.addItem(item.text())

    def clear_selected_tasks(self):
        self.selected_tasks.clear()

    def _move_selected_task_down(self):
        current_row = self.selected_tasks.currentRow()
        if current_row < 0 or current_row >= self.selected_tasks.count() - 1:
            return
        item = self.selected_tasks.takeItem(current_row)
        self.selected_tasks.insertItem(current_row + 1, item)
        self.selected_tasks.setCurrentRow(current_row + 1)

    def _sort_selected_tasks(self):
        self.selected_tasks.sortItems()

    def _show_one_click_task_info(self):
        count = self.selected_tasks.count()
        if count == 0:
            QMessageBox.information(self, "一键任务", "请先从左侧添加任务到已选列表")
            return
        tasks = [self.selected_tasks.item(i).text() for i in range(count)]
        QMessageBox.information(self, "一键任务", f"共 {count} 个任务:\n" + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(tasks)))

    def _adjust_one_click_task(self):
        current_row = self.selected_tasks.currentRow()
        if current_row < 0:
            return
        item = self.selected_tasks.currentItem()
        from PyQt5.QtWidgets import QInputDialog
        new_text, ok = QInputDialog.getText(self, "调整任务", "修改任务名称:", text=item.text())
        if ok and new_text:
            item.setText(new_text)

    def _build_task_params(self):
        """从GUI状态构建所有已启用任务的参数列表"""
        self._collect_gui_to_config()
        c = self.config
        task_chain = []

        self.append_log(f"[调试] 刷副本选项: {c.get_bool('刷副本选项')}")

        combat_keys = []
        for i in range(1, 6):
            if i in self.click_keys:
                key = self.click_keys[i].text().strip()
                if key:
                    delay = 0
                    if i in self.click_delays:
                        try:
                            delay = int(self.click_delays[i].text())
                        except ValueError:
                            delay = 0
                    combat_keys.append({"key": key, "delay": delay})

        hold_keys = []
        for i in range(1, 6):
            if i in self.hold_keys:
                key = self.hold_keys[i].text().strip()
                if key:
                    hold_keys.append(key)

        common = {
            "combat_keys": combat_keys,
            "hold_keys": hold_keys,
            "normal_attack": self.normal_attack_key.text().strip(),
            "pickup_key": self.hotkey_pickup.text().strip() or "z",
            "target_key": self.hotkey_target.text().strip() or "tab",
            "follow_key": self.hotkey_follow.text().strip() or "f5",
            "team_key": self.hotkey_team.text().strip() or "j",
            "hp_key": c.get("HP药品键.Text", "1"),
            "hp_threshold": c.get_float("HP阈值.Text", 0.3),
        }

        if c.get_bool("自动战斗选项"):
            params = dict(common)
            params.update({
                "use_vision": True,
                "idle_check_interval": 0.5,
            })
            task_chain.append(("auto_combat", params))

        if c.get_bool("刷副本选项"):
            dungeon_list = []
            for i in range(self.dungeon_queue.count()):
                text = self.dungeon_queue.item(i).text()
                parts = text.split("→")
                if len(parts) >= 3:
                    dungeon_list.append({
                        "name": parts[0], "difficulty": parts[1], "count": int(parts[2])
                    })
            if dungeon_list:
                params = dict(common)
                params.update({
                    "dungeon_list": dungeon_list,
                    "max_time_min": c.get_int("刷本设定时间.text", 9999),
                    "use_vision": False,
                })
                task_chain.append(("dungeon", params))

        if c.get_bool("坐标打怪采集选项"):
            coords = []
            coord_text = c.get("自定义坐标.Text", "")
            for pair in coord_text.split("|"):
                pair = pair.strip()
                if "," in pair:
                    parts = pair.split(",")
                    try:
                        coords.append((int(parts[0].strip()), int(parts[1].strip())))
                    except (ValueError, IndexError):
                        pass
            params = dict(common)
            sub_modes = []
            if c.get_bool("打怪选项"):
                sub_modes.append("monster")
            if c.get_bool("挖矿选项"):
                sub_modes.append("mine")
            if c.get_bool("挖草选项"):
                sub_modes.append("herb")
            params.update({
                "coordinates": coords,
                "mode": "coord_farm",
                "sub_modes": sub_modes,
                "circle_pickup": c.get_bool("绕圈捡物选项"),
                "use_vision": False,
            })
            task_chain.append(("collection", params))

        if c.get_bool("行宫刷经验选项") or c.get_bool("古兽刷经验选项") or c.get_bool("混沌入侵刷经验选项"):
            params = dict(common)
            exp_dungeons = []
            if c.get_bool("行宫刷经验选项"):
                exp_dungeons.append("行宫")
            if c.get_bool("古兽刷经验选项"):
                exp_dungeons.append("古兽")
            if c.get_bool("混沌入侵刷经验选项"):
                exp_dungeons.append("混沌入侵")
            params.update({
                "dungeon_list": [{"name": d, "difficulty": "普通", "count": 99} for d in exp_dungeons],
                "max_time_min": 9999,
                "use_vision": False,
            })
            task_chain.append(("dungeon", params))

        if c.get_bool("刷镇妖选项"):
            floors = []
            if c.get_bool("镇妖1至12层"):
                floors.append("1-12")
            if c.get_bool("镇妖13至24层"):
                floors.append("13-24")
            if c.get_bool("镇妖25至36层"):
                floors.append("25-36")
            boss_skills = []
            for i in range(1, 5):
                if i in self.tower_boss_skills:
                    sk = self.tower_boss_skills[i].text().strip()
                    if sk:
                        boss_skills.append(sk)
            params = dict(common)
            params.update({
                "dungeon_list": [{"name": "镇妖塔", "difficulty": "普通", "count": 99}],
                "floors": floors,
                "boss_skills": boss_skills,
                "is_small": c.get_bool("镇妖小号选项"),
                "max_time_min": 9999,
                "use_vision": False,
            })
            task_chain.append(("dungeon", params))

        if c.get_bool("智能斗技选项") or c.get_bool("智能斗技选项二"):
            pvp_mode = "solo"
            if c.get_bool("斗技组队"):
                pvp_mode = "team"
            arena_mode = "mode1"
            if c.get_bool("斗技模式2"):
                arena_mode = "mode2"
            params = dict(common)
            params.update({
                "pvp_mode": pvp_mode,
                "arena_mode": arena_mode,
                "is_1v1": c.get_bool("斗技1V1"),
                "use_vision": False,
            })
            task_chain.append(("pvp_arena", params))

        if c.get_bool("精炼选项"):
            params = dict(common)
            params.update({
                "action": "refine",
                "target": self.refine_target.currentText(),
                "retreat": c.get("精炼加几回退选项.Text", "2"),
                "use_vision": False,
            })
            task_chain.append(("equipment", params))

        if c.get_bool("批量合成、重铸装备选项"):
            params = dict(common)
            params.update({
                "action": "recast",
                "attr1": self.recast_attr1.currentText(),
                "attr2": self.recast_attr2.currentText(),
                "attr3": self.recast_attr3.currentText(),
                "skill_keep": c.get("重铸选第几个技能保留.Text", "3"),
                "stop_count": c.get("重铸出几条目标属性停止.Text", "2"),
                "use_vision": False,
            })
            task_chain.append(("equipment", params))

        if c.get_bool("合玉晶石选项"):
            jades = []
            if c.get_bool("合二阶选项"):
                jades.append(2)
            if c.get_bool("合三阶选项"):
                jades.append(3)
            if c.get_bool("合四阶选项"):
                jades.append(4)
            if c.get_bool("合五阶选项"):
                jades.append(5)
            params = dict(common)
            params.update({"action": "jade_combine", "jades": jades, "use_vision": False})
            task_chain.append(("equipment", params))

        enabled_tasks = []
        for name, chk in self.task_checks.items():
            if chk.isChecked():
                enabled_tasks.append(name)
        if enabled_tasks:
            params = dict(common)
            params.update({"task_list": enabled_tasks, "use_vision": False})
            task_chain.append(("tasks", params))

        if c.get_bool("拍卖扫货选项"):
            params = dict(common)
            params.update({
                "price": c.get("拍卖扫货价格.Text"),
                "qty": c.get("拍卖扫货数量.Text"),
                "single_count": c.get("拍卖单一扫货次数.Text"),
                "total_count": c.get("拍卖扫货总次数.Text"),
                "interval": c.get_int("拍卖搜索间隔.Text", 5),
                "use_vision": False,
            })
            task_chain.append(("auction", params))

        one_click_count = self.selected_tasks.count()
        if one_click_count > 0:
            one_click_names = []
            for i in range(one_click_count):
                one_click_names.append(self.selected_tasks.item(i).text())
            if one_click_names:
                params = dict(common)
                params.update({"one_click_tasks": one_click_names, "use_vision": False})
                task_chain.append(("one_click", params))

        return task_chain

    def toggle_start(self):
        if self.worker is None or not self.worker.isRunning():
            self.append_log("[调试] 开始启动流程...")
            self._collect_gui_to_config()

            game_title = self.game_title_input.text().strip() if hasattr(self, 'game_title_input') else "斗战神"
            if not game_title:
                game_title = "斗战神"

            self.append_log(f"[调试] 游戏窗口标题: {game_title}")
            if self.engine.set_game_window(game_title):
                self.append_log(f"[启动] 已定位游戏窗口: {game_title}")
                import pygetwindow as gw
                wins = gw.getWindowsWithTitle(game_title)
                if wins:
                    w = wins[0]
                    self.window_status_label.setText(
                        f"已绑定: {w.title} ({w.width}x{w.height})"
                    )
                    self.window_status_label.setStyleSheet("color: green;")
            else:
                self.append_log(f"[警告] 未找到游戏窗口 '{game_title}'，将使用全屏模式")
                self.window_status_label.setText("未找到游戏窗口")
                self.window_status_label.setStyleSheet("color: red;")

            task_chain = self._build_task_params()
            self.append_log(f"[调试] 构建的任务链: {len(task_chain)} 个任务")

            one_click_count = self.selected_tasks.count()
            if one_click_count > 0:
                one_click_tasks = []
                for i in range(one_click_count):
                    one_click_tasks.append(self.selected_tasks.item(i).text())
                self.log_signal.emit(f"[一键任务] 已选 {len(one_click_tasks)} 个: {', '.join(one_click_tasks)}")

            if not task_chain:
                self.log_signal.emit("[启动] 未选择任何任务，请在各标签页勾选要执行的功能")
                self.append_log("[调试] 任务链为空，请检查GUI选项是否勾选")
                return

            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.start_btn.setText("运行中...")
            self.status_label.setText("正在执行任务...")

            task_names = [t[0] for t in task_chain]
            self.log_signal.emit(f"[启动] 任务链: {', '.join(task_names)}")

            if len(task_chain) == 1:
                name, params = task_chain[0]
                self.worker = WorkerThread(self.engine, name, params)
            else:
                self.worker = TaskChainWorker(self.engine, task_chain)

            self.worker.log_signal.connect(self.append_log)
            self.worker.status_signal.connect(self._on_status_update)
            self.worker.finished_signal.connect(self._on_worker_finished)
            self.worker.start()

            game_hwnd = user32.FindWindowW(None, game_title)
            self.overlay.start_overlay(game_hwnd if game_hwnd else None)
        else:
            self.stop_all()

    def _on_worker_finished(self, success):
        self.overlay.stop_overlay()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.start_btn.setText("启动 (F10)")
        if success:
            self.status_label.setText("已完成")
            self.log_signal.emit("[完成] 所有任务执行完毕")
        else:
            self.status_label.setText("执行出错")
        if self.config.get_bool("自动关机选项"):
            self.log_signal.emit("[关机] 任务完成，准备关机...")
            os.system("shutdown -s -t 60")

    def _on_status_update(self, text):
        self.status_label.setText(text)
        self.overlay.set_status_text(text)

    def stop_all(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        self.worker = None
        self.overlay.stop_overlay()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.start_btn.setText("启动 (F10)")
        self.stop_btn.setText("停止 (F11)")
        self.status_label.setText("已停止")
        self.log_signal.emit("[停止] 所有任务已停止")

    def _register_hotkeys(self):
        hwnd = int(self.winId())
        user32.RegisterHotKey(hwnd, HOTKEY_START, MOD_NONE, VK_F10)
        user32.RegisterHotKey(hwnd, HOTKEY_STOP, MOD_NONE, VK_F11)

    def _unregister_hotkeys(self):
        hwnd = int(self.winId())
        user32.UnregisterHotKey(hwnd, HOTKEY_START)
        user32.UnregisterHotKey(hwnd, HOTKEY_STOP)

    def nativeEvent(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(message.__int__())
            if msg.message == WM_HOTKEY:
                if msg.wParam == HOTKEY_START:
                    self.toggle_start()
                    return True, 0
                elif msg.wParam == HOTKEY_STOP:
                    self.stop_all()
                    return True, 0
        return super().nativeEvent(eventType, message)

    def closeEvent(self, event):
        self.stop_all()
        self._unregister_hotkeys()
        self._collect_gui_to_config()
        self.config.save(self.config.get_config_path())
        event.accept()

    def append_log(self, text):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {text}")

    def load_config(self):
        config_path = self.config.get_config_path()
        self.config_path.setText(config_path)
        if os.path.exists(config_path):
            self.config.load(config_path)
            self.log_signal.emit(f"已加载配置: {config_path}")

    def refresh_window_list(self):
        """刷新游戏窗口列表"""
        self.window_list_combo.clear()
        titles = ["斗战神", "DZS", "dzs", "fps:"]
        game_title = self.game_title_input.text().strip()
        if game_title and game_title not in titles:
            titles.insert(0, game_title)

        import pygetwindow as gw
        import os
        import ctypes
        my_pid = os.getpid()
        found_windows = []

        for title in titles:
            windows = gw.getWindowsWithTitle(title)
            for win in windows:
                if win.title == self.windowTitle():
                    continue
                try:
                    hwnd = win._hWnd
                    pid_val = ctypes.c_ulong()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_val))
                    if pid_val.value == my_pid:
                        continue
                except Exception:
                    pass
                if win.title not in found_windows:
                    found_windows.append(win.title)
                    display_text = f"{win.title} ({win.width}x{win.height})"
                    self.window_list_combo.addItem(display_text, win.title)

        if found_windows:
            self.log_signal.emit(f"[窗口] 发现 {len(found_windows)} 个游戏窗口")
        else:
            self.log_signal.emit("[窗口] 未发现游戏窗口")

    def auto_detect_game_window(self):
        # If a specific window is selected in the combo box, bind to it
        if self.window_list_combo.count() > 0:
            idx = self.window_list_combo.currentIndex()
            if idx >= 0:
                selected_title = self.window_list_combo.itemData(idx)
                if selected_title:
                    return self._bind_to_window(selected_title)

        # Fall back to auto-detect first matching window
        titles = ["斗战神", "DZS", "dzs", "fps:"]
        game_title = self.game_title_input.text().strip()
        if game_title and game_title not in titles:
            titles.insert(0, game_title)

        import pygetwindow as gw
        import os
        import ctypes
        my_pid = os.getpid()
        for title in titles:
            windows = gw.getWindowsWithTitle(title)
            for win in windows:
                if win.title == self.windowTitle():
                    continue
                try:
                    hwnd = win._hWnd
                    pid_val = ctypes.c_ulong()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_val))
                    if pid_val.value == my_pid:
                        continue
                except Exception:
                    pass
                return self._bind_to_window(title, win)

        self.window_status_label.setText("未找到游戏窗口")
        self.window_status_label.setStyleSheet("color: red;")
        self.log_signal.emit("[窗口] 未找到游戏窗口，请确认游戏已启动")
        return False

    def _bind_to_window(self, title, win=None):
        """绑定到指定窗口"""
        import pygetwindow as gw
        import ctypes

        # Find the window if not provided
        if win is None:
            windows = gw.getWindowsWithTitle(title)
            for w in windows:
                if w.title == title:
                    win = w
                    break

        if win is None:
            self.window_status_label.setText("未找到指定窗口")
            self.window_status_label.setStyleSheet("color: red;")
            return False

        # Get fixed resolution if enabled
        fixed_width = None
        fixed_height = None
        if self.use_fixed_resolution_chk.isChecked():
            try:
                fixed_width = int(self.fixed_width_input.text())
                fixed_height = int(self.fixed_height_input.text())
            except ValueError:
                pass

        if self.engine.set_game_window(title, fixed_width, fixed_height):
            w = fixed_width if fixed_width else win.width
            h = fixed_height if fixed_height else win.height
            self.window_status_label.setText(
                f"已绑定: {win.title} ({w}x{h})"
            )
            self.window_status_label.setStyleSheet("color: green;")
            self.game_title_input.setText(title)
            self.log_signal.emit(f"[窗口] 已自动识别并绑定: {win.title} ({w}x{h})")
            return True

        self.window_status_label.setText("绑定失败")
        self.window_status_label.setStyleSheet("color: red;")
        return False

    def load_config_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "加载配置", "", "INI Files (*.ini);;All Files (*)")
        if path:
            try:
                self.config.load(path)
                self.config_path.setText(path)
                self._apply_config_to_gui()
                self.log_signal.emit(f"已加载配置: {path}")
            except Exception as e:
                QMessageBox.critical(self, "加载配置失败", f"加载配置时出错:\n{e}")

    def save_config_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存配置", "", "INI Files (*.ini);;All Files (*)")
        if path:
            try:
                self._collect_gui_to_config()
                self.config.save(path)
                self.config_path.setText(path)
                self.log_signal.emit(f"已保存配置: {path}")
            except Exception as e:
                QMessageBox.critical(self, "保存配置失败", f"保存配置时出错:\n{e}")

    def save_config_auto(self):
        try:
            self._collect_gui_to_config()
            config_path = self.config.get_config_path()
            self.config.save(config_path)
            self.append_log(f"[保存] 配置已保存到: {config_path}")
            self.statusBar().showMessage("配置已保存", 3000)
        except Exception as e:
            QMessageBox.critical(self, "保存配置失败", f"保存配置时出错:\n{e}")
            self.append_log(f"[保存] 配置保存失败: {e}")

    def _apply_config_to_gui(self):
        c = self.config

        # 点击按键
        for i in range(1, 6):
            if i in self.click_keys:
                self.click_keys[i].setText(c.get(f"点击按键{i}.Text"))
                self.click_delays[i].setText(c.get(f"点击按键延迟{i}.Text", "0"))

        # 绕圈大小
        self.circle_size_combo.setCurrentIndex(c.get_int("绕圈大小.ListIndex", 1))

        # 按住按键
        for i in range(1, 6):
            if i in self.hold_keys:
                self.hold_keys[i].setText(c.get(f"按住按键{i}.Text"))
        self.normal_attack_key.setText(c.get("普攻选项"))

        # 功能快捷键
        self.hotkey_up.setText(c.get("上移键.Text", "w"))
        self.hotkey_left.setText(c.get("左移键.Text", "a"))
        self.hotkey_down.setText(c.get("下移键.Text", "s"))
        self.hotkey_right.setText(c.get("右移键.Text", "d"))
        self.hotkey_block.setText(c.get("格挡键.Text", "r"))
        self.hotkey_follow.setText(c.get("跟随键.Text", "f5"))
        self.hotkey_kill1.setText(c.get("秒杀技1.Text", "2"))
        self.hotkey_dodge.setText(c.get("受身键.Text", "e"))
        self.hotkey_pickup.setText(c.get("拣物键.Text", "z"))
        self.hotkey_kill2.setText(c.get("秒杀技2.Text", "f"))
        self.hotkey_interrupt.setText(c.get("打断后摇键.Text"))
        self.hotkey_team.setText(c.get("组队键.Text", "j"))
        self.hotkey_kill3.setText(c.get("秒杀技3.Text", "q"))
        self.hotkey_target.setText(c.get("切换目标.Text", "tab"))
        self.hotkey_kill4.setText(c.get("秒杀技4.Text"))

        # 秒杀技4选项
        self.kill4_inputs["kill4_stealth"].setText(c.get("秒杀技4隐身"))
        self.kill4_inputs["kill4_jiupin"].setText(c.get("秒杀技4九品"))
        self.kill4_inputs["kill4_small1"].setText(c.get("秒杀技4小招1"))
        self.kill4_inputs["kill4_big1"].setText(c.get("秒杀技4大招1"))
        self.kill4_inputs["kill4_small2"].setText(c.get("秒杀技4小招2"))
        self.kill4_inputs["kill4_big2"].setText(c.get("秒杀技4大招2"))
        self.kill4_inputs["kill4_refresh"].setText(c.get("秒杀技4刷新技"))
        self.kill4_inputs["kill4_wait"].setText(c.get("秒杀技4等待技"))

        # 自动战斗
        self.chk_auto_combat.setChecked(c.get_bool("自动战斗选项"))

        # 镇妖设置
        for i in range(1, 5):
            if i in self.tower_boss_skills:
                self.tower_boss_skills[i].setText(c.get(f"镇妖真君技能{i}.Text"))
        self.chk_tower_small.setChecked(c.get_bool("镇妖小号选项"))

        # 副本选项
        self.chk_dungeon.setChecked(c.get_bool("刷副本选项"))
        self.chk_store1.setChecked(c.get_bool("蓝紫存仓1"))
        self.chk_store2.setChecked(c.get_bool("蓝紫存仓2"))
        self.chk_store3.setChecked(c.get_bool("蓝紫存仓3"))
        self.chk_palace_exp.setChecked(c.get_bool("行宫刷经验选项"))
        self.chk_beast_exp.setChecked(c.get_bool("古兽刷经验选项"))
        self.chk_team_leader.setChecked(c.get_bool("团长选项"))
        self.chk_team_member.setChecked(c.get_bool("团员选项"))
        self.chk_chaos_exp.setChecked(c.get_bool("混沌入侵刷经验选项"))
        self.chk_tower_dungeon.setChecked(c.get_bool("刷镇妖选项"))
        self.chk_floor_1_12.setChecked(c.get_bool("镇妖1至12层"))
        self.chk_floor_13_24.setChecked(c.get_bool("镇妖13至24层"))
        self.chk_floor_25_36.setChecked(c.get_bool("镇妖25至36层"))
        self.chk_coord_farm.setChecked(c.get_bool("坐标打怪采集选项"))
        self.chk_circle_pickup.setChecked(c.get_bool("绕圈捡物选项"))
        self.chk_farm_monster.setChecked(c.get_bool("打怪选项"))
        self.chk_farm_mine.setChecked(c.get_bool("挖矿选项"))
        self.chk_farm_herb.setChecked(c.get_bool("挖草选项"))
        self.custom_coord.setText(c.get("自定义坐标.Text", "527, 356"))

        # 混沌模式
        self.chk_flip1.setChecked(c.get_bool("关底翻倍1"))
        self.chk_flip2.setChecked(c.get_bool("关底翻倍2"))
        self.chk_flip3.setChecked(c.get_bool("关底翻倍3"))
        self.chk_flip4.setChecked(c.get_bool("关底翻倍4"))
        self.chk_commerce.setChecked(c.get_bool("选修商令"))
        self.chk_chaos_half.setChecked(c.get_bool("混沌半图选项"))
        self.chk_train_baby.setChecked(c.get_bool("练宝宝选项"))
        self.chk_small_mode.setChecked(c.get_bool("小号选项"))
        self.chk_no_boss.setChecked(c.get_bool("队员不进boss房"))
        self.chk_spider_hero.setChecked(c.get_bool("全自动大蜘蛛英雄任务"))
        self.chk_four_kings.setChecked(c.get_bool("四大天王刷经验"))
        self.chk_four_kings_dual.setChecked(c.get_bool("四大天王主副双修"))

        # PvP
        self.chk_smart_arena.setChecked(c.get_bool("智能斗技选项"))
        self.chk_smart_arena2.setChecked(c.get_bool("智能斗技选项二"))
        self.radio_arena_solo.setChecked(c.get_bool("斗技单排"))
        self.radio_arena_team.setChecked(c.get_bool("斗技组队"))
        self.radio_arena_1v1.setChecked(c.get_bool("斗技1V1"))
        self.radio_arena_mode1.setChecked(c.get_bool("斗技模式1"))
        self.radio_arena_mode2.setChecked(c.get_bool("斗技模式2"))

        self.radio_bf_solo.setChecked(c.get_bool("战场单排"))
        self.radio_bf_team.setChecked(c.get_bool("战场组队"))
        self.chk_capture_flag.setChecked(c.get_bool("抢旗"))
        self.chk_guild_war.setChecked(c.get_bool("智能宗战选项"))

        self.chk_stop_500.setChecked(c.get_bool("满500分停止选项"))
        self.chk_stop_silver.setChecked(c.get_bool("白银停止选项"))
        self.chk_stop_gold.setChecked(c.get_bool("黄金停止选项"))

        # 装备
        self.chk_refine.setChecked(c.get_bool("精炼选项"))
        self.refine_target.setCurrentIndex(c.get_int("精炼目标选项.ListIndex", 7))
        self.refine_retreat.setText(c.get("精炼加几回退选项.Text", "2"))

        self.recast_attr1.setCurrentIndex(c.get_int("第1个重铸目标属性.ListIndex", 0))
        self.recast_attr2.setCurrentIndex(c.get_int("第2个重铸目标属性.ListIndex", 2))
        self.recast_attr3.setCurrentIndex(c.get_int("第3个重铸目标属性.ListIndex", 3))
        self.recast_skill_keep.setText(c.get("重铸选第几个技能保留.Text", "3"))
        self.recast_stop_count.setText(c.get("重铸出几条目标属性停止.Text", "2"))
        self.chk_batch_craft.setChecked(c.get_bool("批量合成、重铸装备选项"))

        self.chk_jade.setChecked(c.get_bool("合玉晶石选项"))
        self.chk_jade2.setChecked(c.get_bool("合二阶选项"))
        self.chk_jade3.setChecked(c.get_bool("合三阶选项"))
        self.chk_jade4.setChecked(c.get_bool("合四阶选项"))
        self.chk_jade5.setChecked(c.get_bool("合五阶选项"))

        # 日常任务
        task_map = {
            "声望任务": "声望任务选项", "三次斗技胜利": "三次斗技胜利选项",
            "好友对话": "好友对话选项", "末日任务": "末日任务选项",
            "兽灵山谷": "兽灵山谷选项", "军备押送": "军备押送选项",
            "抓残魄": "抓残魄选项", "挖宝": "挖宝选项",
            "矿洞挖矿": "矿洞挖矿选项", "月光宝盒": "月光宝盒选项",
            "决战昆仑": "决战昆仑选项", "商店购买": "商店购买选项",
            "精炼装备": "精炼装备选项", "切换角色": "切换角色选项",
            "血战到底": "血战到底选项", "情人节任务": "情人节任务选项",
            "除尘任务": "除尘任务选项", "宝宝开洞": "宝宝开洞选项",
            "练宝宝": "练宝宝选项", "刷活跃": "刷活跃选项",
            "宗派建设": "宗派建设选项", "门派赛": "门派赛选项",
            "妖王赛": "妖王赛选项", "购买偶人": "购买偶人选项",
            "请愿钻风": "请愿钻风选项", "智能接英雄任务": "智能接英雄任务选项",
            "刷行宫": "刷行宫选项", "刷古兽": "刷古兽选项",
            "刷行宫四大天王": "刷行宫四大天王选项",
            "十级号刷官银": "十级号刷官银选项", "混沌半图": "混沌半图选项",
        }
        for name, cfg_key in task_map.items():
            if name in self.task_checks:
                self.task_checks[name].setChecked(c.get_bool(cfg_key))

        # 灵兽节日日常
        self.baby_speed_mult.setText(c.get("变数器倍数.Text"))
        self.lock_skills_misc.setText(c.get("锁技能选项.Text", "例:1,4"))
        self.chk_jade2_misc.setChecked(c.get_bool("合二阶选项"))
        self.chk_jade3_misc.setChecked(c.get_bool("合三阶选项"))
        self.chk_jade4_misc.setChecked(c.get_bool("合四阶选项"))
        self.chk_jade5_misc.setChecked(c.get_bool("合五阶选项"))
        self.chk_dust_run.setChecked(c.get_bool("五一除尘跑环选项"))
        self.chk_valentine_run.setChecked(c.get_bool("情人节跑环选项"))
        self.chk_pray_run.setChecked(c.get_bool("请愿钻风选项"))
        self.chk_poison.setChecked(c.get_bool("端午艾草避毒选项"))
        self.chk_guild_build.setChecked(c.get_bool("宗派建设任务选项"))
        self.silver_receiver.setText(c.get("收钱人名字.Text"))
        self.chk_train_receiver.setChecked(c.get_bool("先练收钱号选项"))

        # 拍卖
        self.chk_auction_scan.setChecked(c.get_bool("拍卖扫货选项"))
        self.auction_price.setText(c.get("拍卖扫货价格.Text"))
        self.auction_qty.setText(c.get("拍卖扫货数量.Text"))
        self.auction_single_count.setText(c.get("拍卖单一扫货次数.Text"))
        self.auction_total_count.setText(c.get("拍卖扫货总次数.Text"))
        self.auction_interval.setText(c.get("拍卖搜索间隔.Text"))
        self.chk_auction_sell.setChecked(c.get_bool("拍卖卖出选项"))

        for i in range(1, 9):
            self.auction_slots[i].setChecked(c.get_bool(f"拍卖买第{i}格选项"))

        # 设置
        self.chk_auto_settings.setChecked(c.get_bool("自动设置选项"))
        self.chk_auto_shutdown.setChecked(c.get_bool("自动关机选项"))
        self.chk_small_no_boss.setChecked(c.get_bool("小号不去打BOSS选项"))
        self.chk_compare_store.setChecked(c.get_bool("对比存仓选项"))
        self.chk_stop_no_star.setChecked(c.get_bool("无锁定星停止"))
        self.speed_mult.setText(c.get("加速倍数选项.Text", "8"))
        self.target_value.setText(c.get("目标数值选项.Text", "15"))
        self.lock_skills.setText(c.get("锁技能选项.Text", "例:1,4"))
        game_title = c.get("游戏窗口标题.Text", "斗战神")
        if game_title:
            self.game_title_input.setText(game_title)
        
        # 固定分辨率设置
        fixed_width = c.get("固定分辨率宽度.Text", "1600")
        fixed_height = c.get("固定分辨率高度.Text", "900")
        self.fixed_width_input.setText(fixed_width)
        self.fixed_height_input.setText(fixed_height)
        self.use_fixed_resolution_chk.setChecked(c.get_bool("使用固定分辨率选项", True))

        equipment_slots = ["帽子", "肩膀", "胸甲", "腰带", "裤子", "护腕", "手套", "鞋子", "武器"]
        for slot in equipment_slots:
            self.spirit_checks[f"主身_{slot}"].setChecked(c.get_bool(f"主身喂灵{slot}自定义"))
            self.spirit_checks[f"元神_{slot}"].setChecked(c.get_bool(f"元神喂灵{slot}自定义"))
            self.spirit_checks[f"材料_{slot}"].setChecked(c.get_bool(f"材料喂灵{slot}自定义"))

    def _collect_gui_to_config(self):
        c = self.config

        # 点击按键
        for i in range(1, 6):
            if i in self.click_keys:
                c.set(f"点击按键{i}.Text", self.click_keys[i].text())
                c.set(f"点击按键延迟{i}.Text", self.click_delays[i].text())

        # 绕圈大小
        c.set("绕圈大小.ListIndex", str(self.circle_size_combo.currentIndex()))

        # 按住按键
        for i in range(1, 6):
            if i in self.hold_keys:
                c.set(f"按住按键{i}.Text", self.hold_keys[i].text())
        c.set("普攻选项", self.normal_attack_key.text())

        # 功能快捷键
        c.set("上移键.Text", self.hotkey_up.text())
        c.set("左移键.Text", self.hotkey_left.text())
        c.set("下移键.Text", self.hotkey_down.text())
        c.set("右移键.Text", self.hotkey_right.text())
        c.set("格挡键.Text", self.hotkey_block.text())
        c.set("跟随键.Text", self.hotkey_follow.text())
        c.set("秒杀技1.Text", self.hotkey_kill1.text())
        c.set("受身键.Text", self.hotkey_dodge.text())
        c.set("拣物键.Text", self.hotkey_pickup.text())
        c.set("秒杀技2.Text", self.hotkey_kill2.text())
        c.set("打断后摇键.Text", self.hotkey_interrupt.text())
        c.set("组队键.Text", self.hotkey_team.text())
        c.set("秒杀技3.Text", self.hotkey_kill3.text())
        c.set("切换目标.Text", self.hotkey_target.text())
        c.set("秒杀技4.Text", self.hotkey_kill4.text())

        # 秒杀技4选项
        c.set("秒杀技4隐身", self.kill4_inputs["kill4_stealth"].text())
        c.set("秒杀技4九品", self.kill4_inputs["kill4_jiupin"].text())
        c.set("秒杀技4小招1", self.kill4_inputs["kill4_small1"].text())
        c.set("秒杀技4大招1", self.kill4_inputs["kill4_big1"].text())
        c.set("秒杀技4小招2", self.kill4_inputs["kill4_small2"].text())
        c.set("秒杀技4大招2", self.kill4_inputs["kill4_big2"].text())
        c.set("秒杀技4刷新技", self.kill4_inputs["kill4_refresh"].text())
        c.set("秒杀技4等待技", self.kill4_inputs["kill4_wait"].text())

        # 自动战斗
        c.set_bool("自动战斗选项", self.chk_auto_combat.isChecked())

        # 镇妖设置
        for i in range(1, 5):
            if i in self.tower_boss_skills:
                c.set(f"镇妖真君技能{i}.Text", self.tower_boss_skills[i].text())
        c.set_bool("镇妖小号选项", self.chk_tower_small.isChecked())

        # 副本选项
        c.set_bool("刷副本选项", self.chk_dungeon.isChecked())
        c.set_bool("蓝紫存仓1", self.chk_store1.isChecked())
        c.set_bool("蓝紫存仓2", self.chk_store2.isChecked())
        c.set_bool("蓝紫存仓3", self.chk_store3.isChecked())
        c.set_bool("行宫刷经验选项", self.chk_palace_exp.isChecked())
        c.set_bool("古兽刷经验选项", self.chk_beast_exp.isChecked())
        c.set_bool("团长选项", self.chk_team_leader.isChecked())
        c.set_bool("团员选项", self.chk_team_member.isChecked())
        c.set_bool("混沌入侵刷经验选项", self.chk_chaos_exp.isChecked())
        c.set_bool("刷镇妖选项", self.chk_tower_dungeon.isChecked())
        c.set_bool("镇妖1至12层", self.chk_floor_1_12.isChecked())
        c.set_bool("镇妖13至24层", self.chk_floor_13_24.isChecked())
        c.set_bool("镇妖25至36层", self.chk_floor_25_36.isChecked())
        c.set_bool("坐标打怪采集选项", self.chk_coord_farm.isChecked())
        c.set_bool("绕圈捡物选项", self.chk_circle_pickup.isChecked())
        c.set_bool("打怪选项", self.chk_farm_monster.isChecked())
        c.set_bool("挖矿选项", self.chk_farm_mine.isChecked())
        c.set_bool("挖草选项", self.chk_farm_herb.isChecked())
        c.set("自定义坐标.Text", self.custom_coord.text())

        # 混沌模式
        c.set_bool("关底翻倍1", self.chk_flip1.isChecked())
        c.set_bool("关底翻倍2", self.chk_flip2.isChecked())
        c.set_bool("关底翻倍3", self.chk_flip3.isChecked())
        c.set_bool("关底翻倍4", self.chk_flip4.isChecked())
        c.set_bool("选修商令", self.chk_commerce.isChecked())
        c.set_bool("混沌半图选项", self.chk_chaos_half.isChecked())
        c.set_bool("练宝宝选项", self.chk_train_baby.isChecked())
        c.set_bool("小号选项", self.chk_small_mode.isChecked())
        c.set_bool("队员不进boss房", self.chk_no_boss.isChecked())
        c.set_bool("全自动大蜘蛛英雄任务", self.chk_spider_hero.isChecked())
        c.set_bool("四大天王刷经验", self.chk_four_kings.isChecked())
        c.set_bool("四大天王主副双修", self.chk_four_kings_dual.isChecked())

        # PvP
        c.set_bool("智能斗技选项", self.chk_smart_arena.isChecked())
        c.set_bool("智能斗技选项二", self.chk_smart_arena2.isChecked())
        c.set_bool("斗技单排", self.radio_arena_solo.isChecked())
        c.set_bool("斗技组队", self.radio_arena_team.isChecked())
        c.set_bool("斗技1V1", self.radio_arena_1v1.isChecked())
        c.set_bool("斗技模式1", self.radio_arena_mode1.isChecked())
        c.set_bool("斗技模式2", self.radio_arena_mode2.isChecked())

        c.set_bool("战场单排", self.radio_bf_solo.isChecked())
        c.set_bool("战场组队", self.radio_bf_team.isChecked())
        c.set_bool("抢旗", self.chk_capture_flag.isChecked())
        c.set_bool("智能宗战选项", self.chk_guild_war.isChecked())

        c.set_bool("满500分停止选项", self.chk_stop_500.isChecked())
        c.set_bool("白银停止选项", self.chk_stop_silver.isChecked())
        c.set_bool("黄金停止选项", self.chk_stop_gold.isChecked())

        # 装备
        c.set_bool("精炼选项", self.chk_refine.isChecked())
        c.set("精炼目标选项.ListIndex", str(self.refine_target.currentIndex()))
        c.set("精炼加几回退选项.Text", self.refine_retreat.text())

        c.set("第1个重铸目标属性.ListIndex", str(self.recast_attr1.currentIndex()))
        c.set("第2个重铸目标属性.ListIndex", str(self.recast_attr2.currentIndex()))
        c.set("第3个重铸目标属性.ListIndex", str(self.recast_attr3.currentIndex()))
        c.set("重铸选第几个技能保留.Text", self.recast_skill_keep.text())
        c.set("重铸出几条目标属性停止.Text", self.recast_stop_count.text())
        c.set_bool("批量合成、重铸装备选项", self.chk_batch_craft.isChecked())

        c.set_bool("合玉晶石选项", self.chk_jade.isChecked())
        c.set_bool("合二阶选项", self.chk_jade2.isChecked())
        c.set_bool("合三阶选项", self.chk_jade3.isChecked())
        c.set_bool("合四阶选项", self.chk_jade4.isChecked())
        c.set_bool("合五阶选项", self.chk_jade5.isChecked())

        # 日常任务
        task_map = {
            "声望任务": "声望任务选项", "三次斗技胜利": "三次斗技胜利选项",
            "好友对话": "好友对话选项", "末日任务": "末日任务选项",
            "兽灵山谷": "兽灵山谷选项", "军备押送": "军备押送选项",
            "抓残魄": "抓残魄选项", "挖宝": "挖宝选项",
            "矿洞挖矿": "矿洞挖矿选项", "月光宝盒": "月光宝盒选项",
            "决战昆仑": "决战昆仑选项", "商店购买": "商店购买选项",
            "精炼装备": "精炼装备选项", "切换角色": "切换角色选项",
            "血战到底": "血战到底选项", "情人节任务": "情人节任务选项",
            "除尘任务": "除尘任务选项", "宝宝开洞": "宝宝开洞选项",
            "练宝宝": "练宝宝选项", "刷活跃": "刷活跃选项",
            "宗派建设": "宗派建设选项", "门派赛": "门派赛选项",
            "妖王赛": "妖王赛选项", "购买偶人": "购买偶人选项",
            "请愿钻风": "请愿钻风选项", "智能接英雄任务": "智能接英雄任务选项",
            "刷行宫": "刷行宫选项", "刷古兽": "刷古兽选项",
            "刷行宫四大天王": "刷行宫四大天王选项",
            "十级号刷官银": "十级号刷官银选项", "混沌半图": "混沌半图选项",
        }
        for name, cfg_key in task_map.items():
            if name in self.task_checks:
                c.set_bool(cfg_key, self.task_checks[name].isChecked())

        # 灵兽节日日常
        c.set("变数器倍数.Text", self.baby_speed_mult.text())
        c.set("锁技能选项.Text", self.lock_skills_misc.text())
        c.set_bool("合二阶选项", self.chk_jade2_misc.isChecked())
        c.set_bool("合三阶选项", self.chk_jade3_misc.isChecked())
        c.set_bool("合四阶选项", self.chk_jade4_misc.isChecked())
        c.set_bool("合五阶选项", self.chk_jade5_misc.isChecked())
        c.set_bool("五一除尘跑环选项", self.chk_dust_run.isChecked())
        c.set_bool("情人节跑环选项", self.chk_valentine_run.isChecked())
        c.set_bool("请愿钻风选项", self.chk_pray_run.isChecked())
        c.set_bool("端午艾草避毒选项", self.chk_poison.isChecked())
        c.set_bool("宗派建设任务选项", self.chk_guild_build.isChecked())
        c.set("收钱人名字.Text", self.silver_receiver.text())
        c.set_bool("先练收钱号选项", self.chk_train_receiver.isChecked())

        # 拍卖
        c.set_bool("拍卖扫货选项", self.chk_auction_scan.isChecked())
        c.set("拍卖扫货价格.Text", self.auction_price.text())
        c.set("拍卖扫货数量.Text", self.auction_qty.text())
        c.set("拍卖单一扫货次数.Text", self.auction_single_count.text())
        c.set("拍卖扫货总次数.Text", self.auction_total_count.text())
        c.set("拍卖搜索间隔.Text", self.auction_interval.text())
        c.set_bool("拍卖卖出选项", self.chk_auction_sell.isChecked())

        for i in range(1, 9):
            c.set_bool(f"拍卖买第{i}格选项", self.auction_slots[i].isChecked())

        # 设置
        c.set_bool("自动设置选项", self.chk_auto_settings.isChecked())
        c.set_bool("自动关机选项", self.chk_auto_shutdown.isChecked())
        c.set_bool("小号不去打BOSS选项", self.chk_small_no_boss.isChecked())
        c.set_bool("对比存仓选项", self.chk_compare_store.isChecked())
        c.set_bool("无锁定星停止", self.chk_stop_no_star.isChecked())
        c.set("加速倍数选项.Text", self.speed_mult.text())
        c.set("目标数值选项.Text", self.target_value.text())
        c.set("锁技能选项.Text", self.lock_skills.text())
        c.set("游戏窗口标题.Text", self.game_title_input.text())
        
        # 固定分辨率设置
        c.set("固定分辨率宽度.Text", self.fixed_width_input.text())
        c.set("固定分辨率高度.Text", self.fixed_height_input.text())
        c.set_bool("使用固定分辨率选项", self.use_fixed_resolution_chk.isChecked())

        equipment_slots = ["帽子", "肩膀", "胸甲", "腰带", "裤子", "护腕", "手套", "鞋子", "武器"]
        for slot in equipment_slots:
            c.set_bool(f"主身喂灵{slot}自定义", self.spirit_checks[f"主身_{slot}"].isChecked())
            c.set_bool(f"元神喂灵{slot}自定义", self.spirit_checks[f"元神_{slot}"].isChecked())
            c.set_bool(f"材料喂灵{slot}自定义", self.spirit_checks[f"材料_{slot}"].isChecked())

    def check_update(self):
        self.check_update_btn.setEnabled(False)
        self.update_status.setText("检查中...")
        self.log_signal.emit("[更新] 正在检查更新...")

        def do_check():
            updater = Updater()
            result = updater.check_update()

            if result["error"]:
                self.log_signal.emit(f"[更新] 检查失败: {result['error']}")
                self.update_status.setText("检查失败")
                self.check_update_btn.setEnabled(True)
                return

            if result["has_update"]:
                self.log_signal.emit(f"[更新] 发现新版本: {result['latest_version']}")
                msg = f"发现新版本 {result['latest_version']}\n\n"
                if result["changelog"]:
                    msg += f"更新内容:\n{result['changelog']}\n\n"
                msg += "是否前往下载?"
                reply = QMessageBox.question(
                    self, "发现新版本", msg,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    import webbrowser
                    webbrowser.open(result["download_url"])
                self.update_status.setText(f"新版本: {result['latest_version']}")
            else:
                self.log_signal.emit(f"[更新] 当前已是最新版本: {APP_VERSION}")
                self.update_status.setText("已是最新")

            self.check_update_btn.setEnabled(True)

        threading.Thread(target=do_check, daemon=True).start()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.WindowText, QColor(0, 0, 0))
    palette.setColor(QPalette.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
    palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.Text, QColor(0, 0, 0))
    palette.setColor(QPalette.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ButtonText, QColor(0, 0, 0))
    palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.Link, QColor(0, 0, 238))
    palette.setColor(QPalette.Highlight, QColor(0, 120, 215))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
