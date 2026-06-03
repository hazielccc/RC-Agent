"""文件上传接口模块"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import config
from app.services.rag_anything_service import (
    IMAGE_EXTENSIONS,
    LEGACY_OFFICE_EXTENSIONS,
    MODERN_OFFICE_EXTENSIONS,
    PDF_EXTENSIONS,
    RAG_ANYTHING_SUPPORTED_EXTENSIONS,
    TEXT_FILE_EXTENSIONS,
    rag_anything_service,
)
from app.services.vector_index_service import vector_index_service
from loguru import logger

router = APIRouter()

# 文件上传后存储的路径
UPLOAD_DIR = Path("./uploads")
LOCAL_INDEX_ROOTS = [
    Path("./aiops-docs").resolve(),
    Path(config.local_knowledge_dir).resolve(),
    UPLOAD_DIR.resolve(),
]
# 支持的文件类型
ALLOWED_EXTENSIONS = [ext.lstrip(".") for ext in RAG_ANYTHING_SUPPORTED_EXTENSIONS]
# 单个文件支持最大大小
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
RAG_UPDATE_MODE_HEADER = "X-RAG-Update-Mode"


class IndexFileRequest(BaseModel):
    """本地文件索引请求。"""

    file_path: str


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    上传文件到当前对话附件目录。

    普通上传不会写入 RAG 知识库；只有 make upload 调用索引接口时才更新知识库。

    Args:
        file: 上传的文件

    Returns:
        JSONResponse: 上传结果
    """
    try:
        # 1. 验证文件
        if not file.filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")

        # 2. 规范化文件名（去除空格，处理 Windows 上传的文件）
        safe_filename = _sanitize_filename(file.filename)

        # 3. 验证文件扩展名
        file_extension = _get_file_extension(safe_filename)
        if file_extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式，仅支持: {', '.join(ALLOWED_EXTENSIONS)}",
            )

        # 4. 创建上传目录
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

        # 5. 保存文件
        file_path = UPLOAD_DIR / safe_filename

        # 如果文件已存在，先删除旧文件（实现覆盖更新）
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
            file_path.unlink()

        # 读取并保存文件内容
        content = await file.read()

        # 验证文件大小
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件大小超过限制（最大 {MAX_FILE_SIZE} 字节）")

        file_path.write_bytes(content)

        logger.info(f"文件上传成功: {file_path}")
        extracted_text, text_truncated, extraction_error = await _extract_attachment_text(file_path)

        # 6. 返回响应。此接口不更新 RAG 知识库，避免对话上传触发持久化索引。
        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success",
                "data": {
                    "filename": safe_filename,
                    "file_path": str(file_path),
                    "size": len(content),
                    "doc_id": None,
                    "indexed": False,
                    "index_skipped": True,
                    "index_error": None,
                    "extracted_text": extracted_text,
                    "text_truncated": text_truncated,
                    "extraction_error": extraction_error,
                    "content_type": file.content_type,
                },
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {e}")


@router.post("/index_file")
async def index_file(
    request: IndexFileRequest,
    x_rag_update_mode: str | None = Header(default=None, alias=RAG_UPDATE_MODE_HEADER),
):
    """
    索引服务端本地文件，保留原始目录路径，适合初始化本地知识库。

    Args:
        request: 包含 file_path 的请求体

    Returns:
        JSONResponse: 索引结果
    """
    try:
        _require_make_upload_mode(x_rag_update_mode)

        file_path = Path(request.file_path).resolve()
        if not any(file_path.is_relative_to(root) for root in LOCAL_INDEX_ROOTS):
            allowed_roots = ", ".join(str(root) for root in LOCAL_INDEX_ROOTS)
            raise HTTPException(
                status_code=400,
                detail=f"不允许索引该路径，仅支持以下目录: {allowed_roots}",
            )

        file_extension = file_path.suffix.lower().lstrip(".")

        if file_extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式，仅支持: {', '.join(ALLOWED_EXTENSIONS)}",
            )

        logger.info(f"开始索引本地文件: {file_path}")
        doc_id = await vector_index_service.aindex_single_file(str(file_path))

        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success",
                "data": {
                    "file_path": str(file_path),
                    "doc_id": doc_id,
                },
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"索引本地文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"索引本地文件失败: {e}")


@router.post("/index_directory")
async def index_directory(
    directory_path: str = None,
    x_rag_update_mode: str | None = Header(default=None, alias=RAG_UPDATE_MODE_HEADER),
):
    """
    索引指定目录下的所有文件

    Args:
        directory_path: 目录路径（可选，默认使用 uploads 目录）

    Returns:
        JSONResponse: 索引结果
    """
    try:
        _require_make_upload_mode(x_rag_update_mode)
        logger.info(f"开始索引目录: {directory_path or 'uploads'}")

        # 执行索引
        result = await vector_index_service.aindex_directory(directory_path)

        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success" if result.success else "partial_success",
                "data": result.to_dict(),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"索引目录失败: {e}")
        raise HTTPException(status_code=500, detail=f"索引目录失败: {e}")


def _get_file_extension(filename: str) -> str:
    """
    获取文件扩展名

    Args:
        filename: 文件名

    Returns:
        str: 扩展名（小写，不含点）
    """
    parts = filename.rsplit(".", 1)
    if len(parts) == 2:
        return parts[1].lower()
    return ""


def _require_make_upload_mode(update_mode: str | None) -> None:
    """只允许 make upload 触发持久化 RAG 知识库更新。"""
    if update_mode == config.rag_update_mode_header_value:
        return

    raise HTTPException(
        status_code=403,
        detail=(
            "RAG 知识库更新已限制为 make upload。"
            f"请通过 Makefile 调用，或携带请求头 {RAG_UPDATE_MODE_HEADER}: "
            f"{config.rag_update_mode_header_value}"
        ),
    )


async def _extract_attachment_text(path: Path) -> tuple[str | None, bool, str | None]:
    """抽取当前对话附件内容，不写入 RAG 知识库。"""
    try:
        rag_anything_service._ensure_runtime_path()
        suffix = path.suffix.lower()

        if suffix in TEXT_FILE_EXTENSIONS:
            text = await asyncio.to_thread(rag_anything_service._read_text_file, path)
        elif suffix in MODERN_OFFICE_EXTENSIONS:
            text = await asyncio.to_thread(rag_anything_service._extract_modern_office_text, path)
        elif suffix in LEGACY_OFFICE_EXTENSIONS:
            text = await asyncio.to_thread(rag_anything_service._extract_legacy_office_text, path)
        elif suffix in PDF_EXTENSIONS:
            text = await asyncio.to_thread(rag_anything_service._extract_pdf_text, path)
            if not text.strip():
                try:
                    text = await rag_anything_service._extract_pdf_text_with_qwen_vision(path)
                except Exception as e:
                    logger.warning(f"当前对话 PDF OCR 解析失败: {path.name}, error={e}")
                    return None, False, f"PDF 未检测到可提取文本层，Qwen3.6 Plus OCR 解析失败: {e}"
        elif suffix in IMAGE_EXTENSIONS:
            if config.rag_anything_image_ocr_enabled:
                try:
                    text = await rag_anything_service._ocr_image_with_qwen(path)
                except Exception as e:
                    logger.warning(f"当前对话图片 OCR 失败，使用图片元数据兜底: {path.name}, error={e}")
                    text = rag_anything_service._extract_image_metadata_text(path, ocr_error=str(e))
            else:
                text = rag_anything_service._extract_image_metadata_text(path)
        else:
            return None, False, f"暂不支持在当前对话直接抽取该文件类型: {suffix}"

        text = (text or "").strip()
        if not text:
            return None, False, "文件内容为空或无法抽取文本"

        limit = max(config.chat_attachment_context_max_chars, 1000)
        if len(text) > limit:
            return text[:limit], True, None
        return text, False, None
    except Exception as e:
        logger.warning(f"当前对话附件内容抽取失败: {path}, error={e}")
        return None, False, str(e)


def _sanitize_filename(filename: str) -> str:
    """
    规范化文件名，去除空格和特殊字符

    Args:
        filename: 原始文件名

    Returns:
        str: 规范化后的文件名
    """
    # 去除空格
    sanitized = filename.replace(" ", "_")
    # 去除其他可能导致问题的字符
    for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        sanitized = sanitized.replace(char, "_")
    return sanitized
