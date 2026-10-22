1. 状态解析器：state/state_parser.py
 
state_fetcher 拿到的 api_mission 是 [1, 2, 1792155961666, 0] 这种机器码，我们需要解析成人类可读的状态，并检查是否大破、入渠。
from typing import Dict, Any, List

MISSION_STATE = {0: "未派遣", 1: "远征中", 2: "已完成", 3: "已收取"}

def parse_fleet(fleet: Dict[str, Any]) -> Dict[str, Any]:
    """解析单支舰队的远征状态"""
    mission = fleet.get("api_mission", [0, 0, 0, 0])
    return {
        "fleet_id": fleet.get("api_id"),
        "fleet_name": fleet.get("api_name"),
        "mission_state_code": mission[0] if len(mission) > 0 else 0,
        "mission_state_text": MISSION_STATE.get(mission[0], "未知") if len(mission) > 0 else "未知",
        "mission_task_id": mission[1] if len(mission) > 1 else 0,
        "mission_completion_time": mission[2] if len(mission) > 2 else 0,
        "ships": fleet.get("api_ship", [])
    }

def check_safety_conditions(ships: Dict[str, Any], fleet_ship_ids: List[int], const_ships: Dict[str, Any]) -> tuple[bool, str]:
    """
    安全检查：检查是否有大破，是否有人入渠。
    如果没有问题返回 (True, "安全")
    """
    for sid in fleet_ship_ids:
        if sid == -1: continue
        ship = ships.get(str(sid))
        if not ship: return False, f"找不到舰娘 {sid} 数据"
        
        # 检查血量（大破）
        nowhp = ship.get("api_nowhp", 0)
        maxhp = ship.get("api_maxhp", 1)
        if nowhp / maxhp <= 0.25:
            return False, f"舰娘 {sid} 大破，拒绝出击/远征"
            
        # 检查入渠状态 (Poi 的 api_state == 2 为入渠中)
        # 注意：这里的 api_state 需要你在 state_fetcher 里额外抓取，如果暂时没有，可以先注释掉
        # if ship.get("api_state") == 2:
        #     return False, f"舰娘 {sid} 正在入渠"
            
    return True, "安全"
 
 
2. 远征任务配置表：scheduler/expedition_config.py
 
Bot 到底跑哪个远征？不能每次都在代码里写死 mission_id=1，需要一个配置表。
# 推荐跑的几个高效远征配置
EXPEDITION_TARGETS = {
    2: {"name": "長距離練習航海", "time": 30, "fleet": 2},
    5: {"name": "海上護衛任務", "time": 90, "fleet": 2},
    21: {"name": "北方鼠輸送作戦", "time": 140, "fleet": 3},
}

# 允许用于远征的舰队
DISPATCHABLE_FLEETS = [2, 3, 4]
 
 
3. 自动补给集成逻辑（在 scheduler.py 里补充）
 
我们在上面的 scheduler.py 中，只写了“空闲就派远征”。但实际上，如果舰队没满补给，派出去会直接失败。 所以我们在调度器里还需要加一层补给判断（如果你已经写了 ActSupply 这个动作，就可以直接在 update() 里插入）：
# 在 Scheduler.update() 中的派遣逻辑前，加入补给检查
        for f in state:
            if f['mission'][0] == 0 and f['id'] in [2, 3, 4]:
                # 这里简化处理，实际应该比对当前资源与满资源
                from ..actions.supply_action import ActSupply
                # 你可以写一个 needs_supply() 的函数来判断
                if self._needs_supply(f):
                    print(f"[调度] 舰队 {f['id']} 需要补给，加入队列")
                    self.queue.add(ActSupply(f['id']))
                    return
                else:
                    from ..actions.expedition_action import ActExpedition
                    print(f"[调度] 舰队 {f['id']} 状态良好，派遣远征")
                    self.queue.add(ActExpedition(f['id'], 1))
                    return
 
 
4. 缺失的 __init__.py 文件
 
如果你全丢了，记得在每个文件夹下建一个空的 __init__.py，否则 Python 找不到模块。
需要建这个空文件的地方：
• src/kancolle_bot/__init__.py
• src/kancolle_bot/core/__init__.py
• src/kancolle_bot/actions/__init__.py
• src/kancolle_bot/state/__init__.py
• src/kancolle_bot/safety/__init__.py