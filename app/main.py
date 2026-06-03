"""FastAPI 应用入口

主应用程序，配置路由、中间件、静态文件等
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from contextlib import asynccontextmanager
import os

from app.config import config
from loguru import logger
from app.api import chat, health, file, aiops
from app.services.rag_anything_service import rag_anything_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("=" * 60)
    logger.info(f"🚀 {config.app_name} v{config.app_version} 启动中...")
    logger.info(f"📝 环境: {'开发' if config.debug else '生产'}")
    logger.info(f"🌐 监听地址: http://{config.host}:{config.port}")
    logger.info(f"📚 API 文档: http://{config.host}:{config.port}/docs")
    
    logger.info("📚 启动阶段不更新 RAG 知识库；仅 make upload 会触发知识库写入")
    
    logger.info("=" * 60)
    
    yield
    
    # 关闭时执行
    logger.info("🔌 正在关闭 RAG-Anything 存储...")
    await rag_anything_service.close()
    logger.info(f"👋 {config.app_name} 关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    description="基于 LangChain 的智能oncall运维系统",
    lifespan=lifespan
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def public_access_token_middleware(request: Request, call_next):
    """公网访问令牌。未配置 PUBLIC_ACCESS_TOKEN 时不启用。"""
    access_token = config.public_access_token.strip()
    if not access_token or request.url.path == "/health":
        return await call_next(request)

    provided_token = (
        request.headers.get("x-access-token")
        or request.query_params.get("access_token")
        or request.cookies.get("superbiz_access_token")
    )
    if provided_token != access_token:
        if request.url.path.startswith("/api"):
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "需要访问令牌"},
            )
        return PlainTextResponse(
            "需要访问令牌。请使用包含 access_token 参数的访问地址。",
            status_code=401,
        )

    response = await call_next(request)
    if request.query_params.get("access_token") == access_token:
        response.set_cookie(
            key="superbiz_access_token",
            value=access_token,
            max_age=24 * 60 * 60,
            httponly=False,
            secure=request.url.scheme == "https",
            samesite="lax",
        )
    return response

# 注册路由
app.include_router(health.router, tags=["健康检查"])
app.include_router(chat.router, prefix="/api", tags=["对话"])
app.include_router(file.router, prefix="/api", tags=["文件管理"])
app.include_router(aiops.router, prefix="/api", tags=["AIOps智能运维"])

# 挂载静态文件
static_dir = "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def root():
    """返回首页"""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return RedirectResponse(
            url="/static/index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )
    return {
        "message": f"Welcome to {config.app_name} API",
        "version": config.app_version,
        "docs": "/docs"
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
        log_level="info"
    )
