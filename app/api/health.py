"""健康检查接口"""

from typing import Any
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.config import config
from app.services.rag_anything_service import rag_anything_service
from loguru import logger

router = APIRouter()


@router.get("/health")
async def health_check():
    
    """健康检查接口
    检查服务状态和数据库连接状态
    
    Returns:
        JSONResponse: 健康检查结果
    """
    # 检查服务基本状态
    health_data: dict[str, Any] = {  # pyright: ignore[reportExplicitAny]
        "service": config.app_name,
        "version": config.app_version,
        "status": "healthy"
    }
    
    # 检查 RAG-Anything 状态。启动阶段不触发初始化/索引，避免健康检查更新知识库。
    try:
        rag_healthy = await rag_anything_service.health_check()
        rag_status: str = "ready" if rag_healthy else "idle"
        rag_message: str = (
            "RAG-Anything 已就绪"
            if rag_healthy
            else "RAG-Anything 尚未初始化；仅 make upload 会触发知识库写入"
        )
        health_data["rag_anything"] = {
            "status": rag_status,
            "message": rag_message
        }
    except Exception as e:
        logger.warning(f"RAG-Anything 健康检查失败: {e}")
        health_data["rag_anything"] = {
            "status": "error",
            "message": f"RAG-Anything 检查失败: {str(e)}"
        }
    
    health_data["status"] = "healthy"
    
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "服务运行正常",
            "data": health_data
        }
    )
