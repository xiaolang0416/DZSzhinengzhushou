"""
配置管理器 - 读写 INI 配置文件
兼容原始 uservar.ini 格式
"""
import os
import sys
import configparser
from pathlib import Path


class ConfigManager:
    DEFAULT_CONFIG_NAME = "uservar.ini"
    SECTION_NAME = "18087ace-317a-4e54-aa1d-479c5c4fedcd"

    def __init__(self):
        self._data = {}
        self._config_path = None

    def _get_base_dir(self):
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def get_config_path(self):
        if self._config_path:
            return self._config_path
        return os.path.join(self._get_base_dir(), self.DEFAULT_CONFIG_NAME)

    def _make_parser(self):
        config = configparser.ConfigParser()
        config.optionxform = str
        return config

    def load(self, path):
        self._config_path = path
        self._data = {}

        config = self._make_parser()
        config.read(path, encoding='utf-8')

        if self.SECTION_NAME in config:
            for key, value in config[self.SECTION_NAME].items():
                self._data[key] = value
        else:
            for section in config.sections():
                for key, value in config[section].items():
                    self._data[key] = value

    def save(self, path):
        config = self._make_parser()
        config[self.SECTION_NAME] = self._data
        config["任务选中"] = {"列表": "好友对话|商店购买|示例坐标采集"}

        with open(path, 'w', encoding='utf-8') as f:
            config.write(f)

    def get(self, key, default=""):
        return self._data.get(key, default)

    def get_int(self, key, default=0):
        try:
            return int(self._data.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def get_bool(self, key, default=False):
        val = self._data.get(key, "")
        if val == "1":
            return True
        elif val == "0":
            return False
        return default

    def get_float(self, key, default=0.0):
        try:
            return float(self._data.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def set(self, key, value):
        self._data[key] = str(value)

    def set_bool(self, key, value):
        self._data[key] = "1" if value else "0"
