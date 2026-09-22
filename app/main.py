"""FastAPI 应用入口。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .audit import TrackOrderError, ZoneGeometryError, audit_track
from .counterfactual import run_counterfactual
from .geojson import violations_to_feature_collection
from .models import (
    AuditRequest,
    AuditResponse,
    CounterfactualRequest,
    CounterfactualResponse,
    GeoJsonFeatureCollection,
)

app = FastAPI(
    title="无人机航迹禁飞区审计",
    version="1.2.0",
    description=(
        "按航段（而非仅航点）审计无人机航迹与禁飞区的冲突："
        "平面、生效时段、高度范围三者同时命中才判定违规，"
        "并区分擦边接触（graze）与真正进入（entry）。"
        "另提供 /audit/counterfactual：将航点高度统一平移后重审并对比差异。"
    ),
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/audit", response_model=AuditResponse)
def audit(req: AuditRequest) -> AuditResponse:
    try:
        violations = audit_track(req.waypoints, req.zones)
    except (TrackOrderError, ZoneGeometryError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AuditResponse(violation_count=len(violations), violations=violations)


@app.post("/audit/geojson", response_model=GeoJsonFeatureCollection)
def audit_geojson(req: AuditRequest) -> GeoJsonFeatureCollection:
    """与 /audit 相同的请求体与判定逻辑，结果以 GeoJSON FeatureCollection 返回。"""
    try:
        violations = audit_track(req.waypoints, req.zones)
    except (TrackOrderError, ZoneGeometryError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return violations_to_feature_collection(violations, req.waypoints)


@app.post("/audit/counterfactual", response_model=CounterfactualResponse)
def audit_counterfactual(req: CounterfactualRequest) -> CounterfactualResponse:
    """反事实审计：所有航点高度统一加上 ``altitude_offset`` 后重跑相同审计。

    原始结果（baseline）与平移结果（shifted）按 ``zone_id``、``entry_time``、
    ``exit_time`` 三元组匹配：仅在 baseline 中出现的进入区间计入 ``cleared``
    （平移后消除），仅在 shifted 中出现的计入 ``introduced``（平移后新增）。
    四组列表均按 ``(entry_time, zone_id, exit_time)`` 稳定排序；
    平移作用于航点副本，请求对象本身不会被修改。
    """
    try:
        return run_counterfactual(req.waypoints, req.zones, req.altitude_offset)
    except (TrackOrderError, ZoneGeometryError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
