"""
guard.py - 安全守卫模块
职责：动作白名单、人工授权拦截、连续失败熔断锁定。
必须独立于 AI 和调度器，规则不可被程序自动覆盖。
"""

class SafetyGuard:
    def __init__(self):
        self.consecutive_failures = 0
        self.max_failures = 3       # 连续失败 3 次触发熔断
        self.is_locked = False      # 熔断锁定状态
        # 是否强制要求人工授权 (True 表示危险动作必须人工确认)
        self.manual_approval_required = True 
        
        # 允许执行的动作白名单
        self.allowed_actions = {"wait", "collect", "dispatch"}

    def check_action(self, decision_action: str, is_authorized: bool = False) -> tuple[bool, str]:
        """
        检查动作是否允许执行
        返回: (是否放行, 原因/提示)
        """
        # 1. 熔断机制优先级最高
        if self.is_locked:
            return False, "熔断器已触发，处于锁定状态，必须人工检查后重置"

        # 2. 检查动作是否在白名单内
        if decision_action not in self.allowed_actions:
            return False, f"未知动作 '{decision_action}'，拒绝执行"

        # 3. 危险动作的人工授权拦截
        # 收取(collect)和派遣(dispatch)具有业务后果，必须要求授权
        if decision_action in ("collect", "dispatch"):
            if self.manual_approval_required and not is_authorized:
                return False, f"动作 '{decision_action}' 需要人工授权，当前未授权"

        # 4. 安全放行
        return True, "安全守卫放行"

    def record_failure(self) -> bool:
        """
        记录一次动作失败。
        返回: 熔断器是否仍然处于可用状态 (未达到阈值)
        """
        self.consecutive_failures += 1
        print(f"[GUARD] 记录失败次数: {self.consecutive_failures}/{self.max_failures}")
        
        if self.consecutive_failures >= self.max_failures:
            self.is_locked = True
            print("🚨 [GUARD] 连续失败达到阈值，触发全局熔断！")
            return False
        return True

    def reset_failures(self):
        """成功执行动作后失败计数 (但不解除熔断锁定)"""
        self.consecutive_failures = 0

    def manual_unlock(self):
        """人工物理解锁：只能在控制台或代码中手动调用，AI 无权解除"""
        self.is_locked = False
        self.consecutive_failures = 0
        print("🔓 [GUARD] 已由人工解除熔断锁定")