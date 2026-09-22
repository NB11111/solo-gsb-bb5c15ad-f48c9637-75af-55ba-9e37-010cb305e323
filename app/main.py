"""FastAPI 应用入口。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .audit import TrackOrderError, ZoneGeometryError, audit_track
from .geojson import violations_to_feature_collection
from .models import AuditRequest, AuditResponse, GeoJsonFeatureCollection

app = FastAPI(
    title="无人机航迹禁飞区审计",
    version="1.1.0",
    description=(
        "按航段（而非仅航点）审计无人机航迹与禁飞区的冲突："
        "平面、生效时段、高度范围三者同时命中才判定违规，"
        "并区分擦边接触（graze）与真正进入（entry）。"
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
