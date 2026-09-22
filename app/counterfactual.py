"""反事实审计：把全部航点高度统一平移后重跑相同审计，并与原始结果做差集。

匹配键固定为 (zone_id, entry_time, exit_time)：
  - 两组结果中键相同的违规视为“同一条”，不出现在 cleared / introduced 中；
  - 仅出现在 baseline 中的键计入 cleared（平移后消除）；
  - 仅出现在 shifted 中的键计入 introduced（平移后新增）。
键相同而 kind 等其它字段不同的情况仍按同一条处理（键不区分 kind）。

所有列表均按 (entry_time, zone_id, exit_time) 稳定排序，因此相同输入
（含同样的列表顺序）必然产生逐字节稳定的输出。平移只作用于审计内部
使用的航点副本，不修改请求对象。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import List, Tuple

from .audit import audit_track
from .models import (
    CounterfactualResponse,
    Violation,
    ViolationSummary,
    Waypoint,
    Zone,
)

# 稳定排序键 / 匹配键：时间优先，其次区域与离开时间
SummaryKey = Tuple[str, datetime, datetime]


def shift_waypoint_altitudes(
    waypoints: List[Waypoint], offset: float
) -> List[Waypoint]:
    """返回高度统一加 offset 的航点副本列表；不修改入参的任何 Waypoint。"""
    return [w.model_copy(update={"alt": w.alt + offset}) for w in waypoints]


def violation_to_summary(v: Violation) -> ViolationSummary:
    return ViolationSummary(
        zone_id=v.zone_id,
        kind=v.kind,
        entry_time=v.entry_time,
        exit_time=v.exit_time,
        segment_start=v.segment_start,
        segment_end=v.segment_end,
    )


def _stable_sorted(summaries: List[ViolationSummary]) -> List[ViolationSummary]:
    """按 (entry_time, zone_id, exit_time) 稳定升序。"""
    return sorted(summaries, key=lambda s: (s.entry_time, s.zone_id, s.exit_time))


def _key(s: ViolationSummary) -> SummaryKey:
    return (s.zone_id, s.entry_time, s.exit_time)


def _diff(
    left: List[ViolationSummary], right: List[ViolationSummary]
) -> List[ViolationSummary]:
    """left 相对于 right 的多重集差集，保留 left 的稳定顺序（含重复键的重数）。"""
    remaining: Counter[SummaryKey] = Counter(_key(s) for s in right)
    out: List[ViolationSummary] = []
    for s in left:  # left 已稳定排序；保持该顺序
        k = _key(s)
        if remaining[k] > 0:
            remaining[k] -= 1
        else:
            out.append(s)
    return out


def run_counterfactual(
    waypoints: List[Waypoint],
    zones: List[Zone],
    altitude_offset: float,
) -> CounterfactualResponse:
    # 原始审计必须在平移之前完成；即便 audit_track 不修改入参，
    # shifted 也只使用副本，从根源上保证请求对象不被改变。
    baseline_violations = audit_track(waypoints, zones)
    shifted_waypoints = shift_waypoint_altitudes(waypoints, altitude_offset)
    shifted_violations = audit_track(shifted_waypoints, zones)

    baseline = _stable_sorted([violation_to_summary(v) for v in baseline_violations])
    shifted = _stable_sorted([violation_to_summary(v) for v in shifted_violations])
    cleared = _diff(baseline, shifted)
    introduced = _diff(shifted, baseline)

    return CounterfactualResponse(
        altitude_offset=altitude_offset,
        baseline=baseline,
        shifted=shifted,
        cleared=cleared,
        introduced=introduced,
        baseline_count=len(baseline),
        shifted_count=len(shifted),
        cleared_count=len(cleared),
        introduced_count=len(introduced),
    )
