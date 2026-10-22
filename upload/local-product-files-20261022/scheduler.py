"""
scheduler.py - KLL 仿生调度架构
实现了单线程任务队列、状态机和错误熔断机制。
"""
import time
import json
import urllib.request
import websocket
import traceback

# 动作的生命周期状态
class AssistantState:
    IDLE = 0      # 空闲
    STARTED = 1   # 执行中
    ERROR = 2     # 报错

class AutoAction:
    """所有原子动作的基类 (相当于 KLL 的 AutoAction)"""
    def __init__(self, name):
        self.name = name
        self.state = AssistantState.IDLE
        self._on_finished_callbacks = []
        self._on_error_callbacks = []

    def start_action(self):
        """触发动作执行"""
        self.state = AssistantState.STARTED
        print(f"  ▶ 开始执行: [{self.name}]")
        try:
            self.execute()  # 调用子类实现的真实逻辑
            self.state = AssistantState.IDLE
            print(f"  ✔ 完成: [{self.name}]")
            for cb in self._on_finished_callbacks: cb()
        except Exception as e:
            self.state = AssistantState.ERROR
            print(f"  ✖ 失败: [{self.name}] - {e}")
            traceback.print_exc()
            for cb in self._on_error_callbacks: cb()

    def execute(self):
        """子类必须实现这个方法，写入具体的 CDP 点击逻辑"""
        raise NotImplementedError("必须实现 execute 方法")

    def on_finished(self, callback):
        self._on_finished_callbacks.append(callback)
        return self

    def on_error(self, callback):
        self._on_error_callbacks.append(callback)
        return self


class AutoActionQueue:
    """任务队列 (相当于 KLL 的 AutoActionQueue)"""
    def __init__(self):
        self.queue = []
        self.is_running = False

    def add(self, action: AutoAction):
        """将动作加入队列，并尝试启动执行"""
        self.queue.append(action)
        self._run_next()

    def _run_next(self):
        """单线程顺序执行队列中的动作"""
        if self.is_running or not self.queue:
            return
        
        self.is_running = True
        action = self.queue.pop(0)  # 取出第一个任务
        
        # 绑定成功和失败的回调
        action.on_finished(self._on_action_finished)
        action.on_error(self._on_action_error)
        
        # 执行（同步阻塞，执行完才会走回调）
        action.start_action()

    def _on_action_finished(self):
        """动作成功后，继续执行下一个"""
        self.is_running = False
        self._run_next()

    def _on_action_error(self):
        """熔断机制：任何动作失败，立即清空后续队列，防止乱点"""
        print("\n🚨 [熔断] 动作失败，清空后续队列，等待下一轮调度。")
        self.queue.clear()
        self.is_running = False


class Scheduler:
    """决策大脑 (相当于 KLL 的 AutoExpedition / AutoSortie)"""
    def __init__(self, queue: AutoActionQueue):
        self.queue = queue
        self.is_started = False

    def start(self):
        self.is_started = True
        print("Scheduler 已启动")

    def update(self):
        """核心调度逻辑：读取状态 -> 决定要做什么 -> 扔进队列"""
        # 如果队列正在执行，或者调度器没启动，直接跳过
        if not self.is_started or self.queue.is_running:
            return

        # 1. 读取当前 Redux 状态 (调用 CDP)
        state = self._read_fleet_state()
        if not state:
            return

        # 2. 优先级最高：收取已完成的远征
        for f in state:
            if f['mission'][0] == 2: # 状态 2 代表远征完成待收取
                from ..actions.collect_action import ActCollect
                print(f"[调度] 发现舰队 {f['id']} 待收取，加入队列")
                self.queue.add(ActCollect(f['id']))
                return # 每次 Update 只入队一个动作，交由队列循环执行

        # 3. 优先级其次：派遣空闲舰队
        for f in state:
            # 如果是空闲舰队 (0)，并且是 2/3/4 队 (1队不能远征)
            if f['mission'][0] == 0 and f['id'] in [2, 3, 4]:
                from ..actions.expedition_action import ActExpedition
                print(f"[调度] 发现舰队 {f['id']} 空闲，准备派遣远征 1")
                self.queue.add(ActExpedition(f['id'], 1))
                return
        
        # 4. 都没事做，静默等待

    def _read_fleet_state(self):
        """通过 CDP 读取舰队状态 (精简版)"""
        CDP = "http://127.0.0.1:9222"
        try:
            with urllib.request.urlopen(f"{CDP}/json", timeout=5) as r:
                ts = json.loads(r.read().decode())
            ws_url = next((x.get("webSocketDebuggerUrl") for x in ts if x.get("type") == "page" and x.get("title") == "poi"), None)
            if not ws_url: return None
            
            ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)
            e = "JSON.stringify((window.getStore?window.getStore('info.fleets'):[]).map(f=>({id:f.api_id,mission:f.api_mission})))"
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": e, "returnByValue": True}}))
            res = json.loads(ws.recv())
            ws.close()
            return json.loads(res["result"]["result"]["value"])
        except Exception:
            return None