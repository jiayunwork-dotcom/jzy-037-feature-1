"""服务配置：数据库路径、Rachford-Rice 求解容差、进料加和容差等。

所有配置项都可以用环境变量覆盖（前缀 ``FLASH_``），例如：

    FLASH_DB_PATH=/data/flash.db
    FLASH_RR_FUNCTION_TOLERANCE=1e-12
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLASH_")

    db_path: str = "./data/flash.db"
    demo_seed_enabled: bool = True

    # 进料组成之和允许的 |Σz - 1| 上限
    feed_sum_tolerance: float = 1e-6

    # Rachford-Rice 二分法收敛判据（语义见 app/flash.py 的模块文档字符串）
    rr_function_tolerance: float = 1e-12
    rr_bracket_tolerance: float = 1e-14
    rr_max_iterations: int = 200

    # 非理想（van Laar）路径：泡点函数二分的组成容差与函数容差，
    # 以及 [0,1] 上符号扫描的等分数（语义见 app/nonideal_flash.py）
    nonideal_x_tolerance: float = 1e-12
    nonideal_f_tolerance: float = 1e-10
    nonideal_scan_intervals: int = 1024

    # 单个作业允许的最大工况点数（防御性上限）
    max_points_per_job: int = 10_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
