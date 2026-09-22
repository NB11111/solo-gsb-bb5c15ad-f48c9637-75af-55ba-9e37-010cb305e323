"""POST /audit/counterfactual 的端到端测试与核心逻辑单元测试。

与 test_audit.py 使用同一禁飞区：平面 [0,10]x[0,10]，时段 10:00-11:00，高度 0-100。
基准场景：航点 (-10,5)->(20,5)，30 分钟匀速，平飞高度 50，
区内区间固定为 10:10 进入、10:20 离开。
"""
from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from app.counterfactual import run_counterfactual, shift_waypoint_altitudes
from app.main import app
from app.models import AuditRequest

client = TestClient(app)

ZONE = {
    "id": "nfz-1",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
    },
    "active_from": "2026-09-22T10:00:00Z",
    "active_to": "2026-09-22T11:00:00Z",
    "min_alt": 0,
    "max_alt": 100,
}


def wp(lon, lat, alt, time):
    return {"lon": lon, "lat": lat, "alt": alt, "time": time}


LEVEL_CROSSING = [
    wp(-10, 5, 50, "2026-09-22T10:00:00Z"),
    wp(20, 5, 50, "2026-09-22T10:30:00Z"),
]
ENTRY_KEY = ("nfz-1", "2026-09-22T10:10:00Z", "2026-09-22T10:20:00Z")


def counterfactual(waypoints, zones, altitude_offset):
    payload = {
        "waypoints": waypoints,
        "zones": zones,
        "altitude_offset": altitude_offset,
    }
    return client.post("/audit/counterfactual", json=payload)


def cf_default(waypoints, altitude_offset):
    return counterfactual(waypoints, [copy.deepcopy(ZONE)], altitude_offset)


def summary_key(s):
    return s["zone_id"], s["entry_time"], s["exit_time"]


# ---------------------------------------------------------------- 消除 / 新增

def test_positive_shift_clears_violation():
    """整体抬升 100 米（50 -> 150）超出高度上限：baseline 的违规被消除。"""
    r = cf_default(LEVEL_CROSSING, 100.0)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["baseline_count"] == 1
    assert body["shifted_count"] == 0
    assert body["cleared_count"] == 1
    assert body["introduced_count"] == 0

    assert body["shifted"] == []
    assert body["introduced"] == []
    cleared = body["cleared"]
    assert len(cleared) == 1
    assert summary_key(cleared[0]) == ENTRY_KEY
    assert cleared[0]["kind"] == "entry"
    assert cleared[0]["segment_start"] == 0
    assert cleared[0]["segment_end"] == 0
    # baseline 与 cleared 中是同一条摘要
    assert summary_key(body["baseline"][0]) == summary_key(cleared[0])
    assert body["altitude_offset"] == 100.0


def test_negative_shift_introduces_violation():
    """原航迹在 150 米无违规，下降 100 米后进入高度窗口：新增违规。"""
    waypoints = [
        wp(-10, 5, 150, "2026-09-22T10:00:00Z"),
        wp(20, 5, 150, "2026-09-22T10:30:00Z"),
    ]
    r = cf_default(waypoints, -100.0)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["baseline_count"] == 0
    assert body["shifted_count"] == 1
    assert body["cleared_count"] == 0
    assert body["introduced_count"] == 1
    assert body["baseline"] == []
    assert body["cleared"] == []
    introduced = body["introduced"]
    assert summary_key(introduced[0]) == ENTRY_KEY
    assert summary_key(body["shifted"][0]) == ENTRY_KEY
    assert body["altitude_offset"] == -100.0


def test_small_shift_preserves_matching_key():
    """平移后仍违规且进入/离开时刻不变：同一键匹配，既不清除也不新增。"""
    r = cf_default(LEVEL_CROSSING, 10.0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["baseline_count"] == body["shifted_count"] == 1
    assert body["cleared"] == []
    assert body["introduced"] == []
    assert body["cleared_count"] == body["introduced_count"] == 0
    assert summary_key(body["baseline"][0]) == summary_key(body["shifted"][0]) == ENTRY_KEY
    # kind 等其余字段也保持一致
    assert body["baseline"][0]["kind"] == body["shifted"][0]["kind"] == "entry"


def test_zero_offset_runs_same_audit():
    """offset=0：shifted 与 baseline 完全一致，无差异。"""
    r = cf_default(LEVEL_CROSSING, 0.0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["baseline"] == body["shifted"]
    assert body["cleared"] == []
    assert body["introduced"] == []
    assert body["baseline_count"] == body["shifted_count"] == 1


def test_climb_shift_changes_exit_time_counts_as_cleared_and_introduced():
    """爬升航段：平移改变高度窗口交点 -> exit_time 变化 -> 旧键清除、新键新增。

    原始 alt 0->200：平面 10:10-10:20，高度达 100 在 10:15，
    故 baseline 键为 (nfz-1, 10:10, 10:15)。
    抬升 20 米后 alt 20->220，爬升率 200 米/30 分，高度达 100 在 10:12
    （平面离开点 10:20 处高度已达 153.3，故由高度窗口截断），
    shifted 键为 (nfz-1, 10:10, 10:12)。
    entry_time 相同但 exit_time 不同，按三元组应视为不同违规。
    """
    waypoints = [
        wp(-10, 5, 0, "2026-09-22T10:00:00Z"),
        wp(20, 5, 200, "2026-09-22T10:30:00Z"),
    ]
    r = cf_default(waypoints, 20.0)
    assert r.status_code == 200, r.text
    body = r.json()

    assert summary_key(body["baseline"][0]) == (
        "nfz-1", "2026-09-22T10:10:00Z", "2026-09-22T10:15:00Z",
    )
    assert summary_key(body["shifted"][0]) == (
        "nfz-1", "2026-09-22T10:10:00Z", "2026-09-22T10:12:00Z",
    )
    assert [summary_key(s) for s in body["cleared"]] == [
        ("nfz-1", "2026-09-22T10:10:00Z", "2026-09-22T10:15:00Z")
    ]
    assert [summary_key(s) for s in body["introduced"]] == [
        ("nfz-1", "2026-09-22T10:10:00Z", "2026-09-22T10:12:00Z")
    ]


# ---------------------------------------------------------------- 稳定排序

def test_groups_stable_sorted_by_entry_time_across_zones():
    """多个区域的清除/新增摘要按 entry_time（再按 zone_id）稳定升序。"""
    zone2 = copy.deepcopy(ZONE)
    zone2["id"] = "nfz-2"
    zone2["geometry"] = {
        "type": "Polygon",
        "coordinates": [[[20, 0], [30, 0], [30, 10], [20, 10], [20, 0]]],
    }
    waypoints = [
        wp(-10, 5, 50, "2026-09-22T10:00:00Z"),
        wp(40, 5, 50, "2026-09-22T10:50:00Z"),
    ]
    # nfz-1: 10:10-10:20；nfz-2: 10:30-10:40。抬升后两条均清除。
    r = counterfactual(waypoints, [zone2, copy.deepcopy(ZONE)], 100.0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cleared_count"] == 2
    assert [s["zone_id"] for s in body["cleared"]] == ["nfz-1", "nfz-2"]
    assert [s["entry_time"] for s in body["cleared"]] == [
        "2026-09-22T10:10:00Z", "2026-09-22T10:30:00Z",
    ]

    # 下降则两条均新增，顺序相同
    waypoints_high = [
        wp(-10, 5, 150, "2026-09-22T10:00:00Z"),
        wp(40, 5, 150, "2026-09-22T10:50:00Z"),
    ]
    r2 = counterfactual(waypoints_high, [zone2, copy.deepcopy(ZONE)], -100.0)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert [s["zone_id"] for s in body2["introduced"]] == ["nfz-1", "nfz-2"]


def test_identical_inputs_yield_identical_output():
    """相同输入重复调用，响应逐字段一致。"""
    r1 = cf_default(LEVEL_CROSSING, 100.0)
    r2 = cf_default(copy.deepcopy(LEVEL_CROSSING), 100.0)
    assert r1.json() == r2.json()


# ---------------------------------------------------------------- 不修改请求对象

def test_request_waypoints_not_mutated():
    """端到端：响应中给出的平移结果不影响再次审计（原载荷可安全复用）。"""
    r1 = client.post("/audit", json={"waypoints": LEVEL_CROSSING, "zones": [copy.deepcopy(ZONE)]})
    r2 = cf_default(LEVEL_CROSSING, 100.0)
    r3 = client.post("/audit", json={"waypoints": LEVEL_CROSSING, "zones": [copy.deepcopy(ZONE)]})
    assert r1.status_code == r2.status_code == r3.status_code == 200
    # 反事实调用前后，对同一原始航迹的审计结果必须完全相同
    assert r1.json() == r3.json()


def test_run_counterfactual_does_not_mutate_models():
    """单元：run_counterfactual 不改变入模航点的高度，副本才被平移。"""
    req = AuditRequest(waypoints=copy.deepcopy(LEVEL_CROSSING), zones=[copy.deepcopy(ZONE)])
    before = [(w.lon, w.lat, w.alt, w.time) for w in req.waypoints]
    shifted_wps = shift_waypoint_altitudes(req.waypoints, 100.0)
    assert [w.alt for w in shifted_wps] == [150.0, 150.0]
    # 生成副本本身不影响原对象
    assert [w.alt for w in req.waypoints] == [50.0, 50.0]

    result = run_counterfactual(req.waypoints, req.zones, 100.0)
    after = [(w.lon, w.lat, w.alt, w.time) for w in req.waypoints]
    assert before == after
    assert [w.alt for w in req.waypoints] == [50.0, 50.0]
    assert result.cleared_count == 1


# ---------------------------------------------------------------- 422 校验

def test_missing_altitude_offset_422():
    payload = {"waypoints": LEVEL_CROSSING, "zones": [copy.deepcopy(ZONE)]}
    r = client.post("/audit/counterfactual", json=payload)
    assert r.status_code == 422
    locs = [e["loc"][-1] for e in r.json()["detail"]]
    assert "altitude_offset" in locs


def test_non_numeric_altitude_offset_422():
    payload = {
        "waypoints": LEVEL_CROSSING,
        "zones": [copy.deepcopy(ZONE)],
        "altitude_offset": "up",
    }
    r = client.post("/audit/counterfactual", json=payload)
    assert r.status_code == 422


def test_out_of_order_waypoints_422():
    r = cf_default([
        wp(0, 0, 0, "2026-09-22T10:05:00Z"),
        wp(1, 1, 1, "2026-09-22T10:00:00Z"),
    ], 100.0)
    assert r.status_code == 422
    assert "时间" in r.json()["detail"]


def test_invalid_zone_geometry_422():
    zone = copy.deepcopy(ZONE)
    zone["geometry"] = {  # 蝴蝶结（自相交）
        "type": "Polygon",
        "coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]],
    }
    r = counterfactual(LEVEL_CROSSING, [zone], 0.0)
    assert r.status_code == 422
    assert "nfz-1" in r.json()["detail"]


def test_too_few_waypoints_422():
    r = cf_default([wp(0, 0, 0, "2026-09-22T10:00:00Z")], 10.0)
    assert r.status_code == 422


def test_no_zones_shift_is_noop():
    r = counterfactual(
        [wp(0, 0, 0, "2026-09-22T10:00:00Z"),
         wp(1, 1, 1, "2026-09-22T10:01:00Z")],
        [], 1000.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["baseline"] == body["shifted"] == []
    assert body["cleared"] == body["introduced"] == []
    assert body["altitude_offset"] == 1000.0
