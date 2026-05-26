#!/usr/bin/env python3
"""
ADB 控制器 - MuMu Player Pro 操作封装
支持：点击、滑动、截图、节点操作
"""

from __future__ import annotations

import subprocess
import time
import re
from pathlib import Path
from typing import Tuple, Optional, List


class ADBController:
    """ADB 控制器的 Python 封装"""

    def __init__(self, host: str = "127.0.0.1", port: int = 16384):
        self.host = host
        self.port = port
        self.serial = f"{host}:{port}"
        self._verify_connection()

    # ---- ADB 基础命令 ----

    def _run(self, *args, timeout: int = 30) -> str:
        """执行 ADB 命令"""
        cmd = ["adb", "-s", self.serial] + list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode != 0:
                raise ADBError(f"ADB command failed: {result.stderr}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise ADBError("ADB command timeout")
        except FileNotFoundError:
            raise ADBError("ADB not found. Install Android Platform Tools and add to PATH")

    def _verify_connection(self):
        """验证设备连接"""
        try:
            self._run("shell", "echo", "connected")
        except ADBError as e:
            raise ADBError(f"Device not connected: {e}")

    # ---- 设备信息 ----

    def get_device_info(self) -> dict:
        """获取设备信息"""
        props = {}
        for line in self._run("shell", "getprop").split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                props[key.strip("[ ]")] = val.strip()
        return props

    def get_screen_size(self) -> Tuple[int, int]:
        """获取屏幕分辨率"""
        output = self._run("shell", "wm", "size")
        match = re.search(r"(\d+)x(\d+)", output)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1080, 1920  # 默认

    # ---- 截图 & 录屏 ----

    def screencap(self, save_path: str | Path, quality: int = 85) -> Path:
        """
        屏幕截图
        Args:
            save_path: 保存路径（.png）
            quality: jpeg 质量 1-100
        Returns:
            图片路径
        """
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # 方法1: screencap (推荐)
        try:
            self._run("shell", "screencap", "-p", str(save_path), timeout=10)
            if save_path.exists() and save_path.stat().st_size > 100:
                return save_path
        except ADBError:
            pass

        # 方法2: screencap to /sdcard + pull
        tmp_remote = "/sdcard/_tmp_screencap.png"
        self._run("shell", "screencap", "-p", tmp_remote)
        self._run("pull", tmp_remote, str(save_path))
        self._run("shell", "rm", tmp_remote)
        return save_path

    # ---- 输入操作 ----

    def tap(self, x: int, y: int, duration: int = 50):
        """
        点击屏幕
        Args:
            x, y: 坐标
            duration: 按住时长 (ms)
        """
        self._run("shell", "input", "tap", str(x), str(y))

    def swipe(
        self,
        x1: int, y1: int,
        x2: int, y2: int,
        duration: int = 300
    ):
        """
        滑动屏幕
        Args:
            x1, y1: 起始坐标
            x2, y2: 结束坐标
            duration: 持续时长 (ms)
        """
        self._run(
            "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(duration)
        )

    def swipe_up(self, duration: int = 300):
        """向上滑动（刷视频）"""
        w, h = self.get_screen_size()
        cx, cy = w // 2, h // 2
        self.swipe(cx, int(h * 0.7), cx, int(h * 0.3), duration)

    def swipe_down(self, duration: int = 300):
        """向下滑动"""
        w, h = self.get_screen_size()
        cx, cy = w // 2, h // 2
        self.swipe(cx, int(h * 0.3), cx, int(h * 0.7), duration)

    def input_text(self, text: str):
        """输入文本（需先聚焦输入框）"""
        # 替换特殊字符
        text = text.replace(" ", "%s").replace("'", "\\'")
        self._run("shell", "input", "text", text)

    def press_enter(self):
        """按回车键"""
        self._run("shell", "input", "keyevent", "ENTER")

    def press_back(self):
        """按返回键"""
        self._run("shell", "input", "keyevent", "KEYCODE_BACK")

    def press_home(self):
        """按 Home 键"""
        self._run("shell", "input", "keyevent", "KEYCODE_HOME")

    # ---- 界面节点（uiautomator2）----

    def get_current_ui_xml(self) -> str:
        """获取当前界面 XML（用于节点分析）"""
        tmp = "/sdcard/_ui_dump.xml"
        try:
            self._run("shell", "uiautomator2", "dump", tmp, timeout=15)
        except ADBError:
            # 备用方式
            self._run("shell", " dumpsys", "window", "windows", "|", "grep", "-i", "mCurrentFocus")
        try:
            self._run("pull", tmp, "/tmp/_ui_dump.xml")
            with open("/tmp/_ui_dump.xml") as f:
                return f.read()
        except Exception:
            return ""

    def find_node_by_text(self, xml: str, text: str, regex: bool = False) -> List[dict]:
        """在 UI XML 中查找节点"""
        nodes = []
        pattern = re.compile(rf'<node[^>]*text="([^"]*{re.escape(text)}[^"]*)"[^>]*>', re.IGNORECASE) if regex \
            else re.compile(rf'<node[^>]*text="{re.escape(text)}"[^>]*>')
        for m in pattern.finditer(xml):
            node_text = m.group(0)
            bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node_text)
            if bounds:
                nodes.append({
                    "text": text,
                    "bounds": (int(bounds.group(1)), int(bounds.group(2)),
                                int(bounds.group(3)), int(bounds.group(4))),
                    "raw": node_text,
                })
        return nodes

    def tap_node_center(self, bounds: Tuple[int, int, int, int]):
        """点击节点中心"""
        x = (bounds[0] + bounds[2]) // 2
        y = (bounds[1] + bounds[3]) // 2
        self.tap(x, y)

    # ---- 应用管理 ----

    def launch_app(self, package: str, activity: str):
        """启动应用"""
        self._run("shell", "am", "start", "-n", f"{package}/{activity}", "-W")

    def stop_app(self, package: str):
        """强制停止应用"""
        self._run("shell", "am", "force-stop", package)

    def get_current_app(self) -> str:
        """获取当前前台应用包名"""
        output = self._run("shell", "dumpsys", "window", "windows", "|", "grep", "-i", "mCurrentFocus")
        m = re.search(r"(\S+)/(\S+)", output)
        return m.group(1) if m else ""

    # ---- 文件操作 ----

    def push(self, local: str | Path, remote: str):
        """推送文件到设备"""
        self._run("push", str(local), remote)

    def pull(self, remote: str, local: str | Path):
        """从设备拉取文件"""
        self._run("pull", remote, str(local))

    # ---- 实用工具 ----

    def wait(self, seconds: float):
        """等待（秒）"""
        time.sleep(seconds)

    def screen_on(self):
        """点亮屏幕"""
        self._run("shell", "input", "keyevent", "KEYCODE_WAKEUP")

    def screen_off(self):
        """关闭屏幕"""
        self._run("shell", "input", "keyevent", "KEYCODE_SLEEP")

    def reboot(self):
        """重启设备"""
        self._run("reboot")


class ADBError(Exception):
    """ADB 操作异常"""
    pass


# ---- 单元测试 ----
if __name__ == "__main__":
    try:
        adb = ADBController()
        print(f"设备已连接: {adb.serial}")
        print(f"屏幕尺寸: {adb.get_screen_size()}")
        print(f"当前应用: {adb.get_current_app()}")
    except ADBError as e:
        print(f"错误: {e}")
        print("请确保 MuMu Player Pro 已启动且 ADB 端口 (16384) 已开启")