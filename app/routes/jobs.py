"""核算作业的 HTTP 路由：提交、整体查询、按作业号取回、单工况点取回。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from .. import service
from ..deps import get_settings, get_store
from ..schemas import JobCreate, JobDetail, JobSummary, PointResult

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", status_code=201, response_model=JobDetail)
def submit_job(payload: JobCreate, request: Request) -> dict:
    """提交一个核算作业：一份物性定义（引用或临时）+ 一组工况点。

    服务逐点求解 Rachford-Rice 方程，全部结果随作业一次性落库后返回。
    任一工况点非法则整个作业以带类型错误拒绝，不落库。
    """
    return service.submit_job(get_store(request), payload, get_settings(request))


@router.get("", response_model=list[JobSummary])
def list_jobs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict]:
    """列出作业摘要（新的在前）。"""
    return get_store(request).list_jobs(limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: str, request: Request) -> dict:
    """按作业号取回全部结果（含物性快照与每个工况点的完整结果）。"""
    return service.get_job_detail(get_store(request), job_id)


@router.get("/{job_id}/points/{point_index}", response_model=PointResult)
def get_job_point(job_id: str, point_index: int, request: Request) -> dict:
    """只取作业中某一个工况点的结果（point_index 从 0 开始，按提交顺序）。"""
    return service.get_job_point(get_store(request), job_id, point_index)
