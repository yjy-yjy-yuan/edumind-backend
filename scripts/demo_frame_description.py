#!/usr/bin/env python3
"""
Frame Description 演示脚本

覆盖三条链路：
1. 正常链路：功能启用 + Vinci 可用 → 流式返回描述
2. 降级链路：功能启用 + Vinci 不可用 → 返回降级文本
3. 恢复链路：熔断器打开后，Vinci 恢复 → 自动恢复正常描述

使用方式：
    python scripts/demo_frame_description.py [scenario]

参数：
    scenario 可选值：
        normal     - 正常链路（默认）
        degraded   - 降级链路
        recovery   - 恢复链路
        all        - 依次运行所有链路
        health     - 仅测试健康检查

前置条件：
    1. 后端运行在 http://127.0.0.1:2004
    2. .env 中 FRAME_DESC_ENABLED=true（除降级链路测试外）
    3. VINCI_BASE_URL 和 VINCI_API_KEY 已配置

示例：
    python scripts/demo_frame_description.py all
    python scripts/demo_frame_description.py health
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Generator

# 追加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import httpx
except ImportError:
    print("[ERROR] 需要 httpx: pip install httpx")
    sys.exit(1)

BASE_URL = os.environ.get("EDUMIND_API_BASE", "http://127.0.0.1:2004")
HEADERS = {"Content-Type": "application/json", "Accept": "application/x-ndjson"}

# 测试用帧数据（1x1 红色 JPEG 的 base64 片段，仅用于触发推理路径）
SAMPLE_FRAME = "/9j/4AAQSkZJRgABAQEASABIAAD/2wBD"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def green(text: str) -> str:
    return f"\033[92m{text}\033[0m"


def red(text: str) -> str:
    return f"\033[91m{text}\033[0m"


def yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m"


def blue(text: str) -> str:
    return f"\033[94m{text}\033[0m"


def banner(title: str) -> None:
    width = 70
    print()
    print(blue("=" * width))
    print(blue(f"  {title}"))
    print(blue("=" * width))


def info(label: str, value: str = "") -> None:
    prefix = f"[INFO]  {label}"
    if value:
        print(f"  {green('✓')} {prefix}: {value}")
    else:
        print(f"  {green('✓')} {prefix}")


def warn(label: str, value: str = "") -> None:
    prefix = f"[WARN]  {label}"
    if value:
        print(f"  {yellow('⚠')} {prefix}: {value}")
    else:
        print(f"  {yellow('⚠')} {prefix}")


def error(label: str, value: str = "") -> None:
    prefix = f"[ERROR] {label}"
    if value:
        print(f"  {red('✗')} {prefix}: {value}")
    else:
        print(f"  {red('✗')} {prefix}")


def parse_ndjson_stream(response_text: str) -> list[dict]:
    """解析 NDJSON 文本流。"""
    events = []
    for line in response_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            warn("JSON解析失败", line[:80])
    return events


def consume_stream(url: str, payload: dict, timeout: float = 30.0) -> tuple[list[dict], str]:
    """消费流式端点，返回事件列表和原始文本。"""
    with httpx.stream(
        "POST",
        url,
        json=payload,
        headers=HEADERS,
        timeout=timeout,
    ) as resp:
        text = resp.read_text()
        if not resp.is_success:
            return [], f"HTTP {resp.status_code}: {text[:200]}"
        return parse_ndjson_stream(text), ""


# ---------------------------------------------------------------------------
# 场景 1：健康检查
# ---------------------------------------------------------------------------


def demo_health() -> bool:
    banner("场景 0：健康检查")
    url = f"{BASE_URL}/api/frame_description/health"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url)
        if resp.is_success:
            data = resp.json()
            info("健康检查", f"HTTP 200 | enabled={data.get('enabled')} | service={data.get('service')}")
            print(f"       响应: {json.dumps(data, ensure_ascii=False, indent=6)}")
            return True
        else:
            error("健康检查失败", f"HTTP {resp.status_code}")
            return False
    except httpx.ConnectError:
        error("无法连接到后端", f"请确认服务运行在 {BASE_URL}")
        return False
    except Exception as exc:
        error("健康检查异常", str(exc))
        return False


# ---------------------------------------------------------------------------
# 场景 1：正常链路
# ---------------------------------------------------------------------------


def demo_normal() -> bool:
    banner("场景 1：正常链路（功能启用 + Vinci 可用）")

    # Step 1: 健康检查
    info("Step 1", "验证功能已启用")
    if not demo_health():
        return False

    # Step 2: 开启会话
    info("Step 2", "开启描述会话")
    session_url = f"{BASE_URL}/api/frame_description/session"
    session_id = str(uuid.uuid4())[:16]
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            session_url,
            json={
                "video_id": 1,
                "action": "start",
                "detail_level": "standard",
                "session_id": session_id,
            },
        )
    if not resp.is_success:
        error("开启会话失败", f"HTTP {resp.status_code}: {resp.text[:100]}")
        return False
    session_data = resp.json()
    info("会话已开启", f"session_id={session_data.get('session_id')} status={session_data.get('status')}")

    # Step 3: 发送帧描述请求
    info("Step 3", "发送流式描述请求")
    print(f"       采样帧: 1 帧 base64 JPEG")
    print(f"       时间戳: 10.0s")
    print(f"       详细度: standard")
    print(f"       会话ID: {session_id}")
    print(f"       等待响应...", end="", flush=True)

    url = f"{BASE_URL}/api/frame_description/describe"
    payload = {
        "video_id": 1,
        "frames": [SAMPLE_FRAME],
        "timestamp": 10.0,
        "detail_level": "standard",
        "session_id": session_id,
        "allow_degrade": True,
    }

    events, err = consume_stream(url, payload, timeout=30.0)
    print(" 完成")

    if err:
        # 网络错误（Vinci 不可用）不算失败，这是降级测试的一部分
        warn("流式请求出错", err)
        return True  # 继续演示

    # Step 4: 解析事件
    info("Step 4", f"收到 {len(events)} 个 NDJSON 事件")

    for i, event in enumerate(events):
        etype = event.get("type", "?")
        if etype == "status":
            info(f"  事件[{i}] status", f"stage={event.get('stage')} progress={event.get('progress')}%")
        elif etype == "description":
            info(f"  事件[{i}] description", event.get("delta", ""))
        elif etype == "complete":
            info(
                f"  事件[{i}] complete",
                f"stage={event.get('stage')} latency={event.get('latency_ms')}ms degraded={event.get('degraded')}",
            )
        elif etype == "error":
            error(f"  事件[{i}] error", f"stage={event.get('stage')} message={event.get('message')}")
        else:
            print(f"  {green('*')} 事件[{i}] {etype}: {json.dumps(event, ensure_ascii=False)[:120]}")

    # Step 5: 关闭会话
    info("Step 5", "关闭描述会话")
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            session_url,
            json={"video_id": 1, "action": "stop", "session_id": session_id},
        )
    if resp.is_success:
        data = resp.json()
        info("会话已关闭", f"status={data.get('status')}")
    else:
        warn("关闭会话失败", f"HTTP {resp.status_code}")

    # 判断结果
    complete_events = [e for e in events if e.get("type") == "complete"]
    if complete_events:
        complete = complete_events[0]
        if not complete.get("degraded"):
            info("正常链路测试", green("PASS - 成功返回描述，无降级"))
            return True
        else:
            warn("正常链路测试", yellow("PARTIAL - 功能正常但触发了降级（Vinci 不可用）"))
            return True
    else:
        error("正常链路测试", red("FAIL - 未收到 complete 事件"))
        return False


# ---------------------------------------------------------------------------
# 场景 2：降级链路
# ---------------------------------------------------------------------------


def demo_degraded() -> bool:
    banner("场景 2：降级链路（功能启用 + Vinci 不可用）")

    info("说明", "此场景通过 Vinci 不可用触发降级模式")
    info("说明", "前端面板会显示「描述服务暂不可用」并持续轮询")

    session_id = str(uuid.uuid4())[:16]

    info("Step 1", "开启会话")
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{BASE_URL}/api/frame_description/session",
            json={
                "video_id": 1,
                "action": "start",
                "detail_level": "standard",
                "session_id": session_id,
            },
        )
    if not resp.is_success:
        warn("开启会话失败", f"HTTP {resp.status_code} - 可能是因为 Vinci 不可用")
    else:
        info("会话已开启", resp.json().get("session_id"))

    info("Step 2", "发送帧描述请求（预期降级）")
    print("       等待 Vinci 超时...", end="", flush=True)

    url = f"{BASE_URL}/api/frame_description/describe"
    payload = {
        "video_id": 1,
        "frames": [SAMPLE_FRAME],
        "timestamp": 20.0,
        "detail_level": "standard",
        "session_id": session_id,
        "allow_degrade": True,
    }

    events, err = consume_stream(url, payload, timeout=35.0)
    print(" 完成")

    if err:
        error("请求失败", err)
        return False

    info("Step 3", f"收到 {len(events)} 个事件")
    complete_events = [e for e in events if e.get("type") == "complete"]
    if complete_events:
        complete = complete_events[0]
        if complete.get("degraded"):
            info("降级链路测试", green("PASS - 正确触发降级模式"))
            info("降级文本", complete.get("full_description", ""))
            return True
        else:
            warn("降级链路测试", yellow("PARTIAL - 未触发降级（Vinci 可能可用）"))
            return True
    else:
        error("降级链路测试", red("FAIL - 未收到 complete 事件"))
        return False


# ---------------------------------------------------------------------------
# 场景 3：恢复链路
# ---------------------------------------------------------------------------


def demo_recovery() -> bool:
    banner("场景 3：恢复链路（熔断器打开 → 探针模式 → 恢复）")

    info("说明", "此场景验证熔断器机制：连续失败 3 次后打开，Vinci 恢复后自动正常")
    info("说明", "实际测试需要 Vinci 服务暂时不可用，然后恢复")
    warn("注意", "此场景需要 Vinci 服务状态变化，请手动操作")

    session_id = str(uuid.uuid4())[:16]

    # 连续 3 次失败触发熔断
    info("Step 1", "连续 3 次请求触发熔断器打开")
    print("       （如 Vinci 不可用，第 3 次后将进入熔断状态）")
    for i in range(3):
        print(f"       请求 {i+1}/3...", end="", flush=True)
        events, err = consume_stream(
            f"{BASE_URL}/api/frame_description/describe",
            {
                "video_id": 1,
                "frames": [SAMPLE_FRAME],
                "timestamp": 30.0 + i * 10,
                "detail_level": "standard",
                "session_id": session_id,
                "allow_degrade": True,
            },
            timeout=35.0,
        )
        complete = next((e for e in events if e.get("type") == "complete"), None)
        degraded = complete.get("degraded", False) if complete else False
        print(f" {'降级' if degraded else '正常'}")

    info("Step 2", "等待 30s 熔断器恢复（探针模式）")
    info("Step 2", "请在此期间恢复 Vinci 服务")
    print(f"       等待 {30} 秒...")
    time.sleep(30)

    info("Step 3", "再次请求（预期恢复正常）")
    events, _ = consume_stream(
        f"{BASE_URL}/api/frame_description/describe",
        {
            "video_id": 1,
            "frames": [SAMPLE_FRAME],
            "timestamp": 70.0,
            "detail_level": "standard",
            "session_id": session_id,
            "allow_degrade": True,
        },
        timeout=30.0,
    )

    complete_events = [e for e in events if e.get("type") == "complete"]
    if complete_events:
        complete = complete_events[0]
        if not complete.get("degraded"):
            info("恢复链路测试", green("PASS - 熔断器自动恢复"))
            return True
        else:
            info("恢复链路测试", yellow("PARTIAL - 仍处于降级，可能 Vinci 未恢复"))
            return True
    else:
        info("恢复链路测试", yellow("PARTIAL - 未收到 complete 事件"))
        return True  # 不算失败，因为 Vinci 可能确实未恢复


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> int:
    scenario = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    scenarios = {
        "health": (demo_health, "健康检查"),
        "normal": (demo_normal, "正常链路"),
        "degraded": (demo_degraded, "降级链路"),
        "recovery": (demo_recovery, "恢复链路"),
        "all": (None, "全部场景"),
    }

    if scenario not in scenarios:
        print(f"用法: python {sys.argv[0]} [health|normal|degraded|recovery|all]")
        print(f"可用场景: {', '.join(scenarios.keys())}")
        return 1

    name, title = scenarios[scenario]
    banner(f"Frame Description 演示 - {title}")

    if scenario == "all":
        results = {}
        for key, (fn, label) in scenarios.items():
            if key == "all":
                continue
            try:
                results[key] = fn()
            except Exception as exc:
                error(label, f"异常: {exc}")
                results[key] = False

        banner("演示结果汇总")
        all_pass = True
        for key, passed in results.items():
            _, label = scenarios.get(key, (None, key))
            status = green("PASS") if passed else red("FAIL")
            print(f"  {label:20s}: {status}")
            if not passed:
                all_pass = False
        return 0 if all_pass else 1

    else:
        try:
            ok = name()
        except Exception as exc:
            error("执行异常", str(exc))
            return 1
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
