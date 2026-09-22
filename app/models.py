"""请求 / 响应的数据模型（Pydantic v2）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _ensure_utc(v: datetime) -> datetime:
    """无 tzinfo 的时间一律按 UTC 处理，避免 naive/aware 混比。"""
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


class Waypoint(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0, description="纬度，度")
    lon: float = Field(..., ge=-180.0, le=180.0, description="经度，度")
    alt: float = Field(..., description="高度，米")
    time: datetime = Field(..., description="ISO 8601 时间，无时区按 UTC")

    _utc = field_validator("time")(_ensure_utc)


class Zone(BaseModel):
    """禁飞区：GeoJSON 多边形 + 生效时段 + 高度范围。"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "nfz-1",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[116.30, 39.90], [116.32, 39.90],
                                 [116.32, 39.92], [116.30, 39.92], [116.30, 39.90]]],
            },
            "active_from": "2026-09-22T10:00:00Z",
            "active_to": "2026-09-22T11:00:00Z",
            "min_alt": 0,
            "max_alt": 120,
        }
    })

    id: str = Field(..., min_length=1, description="区域标识")
    geometry: Dict[str, Any] = Field(..., description="GeoJSON Polygon / MultiPolygon")
    active_from: datetime
    active_to: datetime
    min_alt: Optional[float] = Field(None, description="高度下限（含），缺省不限制")
    max_alt: Optional[float] = Field(None, description="高度上限（含），缺省不限制")

    _utc_from = field_validator("active_from")(_ensure_utc)
    _utc_to = field_validator("active_to")(_ensure_utc)

    @model_validator(mode="after")
    def _check_window(self) -> "Zone":
        if self.active_to <= self.active_from:
            raise ValueError("active_to 必须晚于 active_from")
        if self.min_alt is not None and self.max_alt is not None and self.max_alt < self.min_alt:
            raise ValueError("max_alt 不能小于 min_alt")
        return self


class AuditRequest(BaseModel):
    waypoints: List[Waypoint] = Field(..., min_length=2, description="按时间升序的航点")
    zones: List[Zone] = Field(default_factory=list, description="禁飞区列表")


class CounterfactualRequest(BaseModel):
    """反事实审计请求：在原始请求基础上增加必填的高度平移量。"""

    waypoints: List[Waypoint] = Field(..., min_length=2, description="按时间升序的航点")
    zones: List[Zone] = Field(default_factory=list, description="禁飞区列表")
    altitude_offset: float = Field(
        ...,
        description="全部航点高度统一平移量，米；正值升高、负值降低、0 为原样重跑",
    )


class Position(BaseModel):
    lat: float
    lon: float
    alt: float


class Violation(BaseModel):
    zone_id: str
    kind: Literal["entry", "graze"] = Field(..., description="entry=真正进入；graze=擦边接触")
    entry_time: datetime
    exit_time: datetime
    entry_position: Position
    exit_position: Position
    segment_start: int = Field(..., description="进入点所在的航段下标（从 0 开始）")
    segment_end: int = Field(..., description="离开点所在的航段下标")


class AuditResponse(BaseModel):
    violation_count: int
    violations: List[Violation]


class ViolationSummary(BaseModel):
    """违规摘要：匹配所需的标识键（zone_id + entry_time + exit_time）及必要上下文。"""

    zone_id: str
    kind: Literal["entry", "graze"] = Field(..., description="entry=真正进入；graze=擦边接触")
    entry_time: datetime
    exit_time: datetime
    segment_start: int = Field(..., description="进入点所在的航段下标（从 0 开始）")
    segment_end: int = Field(..., description="离开点所在的航段下标")


class CounterfactualResponse(BaseModel):
    altitude_offset: float = Field(..., description="本次使用的高度平移量，米")
    baseline: List[ViolationSummary] = Field(
        ..., description="原始航迹的违规摘要，按 (entry_time, zone_id, exit_time) 稳定升序"
    )
    shifted: List[ViolationSummary] = Field(
        ..., description="高度平移后的违规摘要，排序规则与 baseline 相同"
    )
    cleared: List[ViolationSummary] = Field(
        ..., description="baseline 中有、shifted 中无的违规（平移后消除），同序稳定排列"
    )
    introduced: List[ViolationSummary] = Field(
        ..., description="shifted 中有、baseline 中无的违规（平移后新增），同序稳定排列"
    )
    baseline_count: int
    shifted_count: int
    cleared_count: int
    introduced_count: int


class ViolationProperties(BaseModel):
    """GeoJSON Feature 的属性，与 Violation 的标量字段一一对应。"""

    zone_id: str
    kind: Literal["entry", "graze"]
    entry_time: datetime
    exit_time: datetime
    segment_start: int
    segment_end: int


class GeoJsonFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: Dict[str, Any] = Field(
        ..., description="Point 或 LineString，坐标为 [经度, 纬度, 高度]"
    )
    properties: ViolationProperties


class GeoJsonFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: List[GeoJsonFeature]
