# -*- coding: utf-8 -*-
"""手动跑完整远征流程的编排工具（只读体检 + 调用现有生产入口）。

设计边界（重要）：
  * 本脚本**不新建控制逻辑、不发送任何游戏点击或 API**。所有可见 UI 动作
    一律由现有生产入口 ``tools/one_expedition_ui_loop.py`` 执行，视觉双帧
    点击门禁、0.97/0.04 指纹阈值、stateless 默认语义全部原样保留。
  * 本脚本只负责四件事：
      1. 启动前只读体检 + 双图层屏幕硬门禁（命中奖励页即 FAIL_CLOSED，不启动）；
      2. 把屏幕截图落盘，便于「看图定案」而不是只看分数；
      3. 按时间窗汇总 stdout 与 run-log 的 drain / 结算 / 补给 / 错误证据；
      4. 每步之后做只读终态复核（Poi、编成补给、账本）。
  * 失败纪律：不自动重试、不盲目点击、不写账本、不修改生产源码。

用法（在仓库根目录，Git Bash / PowerShell 均可）：
    python tools/manual_expedition_walkthrough.py check
    python tools/manual_expedition_walkthrough.py dispatch --authorize-ui-actions
    python tools/manual_expedition_walkthrough.py wait
    python tools/manual_expedition_walkthrough.py settle  --authorize-ui-actions
    python tools/manual_expedition_walkthrough.py cycle   --authorize-ui-actions

``check`` / ``wait`` 纯只读，不需要授权；``dispatch`` / ``settle`` / ``cycle``
必须显式带 ``--authorize-ui-actions``（除了生产入口自带的 required 开关之外，
本脚本再加一道，避免误跑）。``--dry-run`` 只打印将要执行的命令，不执行任何动作。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
LOGS = ROOT / "logs"
FINGERPRINT_DOC = SRC / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json"
PRODUCTION_ENTRY = TOOLS / "one_expedition_ui_loop.py"

# 与生产入口 REWARD_SCREEN_STATES 一致；门禁函数保持纯函数，不 import 重模块。
REWARD_STATES = ("HOME_PORT_RETURN_NOTICE", "RETURN_RESULT", "BONUS_ITEMS")

ERROR_PATTERNS = (
    "STOPPED_FAIL_CLOSED", "other_returns", "UNATTRIBUTED", "ui_stuck",
    "Traceback", "refused", "single_frame_probe_failed", "startup_settlement_refused",
)

KEY_STDOUT_EVENTS = (
    "stateless_mode_enabled", "global_drain_wired", "startup_reward_screen_detected",
    "production_supervisor_started", "fleet_monitor_transition",
    "PRODUCTION_UI_CONTROLLER_INTEGRATION_VERIFIED", "PRODUCTION_CLOSED_LOOP_ACTIONS",
    "supervisor_tick", "PRODUCTION_SUPERVISOR_PAUSED", "STOPPED_FAIL_CLOSED", "run_stopped",
)

DETAIL_RUNLOG_EVENTS = (
    "settlement_drain_started", "settlement_drain_action", "settlement_drain_page_changed",
    "settlement_drain_finished", "supervisor_settlement_drain", "fleet_monitor_transition",
    "continuous_ui_settlement_screen_absent", "dispatch_confirmed_by_poi", "supervisor_tick",
)


def _production_module():
    """延迟导入生产入口，保证纯函数可被离线测试直接导入。"""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import one_expedition_ui_loop as entry  # noqa: E402
    return entry


# --------------------------------------------------------------------------
# 只读状态读取
# --------------------------------------------------------------------------
def _poi_fleets() -> dict[int, list]:
    from kancollebot.adapters.poi.mcp_client import PoiMcpClient  # noqa: E402
    payload = json.loads(
        PoiMcpClient().call_read_tool("poi_get", {"path": "info.fleets"})["content"][0]["text"]
    )
    return {int(f["api_id"]): list(f.get("api_mission") or []) for f in payload["items"]}


def _fleet_supply(fleet_id: int) -> dict:
    from kancollebot.adapters.poi.cdp_fleet_state import read_poi_fleet_states  # noqa: E402
    from kancollebot.adapters.poi.cdp_websocket import connect_poi_session  # noqa: E402
    with connect_poi_session(timeout=3.0) as session:
        states = read_poi_fleet_states(session.transport)
    state = next((s for s in states if s.fleet_id == fleet_id), None)
    if state is None:
        return {"error": f"fleet {fleet_id} not in CDP snapshot"}
    members = [x for x in state.ship_ids if x > 0]
    return {
        "members": len(members),
        "levels": [x.level for x in state.supply],
        "all_full": bool(state.supply) and all(x.is_fully_supplied for x in state.supply),
        "supply": [
            {"ship_id": x.ship_id, "fuel": x.fuel, "max_fuel": x.max_fuel,
             "ammo": x.ammo, "max_ammo": x.max_ammo, "full": x.is_fully_supplied}
            for x in state.supply
        ],
    }


def _ledger(database: Path) -> list:
    if not database.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            rows = list(con.execute(
                "select dispatch_id, fleet_id, mission_id, status, settled_at "
                "from dispatch_identities order by rowid desc limit 6"
            ))
        finally:
            con.close()
        return [list(r) for r in rows]
    except Exception as exc:
        return [["<ledger read failed>", type(exc).__name__, str(exc), "", ""]]


def _capture_screen(stamp: str, tag: str, *, save: bool = True) -> dict:
    """双图层只读截图 + 分类。返回 layers / reward_probe / 保存的文件。"""
    entry = _production_module()
    from kancollebot.adapters.poi.visual_fingerprint import rank_screenshot_fingerprints  # noqa: E402
    doc = entry._read_visual_document(FINGERPRINT_DOC)
    result: dict = {"tag": tag, "captured_at_utc": datetime.now(timezone.utc).isoformat(), "layers": {}}
    with entry.connect_game_webview(timeout=3.0) as session:
        for layer, from_surface in (("composited", False), ("default", True)):
            payload = session.input.capture_screenshot(from_surface=from_surface)
            ranking = rank_screenshot_fingerprints(payload, doc)
            item = {
                "state": ranking.state,
                "best_name": ranking.best_name,
                "best_score": round(ranking.best_score, 4),
                "second_name": ranking.second_name,
                "second_score": round(ranking.second_score, 4),
                "accepted": ranking.accepted,
                "top3": {k: round(float(v), 4) for k, v in
                         sorted(ranking.scores.items(), key=lambda x: -x[1])[:3]},
            }
            if save:
                path = LOGS / f"manual_{stamp}_{tag}_{layer}.png"
                path.write_bytes(base64.b64decode(payload, validate=True))
                item["file"] = str(path)
            result["layers"][layer] = item
        result["reward_probe"] = entry._reward_screen_on_any_layer(session)
    return result


# --------------------------------------------------------------------------
# 纯函数（可离线测试）
# --------------------------------------------------------------------------
def _decide_gate(layers: dict, reward_probe: str | None,
                 reward_states=REWARD_STATES) -> tuple[str, list[str]]:
    """启动前屏幕硬门禁：GO 才允许调用生产入口，否则 FAIL_CLOSED。"""
    reasons: list[str] = []
    if "default" not in layers or "composited" not in layers:
        return "FAIL_CLOSED", ["missing capture layer(s)"]
    default, composited = layers["default"], layers["composited"]
    if reward_probe is not None:
        reasons.append(f"production two-layer reward probe = {reward_probe}")
    if default.get("accepted") and default.get("state") in reward_states:
        reasons.append(f"default layer is reward screen: {default.get('state')}")
    if composited.get("accepted") and composited.get("state") in reward_states:
        reasons.append(f"composited layer is reward screen: {composited.get('state')}")
    if default.get("state") != "HOME_PORT" or not default.get("accepted"):
        reasons.append(
            f"default layer is not an accepted HOME_PORT "
            f"(state={default.get('state')}, accepted={default.get('accepted')})"
        )
    if not composited.get("accepted") and composited.get("state") != "UNKNOWN":
        reasons.append(
            f"composited layer is neither accepted nor plain UNKNOWN "
            f"(state={composited.get('state')})"
        )
    return ("FAIL_CLOSED", reasons) if reasons else ("GO", [])


def _filter_rows(rows: list[dict], start_iso: str, run_id: str | None) -> list[dict]:
    """run-log 是按日期追加的单文件，必须按时间窗 + run_id 过滤，否则混入旧轮次。"""
    kept = []
    for row in rows:
        ts = str(row.get("timestamp_utc") or row.get("timestamp") or "")[:19]
        if start_iso and ts and ts < start_iso:
            continue
        if run_id and row.get("run_id") and row.get("run_id") != run_id:
            continue
        kept.append(row)
    return kept


# --------------------------------------------------------------------------
# 门禁与证据
# --------------------------------------------------------------------------
def _prelaunch_gate(stamp: str, action: str) -> tuple[str, dict]:
    LOGS.mkdir(parents=True, exist_ok=True)
    evidence: dict = {"action": action, "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        evidence["poi_fleets"] = {str(k): v for k, v in _poi_fleets().items()}
        screen = _capture_screen(stamp, f"{action}_gate", save=True)
        evidence["screen"] = screen
        decision, reasons = _decide_gate(screen["layers"], screen["reward_probe"])
        evidence["decision"], evidence["reasons"] = decision, reasons
    except Exception as exc:
        evidence["decision"] = "FAIL_CLOSED"
        evidence["reasons"] = [f"pre-launch read raised {type(exc).__name__}: {exc}"]
    path = LOGS / f"manual_{stamp}_{action}_gate.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence["evidence_file"] = str(path)

    print(f"[门禁] 结论 = {evidence['decision']}")
    for layer, item in (evidence.get("screen", {}).get("layers") or {}).items():
        print(f"  {layer:11s} {str(item.get('state')):26s} "
              f"best={item.get('best_name')}/{item.get('best_score')} "
              f"accepted={item.get('accepted')}"
              + (f"  -> {item.get('file')}" if item.get("file") else ""))
    print(f"  生产双图层奖励页探针 = {evidence.get('screen', {}).get('reward_probe')}")
    for reason in evidence["reasons"]:
        print(f"  原因: {reason}")
    print(f"  证据: {path}")
    return evidence["decision"], evidence


def _report_check(args, *, save_screens: bool = True) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print("=== 只读体检（不点击、不发游戏 API）===")
    try:
        fleets = _poi_fleets()
        for fleet_id, mission in sorted(fleets.items()):
            print(f"  Poi F{fleet_id}: {mission}")
        target = fleets.get(args.fleet_id, [])
        if len(target) >= 3 and target[0] == 1 and target[2]:
            moment = datetime.fromtimestamp(target[2] / 1000, timezone.utc)
            local = moment.astimezone(timezone(timedelta(hours=8)))
            remain = (moment - datetime.now(timezone.utc)).total_seconds()
            print(f"  F{args.fleet_id} 在途，远征 {target[1]}，返航 {local:%Y-%m-%d %H:%M:%S} GMT+8"
                  f"（剩余 {remain / 60:.1f} 分钟）")
    except Exception as exc:
        print(f"  Poi 读取失败: {type(exc).__name__}: {exc}")
    try:
        supply = _fleet_supply(args.fleet_id)
        if "error" in supply:
            print(f"  F{args.fleet_id} 编成/补给: {supply['error']}")
        else:
            print(f"  F{args.fleet_id} 编成 {supply['members']} 舰，等级 {supply['levels']}，"
                  f"满补 = {supply['all_full']}")
            if not supply["all_full"]:
                print("  ⚠ 未满补：派遣前的编成/补给门禁可能拒绝，或由结算轮的补给步骤补齐；"
                      "脚本不代点、不自动补给")
    except Exception as exc:
        print(f"  编成/补给读取失败: {type(exc).__name__}: {exc}")
    try:
        screen = _capture_screen(stamp, "check", save=save_screens)
        for layer, item in screen["layers"].items():
            print(f"  屏幕 {layer:11s} {str(item['state']):26s} "
                  f"best={item['best_name']}/{item['best_score']:.4f} "
                  f"second={item['second_name']}/{item['second_score']:.4f} accepted={item['accepted']}"
                  + (f"  -> {item.get('file')}" if item.get("file") else ""))
        print(f"  生产双图层奖励页探针 = {screen['reward_probe']}")
        decision, reasons = _decide_gate(screen["layers"], screen["reward_probe"])
        print(f"  若现在启动主循环，门禁结论 = {decision}")
        for reason in reasons:
            print(f"    - {reason}")
    except Exception as exc:
        print(f"  屏幕读取失败: {type(exc).__name__}: {exc}")
    print(f"  账本（{args.database}，最近 6 条，只读）:")
    for row in _ledger(args.database) or ["<空 / 库不存在>"]:
        print(f"    {row}")


# --------------------------------------------------------------------------
# 生产入口调用与取证
# --------------------------------------------------------------------------
def _build_production_argv(args, log_path: Path) -> list[str]:
    argv = [
        sys.executable, str(PRODUCTION_ENTRY),
        "--fleet-id", str(args.fleet_id),
        "--mission-id", str(args.mission_id),
        "--authorize-ui-actions",
        "--database", str(args.database),
        "--run-log-dir", str(args.run_log_dir),
        "--global-drain",
        "--drain-action-interval-seconds", str(args.drain_interval),
        "--drain-timeout-seconds", str(args.drain_timeout),
        "--drain-max-actions", str(args.drain_max_actions),
        "--log-path", str(log_path),
    ]
    if args.strict:
        argv.append("--strict")
    return argv


def _run_production(args, action: str, stamp: str) -> dict:
    LOGS.mkdir(parents=True, exist_ok=True)
    args.run_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"manual_{stamp}_{action}.production.log"
    argv = _build_production_argv(args, log_path)
    print(f"=== 调用现有生产入口（{action}）===")
    print("  " + " ".join(argv[1:]))
    if args.dry_run:
        print("  --dry-run: 未执行任何动作")
        return {"returncode": None, "dry_run": True}

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    start_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[dict] = []
    proc = subprocess.Popen(
        argv, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            row = json.loads(line)
        except Exception:
            if line.strip():
                print("  " + line.strip()[:300])
            continue
        rows.append(row)
        if row.get("event") in KEY_STDOUT_EVENTS:
            keep = {k: v for k, v in row.items()
                    if k not in ("timestamp_utc", "run_id", "task_kind", "event", "scores")}
            print(f"  [{row.get('timestamp_utc', '')[11:19]}] {row['event']} "
                  f"{json.dumps(keep, ensure_ascii=False)[:360]}")
    returncode = proc.wait()
    print(f"  生产入口 EXIT = {returncode}   主日志 = {log_path}")

    summary = _summarize(args, rows, start_iso, action, log_path)
    # A paused supervisor can still emit INTEGRATION_VERIFIED in an earlier
    # tick, so the process exit code is the final authority on success.
    summary["ok"] = bool(summary.get("ok")) and returncode == 0
    summary.update({"returncode": returncode, "log_path": log_path, "dry_run": False})
    return summary


def _summarize(args, rows: list[dict], start_iso: str, action: str, log_path: Path) -> dict:
    run_ids = {r.get("run_id") for r in rows if r.get("run_id")}
    run_id = next(iter(run_ids), None)
    raw = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    print("  --- 主日志错误扫描 ---")
    for pattern in ERROR_PATTERNS:
        count = raw.count(pattern)
        if count:
            print(f"    {pattern}: {count}")

    runlog_rows: list[dict] = []
    for file in sorted(args.run_log_dir.glob("*.jsonl")):
        for line in file.read_text(encoding="utf-8").splitlines():
            try:
                runlog_rows.append(json.loads(line))
            except Exception:
                continue
    window = _filter_rows(runlog_rows, start_iso, run_id)
    print(f"  --- run-log 本轮事件（时间窗 {start_iso} 起，共 {len(window)} 行）---")
    drain_actions = 0
    finished: dict = {}
    for row in window:
        event = row.get("event") or row.get("type")
        if event == "settlement_drain_action":
            drain_actions += 1
        if event == "settlement_drain_finished":
            finished = row
        if event in DETAIL_RUNLOG_EVENTS:
            keep = {k: v for k, v in row.items()
                    if k not in ("timestamp_utc", "timestamp", "run_id", "task_kind",
                                 "event", "type", "scores", "event_id")}
            ts = str(row.get("timestamp_utc") or row.get("timestamp") or "")
            print(f"    [{ts[11:19]}] {event} {json.dumps(keep, ensure_ascii=False)[:330]}")
    print(f"  drain 动作数 = {drain_actions} / {args.drain_max_actions}；"
          f"收口 = reached_home:{finished.get('reached_home')} "
          f"completed_pages:{finished.get('completed_pages')} "
          f"error:{finished.get('error')}")

    verified = any(r.get("event") == "PRODUCTION_UI_CONTROLLER_INTEGRATION_VERIFIED" for r in rows)
    paused = [r for r in rows if r.get("event") == "PRODUCTION_SUPERVISOR_PAUSED"]
    print(f"  INTEGRATION_VERIFIED = {verified}")
    if paused:
        print(f"  PAUSED reason = {paused[-1].get('reason')}")
    print("  --- 终态只读复核 ---")
    try:
        for fleet_id, mission in sorted(_poi_fleets().items()):
            print(f"    Poi F{fleet_id}: {mission}")
    except Exception as exc:
        print(f"    Poi 读取失败 {type(exc).__name__}: {exc}")
    try:
        supply = _fleet_supply(args.fleet_id)
        print(f"    F{args.fleet_id} 满补 = {supply.get('all_full', supply.get('error'))}")
    except Exception as exc:
        print(f"    补给读取失败 {type(exc).__name__}: {exc}")
    for row in _ledger(args.database):
        print(f"    账本 {row}")

    if returncode_hint := finished.get("error"):
        print(f"  !! drain 未走完：{returncode_hint}；证据见 {log_path} 与 {args.run_log_dir}")
        print("  !! 按纪律不自动重试；若屏幕仍挂结算页，请手动点完再重新执行 settle/check")
    ok = (any(r.get("event") == "run_stopped" for r in rows) and verified) or verified
    return {"verified": verified, "drain_actions": drain_actions,
            "drain_finished": finished, "ok": ok}


def _wait_for_return(args) -> bool:
    print("=== 只读等返航（不点击、不触发 UI 导航）===")
    try:
        fleets = _poi_fleets()
    except Exception as exc:
        print(f"  Poi 读取失败: {type(exc).__name__}: {exc}")
        return False
    mission = fleets.get(args.fleet_id, [])
    if len(mission) < 3 or mission[0] != 1 or not mission[2]:
        print(f"  F{args.fleet_id} 当前不在途（{mission}），无需等待")
        return False
    target_ms = int(mission[2])
    moment = datetime.fromtimestamp(target_ms / 1000, timezone.utc)
    local = moment.astimezone(timezone(timedelta(hours=8)))
    deadline = target_ms / 1000 + args.buffer_seconds
    print(f"  远征 {mission[1]}，返航 {local:%Y-%m-%d %H:%M:%S} GMT+8；"
          f"再留 {args.buffer_seconds}s 缓冲，目标启动时刻 "
          f"{(moment + timedelta(seconds=args.buffer_seconds)).astimezone(timezone(timedelta(hours=8))):%H:%M:%S}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    while True:
        remain = deadline - time.time()
        if remain <= 0:
            break
        try:
            fleets = _poi_fleets()
            print(f"  剩余 {remain / 60:.1f} 分钟 | F{args.fleet_id} = {fleets.get(args.fleet_id)}")
        except Exception as exc:
            print(f"  剩余 {remain / 60:.1f} 分钟 | Poi 读取失败 {type(exc).__name__}")
        time.sleep(min(args.poll_seconds, max(remain, 0)))
    try:
        screen = _capture_screen(stamp, "after_return", save=True)
        for layer, item in screen["layers"].items():
            print(f"  返航后 {layer:11s} {str(item['state']):26s} "
                  f"best={item['best_name']}/{item['best_score']:.4f}"
                  + (f"  -> {item.get('file')}" if item.get("file") else ""))
        print(f"  生产双图层奖励页探针 = {screen['reward_probe']}")
    except Exception as exc:
        print(f"  返航后截图失败: {type(exc).__name__}: {exc}")
    print("  注意：Poi 的状态推进由随后 settle 的可见 UI 导航完成，等待阶段不点击")
    return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fleet-id", type=int, default=2)
    parser.add_argument("--mission-id", type=int, default=1)
    parser.add_argument("--database", type=Path, default=LOGS / "manual_walkthrough.sqlite3")
    parser.add_argument("--run-log-dir", type=Path, default=LOGS / "run_log_manual")
    parser.add_argument("--drain-interval", type=float, default=10.0,
                        help="传给 --drain-action-interval-seconds")
    parser.add_argument("--drain-timeout", type=float, default=240.0,
                        help="传给 --drain-timeout-seconds")
    parser.add_argument("--drain-max-actions", type=int, default=24)
    parser.add_argument("--strict", action="store_true",
                        help="透传 --strict，恢复历史严格账本模式（默认 stateless）")
    parser.add_argument("--buffer-seconds", type=float, default=120.0,
                        help="wait: 返航后再等多少秒才允许 settle")
    parser.add_argument("--poll-seconds", type=float, default=30.0,
                        help="wait: 只读轮询间隔")
    parser.add_argument("--authorize-ui-actions", action="store_true",
                        help="dispatch/settle/cycle 必需；check/wait 不需要")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印生产入口命令，不执行任何动作")
    parser.add_argument("--no-screens", action="store_true",
                        help="check 不落盘截图（门禁截图仍会保存）")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(
        description="手动远征全流程：只读体检 + 调现有生产入口 + 截图与证据汇总",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "典型顺序：check -> dispatch --authorize-ui-actions -> wait -> "
            "check（看图确认真的是结算页/母港） -> settle --authorize-ui-actions -> check\n"
            "也可用 cycle --authorize-ui-actions 一次跑完一轮。"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("check", "只读体检：Poi / 编成补给 / 双帧屏幕 / 账本 / 返航倒计时 + 截图"),
        ("dispatch", "启动前门禁通过后，调生产入口派遣一次（单 tick）"),
        ("wait", "只读等返航 + 缓冲，期间不点击；返航后截图"),
        ("settle", "启动前门禁通过后，调生产入口完成 drain→结算→补给→再派遣"),
        ("cycle", "check→dispatch→wait→settle→check 跑完整一轮；失败即停，不重试"),
    ):
        _add_common(sub.add_parser(name, help=help_text))
    args = parser.parse_args(argv)
    args.database = args.database.expanduser().resolve()
    args.run_log_dir = args.run_log_dir.expanduser().resolve()
    LOGS.mkdir(parents=True, exist_ok=True)

    needs_authorization = args.command in ("dispatch", "settle", "cycle")
    if needs_authorization and not args.authorize_ui_actions and not args.dry_run:
        print("拒绝执行：dispatch/settle/cycle 必须显式带 --authorize-ui-actions")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if args.command == "check":
        _report_check(args, save_screens=not args.no_screens)
        return 0
    if args.command == "wait":
        _wait_for_return(args)
        return 0

    if args.command == "cycle":
        _report_check(args, save_screens=True)
    decision, evidence = _prelaunch_gate(stamp, args.command)
    if decision != "GO":
        print("=== FAIL_CLOSED：启动前屏幕门禁未放行，未启动主循环，零点击、零游戏 API ===")
        print(f"证据：{evidence['evidence_file']}")
        print("请手动点完残留页面链到纯 HOME_PORT 后再执行 check / 重试")
        return 2

    if args.command in ("dispatch", "settle"):
        summary = _run_production(args, args.command, stamp)
        if args.dry_run:
            return 0
        _report_check(args, save_screens=True)
        return 0 if summary.get("ok") else 2

    # cycle
    first = _run_production(args, "dispatch", stamp)
    if not args.dry_run and not first.get("ok"):
        print("cycle 停止：派遣未成功，不重试")
        return 2
    _wait_for_return(args)
    second_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    decision, evidence = _prelaunch_gate(second_stamp, "settle")
    if decision != "GO":
        print("=== FAIL_CLOSED：返航后屏幕门禁未放行，未执行结算，零点击 ===")
        print(f"证据：{evidence['evidence_file']}")
        return 2
    second = _run_production(args, "settle", second_stamp)
    if args.dry_run:
        return 0
    _report_check(args, save_screens=True)
    return 0 if second.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
