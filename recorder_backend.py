"""
录制后端 - 录制角色创建副本、进入副本、跑图、打怪、打Boss等操作
录制数据以副本名字保存，保存目录为"副本数据"
与前端功能强关联，支持前端自动化回放
"""
import json
import time
import threading
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    import pyHook
    import pythoncom
    HAS_PYHOOK = True
except ImportError:
    HAS_PYHOOK = False

try:
    from pynput import keyboard, mouse
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False


class ActionRecorder:
    """动作录制器 - 录制键盘鼠标操作"""
    
    def __init__(self):
        self.actions: List[Dict[str, Any]] = []
        self.start_time: float = 0
        self.is_recording: bool = False
        self._lock = threading.Lock()
        self._mouse_listener = None
        self._keyboard_listener = None
        self._current_context: str = "idle"  # idle, combat, movement, dungeon, boss, settlement
        
    def start_recording(self):
        """开始录制"""
        if self.is_recording:
            return
        
        self.actions = []
        self.start_time = time.time()
        self.is_recording = True
        
        if HAS_PYNPUT:
            self._mouse_listener = mouse.Listener(
                on_move=self._on_mouse_move,
                on_click=self._on_mouse_click,
                on_scroll=self._on_mouse_scroll
            )
            self._keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release
            )
            self._mouse_listener.start()
            self._keyboard_listener.start()
            print("[录制] 开始录制 (pynput)")
        elif HAS_PYHOOK:
            self._start_pyhook()
            print("[录制] 开始录制 (pyHook)")
        else:
            print("[录制] 错误: 未找到可用的录制库 (pynput 或 pyHook)")
            self.is_recording = False
    
    def stop_recording(self) -> List[Dict[str, Any]]:
        """停止录制并返回录制的动作"""
        if not self.is_recording:
            return []
        
        self.is_recording = False
        
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()
        
        if HAS_PYHOOK and not HAS_PYNPUT:
            pythoncom.PumpMessages()  # 停止消息循环
        
        print(f"[录制] 停止录制，共录制 {len(self.actions)} 个动作")
        return self.actions.copy()
    
    def set_context(self, context: str):
        """设置当前录制上下文"""
        self._current_context = context
        self._add_action("context_change", {"context": context})
    
    def _add_action(self, action_type: str, data: Dict[str, Any]):
        """添加动作到录制列表"""
        if not self.is_recording:
            return
        
        with self._lock:
            action = {
                "type": action_type,
                "timestamp": time.time() - self.start_time,
                "context": self._current_context,
                "data": data
            }
            self.actions.append(action)
    
    def _on_mouse_move(self, x, y):
        """鼠标移动事件"""
        # 不记录所有移动，只记录显著移动
        pass
    
    def _on_mouse_click(self, x, y, button, pressed):
        """鼠标点击事件"""
        if pressed:
            self._add_action("mouse_click", {
                "x": x,
                "y": y,
                "button": str(button),
                "action": "press"
            })
        else:
            self._add_action("mouse_click", {
                "x": x,
                "y": y,
                "button": str(button),
                "action": "release"
            })
    
    def _on_mouse_scroll(self, x, y, dx, dy):
        """鼠标滚轮事件"""
        self._add_action("mouse_scroll", {
            "x": x,
            "y": y,
            "dx": dx,
            "dy": dy
        })
    
    def _on_key_press(self, key):
        """键盘按下事件"""
        try:
            key_name = key.char if hasattr(key, 'char') and key.char else str(key)
        except:
            key_name = str(key)
        
        self._add_action("key_press", {
            "key": key_name,
            "action": "press"
        })
    
    def _on_key_release(self, key):
        """键盘释放事件"""
        try:
            key_name = key.char if hasattr(key, 'char') and key.char else str(key)
        except:
            key_name = str(key)
        
        self._add_action("key_release", {
            "key": key_name,
            "action": "release"
        })
    
    def _start_pyhook(self):
        """使用 pyHook 开始录制"""
        hm = pyHook.HookManager()
        hm.MouseAll = self._pyhook_mouse_handler
        hm.KeyAll = self._pyhook_key_handler
        hm.HookMouse()
        hm.HookKeyboard()
    
    def _pyhook_mouse_handler(self, event):
        """pyHook 鼠标事件处理"""
        if event.MessageName == 'mouse left down':
            self._add_action("mouse_click", {
                "x": event.Position[0],
                "y": event.Position[1],
                "button": "left",
                "action": "press"
            })
        elif event.MessageName == 'mouse left up':
            self._add_action("mouse_click", {
                "x": event.Position[0],
                "y": event.Position[1],
                "button": "left",
                "action": "release"
            })
        elif event.MessageName == 'mouse right down':
            self._add_action("mouse_click", {
                "x": event.Position[0],
                "y": event.Position[1],
                "button": "right",
                "action": "press"
            })
        elif event.MessageName == 'mouse right up':
            self._add_action("mouse_click", {
                "x": event.Position[0],
                "y": event.Position[1],
                "button": "right",
                "action": "release"
            })
        return True
    
    def _pyhook_key_handler(self, event):
        """pyHook 键盘事件处理"""
        if event.MessageName == 'key down':
            self._add_action("key_press", {
                "key": event.Key,
                "action": "press"
            })
        elif event.MessageName == 'key up':
            self._add_action("key_release", {
                "key": event.Key,
                "action": "release"
            })
        return True


class DungeonRecorder:
    """副本录制器 - 录制完整副本流程"""
    
    def __init__(self, save_dir: str = "副本数据"):
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(exist_ok=True)
        
        self.recorder = ActionRecorder()
        self.current_dungeon: Optional[str] = None
        self.dungeon_data: Dict[str, Any] = {}
        self.is_recording_dungeon: bool = False
        
        # 副本阶段
        self.phases = [
            "character_creation",  # 角色创建
            "dungeon_entry",       # 进入副本
            "map_running",         # 跑图
            "combat",              # 打怪
            "boss_fight",          # 打Boss
            "settlement",          # 副本结算
        ]
        self.current_phase: str = "idle"
        
        # 统计数据
        self.stats = {
            "monsters_killed": 0,
            "bosses_killed": 0,
            "items_collected": 0,
            "deaths": 0,
            "revives": 0,
        }
    
    def start_dungeon_recording(self, dungeon_name: str, difficulty: str = "普通"):
        """开始录制副本"""
        if self.is_recording_dungeon:
            print("[录制] 错误: 正在录制其他副本")
            return False
        
        self.current_dungeon = dungeon_name
        self.dungeon_data = {
            "dungeon_name": dungeon_name,
            "difficulty": difficulty,
            "record_time": datetime.now().isoformat(),
            "phases": {},
            "stats": self.stats.copy(),
            "metadata": {
                "version": "1.0",
                "frontend_version": "5.1",
                "compatible": True
            }
        }
        self.is_recording_dungeon = True
        self.current_phase = "idle"
        
        # 重置统计
        self.stats = {
            "monsters_killed": 0,
            "bosses_killed": 0,
            "items_collected": 0,
            "deaths": 0,
            "revives": 0,
        }
        
        # 开始录制
        self.recorder.start_recording()
        print(f"[录制] 开始录制副本: {dungeon_name} ({difficulty})")
        return True
    
    def stop_dungeon_recording(self) -> bool:
        """停止录制副本并保存"""
        if not self.is_recording_dungeon:
            print("[录制] 错误: 未在录制副本")
            return False
        
        # 停止录制
        actions = self.recorder.stop_recording()
        
        # 整理数据
        self.dungeon_data["actions"] = actions
        self.dungeon_data["stats"] = self.stats
        self.dungeon_data["duration"] = time.time() - self.recorder.start_time
        
        # 按阶段分组动作
        self._organize_phases(actions)
        
        # 保存数据
        if self._save_dungeon_data():
            print(f"[录制] 副本录制完成: {self.current_dungeon}")
            self.is_recording_dungeon = False
            self.current_dungeon = None
            return True
        else:
            print(f"[录制] 保存失败")
            return False
    
    def set_phase(self, phase: str):
        """设置当前副本阶段"""
        if phase not in self.phases and phase != "idle":
            print(f"[录制] 警告: 未知阶段 {phase}")
            return
        
        old_phase = self.current_phase
        self.current_phase = phase
        self.recorder.set_context(phase)
        print(f"[录制] 阶段变更: {old_phase} -> {phase}")
    
    def mark_monster_killed(self):
        """标记怪物击杀"""
        self.stats["monsters_killed"] += 1
        self.recorder._add_action("event", {
            "event": "monster_killed",
            "count": self.stats["monsters_killed"]
        })
    
    def mark_boss_killed(self):
        """标记Boss击杀"""
        self.stats["bosses_killed"] += 1
        self.recorder._add_action("event", {
            "event": "boss_killed",
            "count": self.stats["bosses_killed"]
        })
    
    def mark_item_collected(self, item_name: str = ""):
        """标记物品收集"""
        self.stats["items_collected"] += 1
        self.recorder._add_action("event", {
            "event": "item_collected",
            "item": item_name,
            "count": self.stats["items_collected"]
        })
    
    def mark_death(self):
        """标记死亡"""
        self.stats["deaths"] += 1
        self.recorder._add_action("event", {
            "event": "death",
            "count": self.stats["deaths"]
        })
    
    def mark_revive(self):
        """标记复活"""
        self.stats["revives"] += 1
        self.recorder._add_action("event", {
            "event": "revive",
            "count": self.stats["revives"]
        })
    
    def _organize_phases(self, actions: List[Dict[str, Any]]):
        """按阶段组织动作"""
        phase_actions = {phase: [] for phase in self.phases}
        phase_actions["idle"] = []
        
        for action in actions:
            context = action.get("context", "idle")
            if context in phase_actions:
                phase_actions[context].append(action)
            else:
                phase_actions["idle"].append(action)
        
        self.dungeon_data["phases"] = phase_actions
    
    def _save_dungeon_data(self) -> bool:
        """保存副本数据"""
        if not self.current_dungeon:
            return False
        
        # 创建副本目录
        dungeon_dir = self.save_dir / self.current_dungeon
        dungeon_dir.mkdir(exist_ok=True)
        
        # 生成文件名 (带时间戳)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.current_dungeon}_{timestamp}.json"
        filepath = dungeon_dir / filename
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.dungeon_data, f, ensure_ascii=False, indent=2)
            print(f"[录制] 数据已保存: {filepath}")
            return True
        except Exception as e:
            print(f"[录制] 保存失败: {e}")
            return False
    
    def load_dungeon_data(self, dungeon_name: str, timestamp: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """加载副本数据"""
        dungeon_dir = self.save_dir / dungeon_name
        if not dungeon_dir.exists():
            print(f"[录制] 副本数据不存在: {dungeon_name}")
            return None
        
        # 查找最新的录制文件
        json_files = list(dungeon_dir.glob("*.json"))
        if not json_files:
            print(f"[录制] 未找到录制文件: {dungeon_name}")
            return None
        
        if timestamp:
            # 查找特定时间戳的文件
            target = dungeon_dir / f"{dungeon_name}_{timestamp}.json"
            if target.exists():
                json_files = [target]
            else:
                print(f"[录制] 未找到指定时间戳的文件: {timestamp}")
                return None
        
        # 使用最新的文件
        latest_file = max(json_files, key=lambda f: f.stat().st_mtime)
        
        try:
            with open(latest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"[录制] 加载副本数据: {latest_file}")
            return data
        except Exception as e:
            print(f"[录制] 加载失败: {e}")
            return None
    
    def list_dungeons(self) -> List[str]:
        """列出所有已录制的副本"""
        if not self.save_dir.exists():
            return []
        
        dungeons = []
        for item in self.save_dir.iterdir():
            if item.is_dir():
                dungeons.append(item.name)
        
        return sorted(dungeons)
    
    def list_recordings(self, dungeon_name: str) -> List[str]:
        """列出副本的所有录制"""
        dungeon_dir = self.save_dir / dungeon_name
        if not dungeon_dir.exists():
            return []
        
        recordings = []
        for f in dungeon_dir.glob("*.json"):
            recordings.append(f.stem)
        
        return sorted(recordings)


class DungeonPlayer:
    """副本播放器 - 回放录制的副本数据"""
    
    def __init__(self, engine):
        """
        Args:
            engine: AutomationEngine 实例
        """
        self.engine = engine
        self.recorder = DungeonRecorder()
        self.is_playing: bool = False
        self._stop_flag: bool = False
    
    def play_dungeon(self, dungeon_name: str, timestamp: Optional[str] = None, speed: float = 1.0) -> bool:
        """
        播放副本录制
        
        Args:
            dungeon_name: 副本名称
            timestamp: 录制时间戳 (可选，默认最新)
            speed: 播放速度 (1.0 = 正常速度)
        """
        # 加载数据
        data = self.recorder.load_dungeon_data(dungeon_name, timestamp)
        if not data:
            print(f"[播放] 无法加载副本数据: {dungeon_name}")
            return False
        
        actions = data.get("actions", [])
        if not actions:
            print(f"[播放] 录制数据为空: {dungeon_name}")
            return False
        
        print(f"[播放] 开始播放: {dungeon_name} ({len(actions)} 个动作)")
        self.is_playing = True
        self._stop_flag = False
        
        start_time = time.time()
        
        try:
            for action in actions:
                if self._stop_flag:
                    print("[播放] 停止播放")
                    break
                
                # 计算延迟
                target_time = action["timestamp"] / speed
                elapsed = time.time() - start_time
                wait_time = target_time - elapsed
                
                if wait_time > 0:
                    time.sleep(wait_time)
                
                # 执行动作
                self._execute_action(action)
            
            print(f"[播放] 播放完成: {dungeon_name}")
            return True
        except Exception as e:
            print(f"[播放] 播放错误: {e}")
            return False
        finally:
            self.is_playing = False
    
    def stop_playback(self):
        """停止播放"""
        self._stop_flag = True
    
    def _execute_action(self, action: Dict[str, Any]):
        """执行单个动作"""
        action_type = action["type"]
        data = action["data"]
        
        if action_type == "key_press":
            key = data["key"]
            # 清理键名
            if key.startswith('Key.') and key != 'Key.space':
                key = key[4:]
            if key == 'Key.space':
                key = 'space'
            
            self.engine._press_key(key)
        
        elif action_type == "mouse_click":
            x, y = data["x"], data["y"]
            button = data["button"]
            
            if "left" in button:
                self.engine._click(x, y, button='left')
            elif "right" in button:
                self.engine._click(x, y, button='right')
        
        elif action_type == "mouse_scroll":
            x, y = data["x"], data["y"]
            dy = data["dy"]
            self.engine._move_to(x, y)
            if dy > 0:
                self.engine._scroll_up()
            elif dy < 0:
                self.engine._scroll_down()
        
        elif action_type == "context_change":
            # 上下文变更，可以触发特定逻辑
            context = data["context"]
            print(f"[播放] 上下文: {context}")
        
        elif action_type == "event":
            # 事件标记，可以触发特定逻辑
            event = data["event"]
            print(f"[播放] 事件: {event}")


# 便捷函数
def create_recorder(save_dir: str = "副本数据") -> DungeonRecorder:
    """创建副本录制器"""
    return DungeonRecorder(save_dir)

def create_player(engine) -> DungeonPlayer:
    """创建副本播放器"""
    return DungeonPlayer(engine)


if __name__ == "__main__":
    # 测试录制器
    recorder = create_recorder()
    
    print("=== 副本录制器测试 ===")
    print("可用命令:")
    print("  start <副本名> [难度] - 开始录制")
    print("  stop - 停止录制")
    print("  phase <阶段> - 设置阶段")
    print("  list - 列出副本")
    print("  quit - 退出")
    print()
    
    while True:
        try:
            cmd = input("录制器> ").strip()
            if not cmd:
                continue
            
            parts = cmd.split()
            action = parts[0].lower()
            
            if action == "start":
                if len(parts) < 2:
                    print("用法: start <副本名> [难度]")
                    continue
                dungeon_name = parts[1]
                difficulty = parts[2] if len(parts) > 2 else "普通"
                recorder.start_dungeon_recording(dungeon_name, difficulty)
            
            elif action == "stop":
                recorder.stop_dungeon_recording()
            
            elif action == "phase":
                if len(parts) < 2:
                    print("用法: phase <阶段>")
                    print("可用阶段: character_creation, dungeon_entry, map_running, combat, boss_fight, settlement")
                    continue
                recorder.set_phase(parts[1])
            
            elif action == "list":
                dungeons = recorder.list_dungeons()
                if dungeons:
                    print("已录制副本:")
                    for d in dungeons:
                        recordings = recorder.list_recordings(d)
                        print(f"  {d}: {len(recordings)} 个录制")
                else:
                    print("暂无录制数据")
            
            elif action == "quit":
                break
            
            else:
                print(f"未知命令: {action}")
        
        except KeyboardInterrupt:
            print("\n退出")
            break
        except Exception as e:
            print(f"错误: {e}")
