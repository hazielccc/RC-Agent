"""RAG-Anything 服务适配层.

封装 RAG-Anything/LightRAG 的初始化、文档写入和查询，供现有 API 和工具复用。
"""

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from contextlib import asynccontextmanager
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from lightrag.llm.openai import openai_complete_if_cache, openai_embed
from lightrag.utils import EmbeddingFunc
from loguru import logger
from openai import AsyncOpenAI
from raganything import RAGAnything, RAGAnythingConfig

from app.config import config


RAG_ANYTHING_SUPPORTED_EXTENSIONS = tuple(RAGAnythingConfig().supported_file_extensions)
TEXT_FILE_EXTENSIONS = (".txt", ".md")
MODERN_OFFICE_EXTENSIONS = (".docx", ".pptx", ".xlsx")
LEGACY_OFFICE_EXTENSIONS = (".doc", ".ppt", ".xls")
PDF_EXTENSIONS = (".pdf",)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp")


class RAGAnythingService:
    """RAG-Anything 服务门面。"""

    def __init__(self) -> None:
        self.working_dir = Path(config.rag_anything_working_dir)
        self.parser_output_dir = Path(config.rag_anything_parser_output_dir)
        self.query_mode = config.rag_anything_query_mode
        self._rag: RAGAnything | None = None
        self._init_lock = asyncio.Lock()

    async def ensure_ready(self) -> None:
        """初始化 RAG-Anything 和底层 LightRAG 存储。"""
        if self._rag is not None and self._rag.lightrag is not None:
            return

        async with self._init_lock:
            if self._rag is not None and self._rag.lightrag is not None:
                return

            self.working_dir.mkdir(parents=True, exist_ok=True)
            self.parser_output_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_runtime_path()

            rag_config = RAGAnythingConfig(
                working_dir=str(self.working_dir),
                parser_output_dir=str(self.parser_output_dir),
                parser=config.rag_anything_parser,
                parse_method=config.rag_anything_parse_method,
                display_content_stats=False,
                enable_image_processing=True,
                enable_table_processing=True,
                enable_equation_processing=True,
                supported_file_extensions=list(RAG_ANYTHING_SUPPORTED_EXTENSIONS),
            )

            self._rag = RAGAnything(
                config=rag_config,
                llm_model_func=self._llm_model_func,
                embedding_func=self._embedding_func(),
                lightrag_kwargs={
                    "llm_model_name": self._rag_anything_llm_model(),
                    "llm_model_max_async": max(1, config.rag_anything_llm_max_async),
                    "embedding_func_max_async": 2,
                    "enable_llm_cache": True,
                },
            )

            result = await self._rag._ensure_lightrag_initialized()
            if not result.get("success"):
                raise RuntimeError(f"RAG-Anything 初始化失败: {result.get('error')}")

            logger.info(
                f"RAG-Anything 初始化完成: working_dir={self.working_dir}, "
                f"query_mode={self.query_mode}"
            )

    def is_ready(self) -> bool:
        """返回 RAG-Anything 当前是否已完成初始化。"""
        return self._rag is not None and self._rag.lightrag is not None

    async def index_file(self, file_path: str) -> str:
        """将单个 RAG-Anything 支持的文件写入知识库。"""
        await self.ensure_ready()
        assert self._rag is not None

        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            raise ValueError(f"文件不存在: {file_path}")

        suffix = path.suffix.lower()
        if suffix not in RAG_ANYTHING_SUPPORTED_EXTENSIONS:
            supported = ", ".join(RAG_ANYTHING_SUPPORTED_EXTENSIONS)
            raise ValueError(f"RAG-Anything 不支持该文件格式: {suffix}，支持: {supported}")

        doc_id = self._doc_id(path)
        await self._delete_existing_doc(doc_id)

        if suffix in TEXT_FILE_EXTENSIONS:
            content = self._read_text_file(path)
            await self._insert_text_content(path, doc_id, content)
        elif suffix in MODERN_OFFICE_EXTENSIONS:
            logger.info(
                f"使用 Office 文本解析，避免 MinerU/PDF 解析占用过高内存: {path.name}"
            )
            content = self._extract_modern_office_text(path)
            await self._insert_text_content(path, doc_id, content)
        elif suffix in LEGACY_OFFICE_EXTENSIONS:
            logger.info(
                f"使用旧版 Office 文本解析，避免 MinerU/PDF 解析长时间阻塞: {path.name}"
            )
            content = self._extract_legacy_office_text(path)
            await self._insert_text_content(path, doc_id, content)
        elif suffix in PDF_EXTENSIONS:
            content = self._extract_pdf_text(path)
            if content.strip():
                logger.info(
                    f"使用 PDF 文本层解析，避免 MinerU/OCR 占用过高内存: {path.name}"
                )
                await self._insert_text_content(path, doc_id, content)
            else:
                logger.info(
                    f"PDF 无可提取文本层，使用 {config.chat_pdf_ocr_model} 并发 OCR: {path.name}"
                )
                try:
                    content = await self._extract_pdf_text_with_qwen_vision(path)
                    if not content.strip():
                        raise RuntimeError("Qwen PDF OCR 未返回可用文本")
                    await self._insert_text_content(path, doc_id, content)
                except Exception as e:
                    raise RuntimeError(f"扫描版 PDF Qwen OCR 解析失败: {path.name}: {e}") from e
        elif suffix in IMAGE_EXTENSIONS:
            if config.rag_anything_image_ocr_enabled:
                try:
                    content = await self._ocr_image_with_qwen(path)
                except Exception as e:
                    logger.warning(f"Qwen 视觉 OCR 失败，使用图片元数据兜底: {path.name}, error={e}")
                    content = self._extract_image_metadata_text(path, ocr_error=str(e))
            else:
                logger.info(
                    f"图片 OCR 已关闭，使用图片文件名和元数据入库: {path.name}"
                )
                content = self._extract_image_metadata_text(path)
            await self._insert_text_content(path, doc_id, content)
        else:
            try:
                await self._process_with_mineru(path, doc_id)
            except Exception as e:
                if suffix in MODERN_OFFICE_EXTENSIONS and self._is_libreoffice_error(e):
                    logger.warning(
                        f"LibreOffice 转换失败，使用 Office 文本兜底解析: {path.name}"
                    )
                    await self._delete_existing_doc(doc_id)
                    content = self._extract_modern_office_text(path)
                    await self._insert_text_content(path, doc_id, content)
                elif suffix in PDF_EXTENSIONS and self._is_mps_out_of_memory_error(e):
                    logger.warning(f"MinerU MPS 内存不足，尝试 PDF 文本层兜底: {path.name}")
                    await self._delete_existing_doc(doc_id)
                    content = self._extract_pdf_text(path)
                    if not content.strip():
                        raise
                    await self._insert_text_content(path, doc_id, content)
                else:
                    raise

        logger.info(f"RAG-Anything 文档索引完成: {path}, doc_id={doc_id}")
        return doc_id

    async def _process_with_mineru(
        self,
        path: Path,
        doc_id: str,
    ) -> None:
        if self._rag is None:
            raise RuntimeError("RAG-Anything 尚未初始化")

        kwargs: dict[str, Any] = {}
        mineru_backend = self._mineru_backend()
        if mineru_backend:
            kwargs["backend"] = mineru_backend

        async with self._limit_lightrag_pipeline_to_doc(doc_id):
            await self._rag.process_document_complete(
                file_path=str(path),
                output_dir=str(self.parser_output_dir),
                parse_method=config.rag_anything_parse_method,
                doc_id=doc_id,
                file_name=str(path),
                display_stats=False,
                **kwargs,
            )
        await self._ensure_doc_processed(path, doc_id)

    async def _insert_text_content(self, path: Path, doc_id: str, content: str) -> None:
        if self._rag is None:
            raise RuntimeError("RAG-Anything 尚未初始化")

        if not content.strip():
            raise ValueError(f"文件内容为空或无法提取文本: {path}")

        async with self._limit_lightrag_pipeline_to_doc(doc_id):
            await self._rag.insert_content_list(
                content_list=[{"type": "text", "text": content, "page_idx": 0}],
                file_path=str(path),
                doc_id=doc_id,
                display_stats=False,
            )
        await self._ensure_doc_processed(path, doc_id)

    async def _ensure_doc_processed(self, path: Path, doc_id: str) -> None:
        """Fail fast when LightRAG only queued a document but did not index it."""
        if self._rag is None or self._rag.lightrag is None:
            raise RuntimeError("RAG-Anything 尚未初始化")

        doc_status_storage = getattr(self._rag.lightrag, "doc_status", None)
        if doc_status_storage is None:
            logger.warning(f"无法校验文档索引状态，跳过校验: {path.name}")
            return

        status = await doc_status_storage.get_by_id(doc_id)
        if not status:
            raise RuntimeError(f"文档索引未生成状态记录: {path.name}, doc_id={doc_id}")

        raw_status = status.get("status")
        state = str(getattr(raw_status, "value", raw_status or "")).lower()
        chunks_count = status.get("chunks_count") or 0
        chunks_list = status.get("chunks_list") or []

        if state != "processed":
            error_msg = status.get("error_msg") or ""
            detail = f"status={state or 'unknown'}, chunks={chunks_count}"
            if error_msg:
                detail = f"{detail}, error={error_msg}"
            raise RuntimeError(
                f"文档尚未完成可检索索引: {path.name} ({detail})。"
                "请等待当前索引任务结束后重试。"
            )

        if chunks_count <= 0 or not chunks_list:
            raise RuntimeError(f"文档索引完成但没有生成可检索分块: {path.name}")

    @asynccontextmanager
    async def _limit_lightrag_pipeline_to_doc(self, doc_id: str):
        """Limit a make-upload single-file write to its own document queue item.

        LightRAG's insert pipeline normally consumes every pending/failed document
        in the shared doc_status store. make upload calls this service one file at a
        time, so processing old backlog entries here causes surprising full re-runs.
        """
        if self._rag is None or self._rag.lightrag is None:
            yield
            return

        doc_status = getattr(self._rag.lightrag, "doc_status", None)
        original_get_docs = getattr(doc_status, "get_docs_by_statuses", None)
        if doc_status is None or original_get_docs is None:
            yield
            return

        async def get_only_current_doc(statuses):
            docs = await original_get_docs(statuses)
            if doc_id in docs:
                return {doc_id: docs[doc_id]}
            return {}

        setattr(doc_status, "get_docs_by_statuses", get_only_current_doc)
        try:
            yield
        finally:
            setattr(doc_status, "get_docs_by_statuses", original_get_docs)

    async def _delete_existing_doc(self, doc_id: str) -> None:
        try:
            if self._rag.lightrag is not None:
                await self._rag.lightrag.adelete_by_doc_id(doc_id)
        except Exception as e:
            logger.debug(f"删除旧 RAG-Anything 文档失败或文档不存在: doc_id={doc_id}, error={e}")

    async def query(self, query: str) -> str:
        """使用 RAG-Anything 查询知识库。"""
        await self.ensure_ready()
        assert self._rag is not None

        result = await self._rag.aquery(
            query,
            mode=self.query_mode,
            top_k=config.rag_top_k,
            vlm_enhanced=False,
        )
        return result if isinstance(result, str) else str(result)

    async def _ocr_image_with_qwen(self, path: Path) -> str:
        if not config.dashscope_api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置，无法调用 Qwen 视觉 OCR")

        client = AsyncOpenAI(
            api_key=config.dashscope_api_key,
            base_url=config.dashscope_api_base,
            timeout=config.rag_anything_image_ocr_timeout,
        )
        prompt = (
            "请对这张图片进行 OCR 和视觉理解，输出适合写入知识库检索的中文 Markdown。\n"
            "要求：\n"
            "1. 尽量完整识别图片中的文字、数字、标题、表格、注释和图例。\n"
            "2. 如果是表格，请尽量用 Markdown 表格或分行键值对保留结构。\n"
            "3. 如果是图表、公式或示意图，请补充一段客观说明。\n"
            "4. 不要编造看不清的内容；无法识别的位置用 [?] 标记。\n"
            "5. 只输出识别和说明内容，不要输出寒暄。"
        )

        completion = await client.chat.completions.create(
            model=config.rag_anything_image_ocr_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": self._image_data_url(path)},
                            "min_pixels": 32 * 32 * 3,
                            "max_pixels": 32 * 32 * 8192,
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            max_tokens=config.rag_anything_image_ocr_max_tokens,
            temperature=0.01,
        )

        content = completion.choices[0].message.content or ""
        content = content.strip()
        if not content:
            raise RuntimeError("Qwen 视觉 OCR 返回内容为空")

        return "\n".join(
            [
                f"图片文件: {path.name}",
                f"文件路径: {path}",
                f"OCR模型: {config.rag_anything_image_ocr_model}",
                "",
                "OCR识别结果:",
                content,
            ]
        )

    async def health_check(self) -> bool:
        """检查 RAG-Anything 是否已就绪，不在健康检查里触发重型初始化。"""
        return self.is_ready()

    async def close(self) -> None:
        """持久化并关闭 RAG-Anything 存储。"""
        if self._rag is not None:
            await self._rag.finalize_storages()

    @staticmethod
    async def _llm_model_func(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        if history_messages is None:
            history_messages = []

        api_key = RAGAnythingService._rag_anything_llm_api_key()
        if not api_key:
            raise RuntimeError("未配置 RAG-Anything 建库模型 API Key")

        return await openai_complete_if_cache(
            RAGAnythingService._rag_anything_llm_model(),
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            api_key=api_key,
            base_url=RAGAnythingService._rag_anything_llm_api_base(),
            **kwargs,
        )

    @staticmethod
    def _rag_anything_llm_model() -> str:
        return (
            config.rag_anything_llm_model
            or config.mimo_model
            or config.rag_model
        ).strip()

    @staticmethod
    def _rag_anything_llm_api_key() -> str:
        return (
            config.rag_anything_llm_api_key
            or config.mimo_api_key
            or config.dashscope_api_key
        ).strip()

    @staticmethod
    def _rag_anything_llm_api_base() -> str:
        return (
            config.rag_anything_llm_api_base
            or config.mimo_api_base
            or config.dashscope_api_base
        ).strip()

    @staticmethod
    def _embedding_func() -> EmbeddingFunc:
        if (config.embedding_provider or "").strip().lower() in {
            "bge-m3-mlx",
            "mlx",
            "local",
        }:
            from app.services.local_embedding_service import embed_texts_with_bge_m3_mlx

            return EmbeddingFunc(
                embedding_dim=config.local_embedding_dim,
                max_token_size=config.local_embedding_max_tokens,
                send_dimensions=False,
                model_name=config.local_embedding_model_name,
                func=embed_texts_with_bge_m3_mlx,
            )

        return EmbeddingFunc(
            embedding_dim=config.local_embedding_dim,
            max_token_size=config.local_embedding_max_tokens,
            send_dimensions=True,
            model_name=config.dashscope_embedding_model,
            func=partial(
                openai_embed.func,
                model=config.dashscope_embedding_model,
                api_key=config.dashscope_api_key,
                base_url=config.dashscope_api_base,
            ),
        )

    @staticmethod
    def _ensure_runtime_path() -> None:
        candidate_dirs = [
            Path(sys.prefix) / "bin",
            Path(sys.executable).parent,
            Path(sys.executable).resolve().parent,
            Path.cwd() / ".venv" / "bin",
            Path("/Applications/LibreOffice.app/Contents/MacOS"),
            Path.home() / "Applications" / "LibreOffice.app" / "Contents" / "MacOS",
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
        ]
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        new_entries = [str(path) for path in candidate_dirs if path.exists()]
        for entry in reversed(new_entries):
            if entry not in path_entries:
                path_entries.insert(0, entry)
        os.environ["PATH"] = os.pathsep.join(path_entries)

    @staticmethod
    def _read_text_file(path: Path) -> str:
        for encoding in ("utf-8", "gbk", "latin-1", "cp1252"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法识别文本文件编码: {path}")

    @staticmethod
    def _has_libreoffice() -> bool:
        return shutil.which("libreoffice") is not None or shutil.which("soffice") is not None

    @staticmethod
    def _is_libreoffice_error(error: Exception) -> bool:
        message = str(error)
        return "LibreOffice conversion failed" in message or "LibreOffice" in message

    @staticmethod
    def _is_mps_out_of_memory_error(error: Exception) -> bool:
        message = str(error)
        return "MPS backend out of memory" in message or "mps" in message.lower()

    @staticmethod
    def _mineru_backend() -> str:
        return config.rag_anything_mineru_backend.strip()

    @staticmethod
    def _extract_pdf_text(path: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as e:
            logger.warning(f"pypdf 未安装，无法进行 PDF 文本层解析: {e}")
            return ""

        try:
            reader = PdfReader(str(path))
        except Exception as e:
            logger.warning(f"无法读取 PDF 文本层: {path}, error={e}")
            return ""

        pages: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as e:
                logger.debug(f"跳过无法抽取文本的 PDF 页: {path.name} page={index}, error={e}")
                continue
            text = text.strip()
            if text:
                pages.append(f"Page {index}\n{text}")

        return "\n\n".join(pages)

    async def _extract_pdf_text_with_qwen_vision(self, path: Path) -> str:
        """用 Qwen3.6 Plus 对扫描版 PDF 逐页并发 OCR。"""
        cached_text = self._read_cached_qwen_pdf_ocr_text(path)
        if cached_text.strip():
            logger.info(f"复用已存在的 Qwen PDF OCR 结果: {path.name}")
            return cached_text

        if not config.chat_pdf_ocr_enabled:
            return ""
        if not config.dashscope_api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置，无法调用 Qwen3.6 Plus PDF OCR")

        page_count = await asyncio.to_thread(self._pdf_page_count, path)
        if page_count <= 0:
            return ""

        client = AsyncOpenAI(
            api_key=config.dashscope_api_key,
            base_url=config.dashscope_api_base,
            timeout=config.chat_pdf_ocr_page_timeout,
        )
        concurrency = max(1, config.chat_pdf_ocr_concurrency)
        semaphore = asyncio.Semaphore(concurrency)

        logger.info(
            f"PDF 无文本层，开始使用 {config.chat_pdf_ocr_model} 并发 OCR: "
            f"{path.name}, pages={page_count}, concurrency={concurrency}"
        )

        async def extract_page(page_index: int) -> tuple[int, str]:
            async with semaphore:
                return await self._extract_pdf_page_with_qwen(client, path, page_index, page_count)

        tasks = [asyncio.create_task(extract_page(page_index)) for page_index in range(page_count)]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=config.chat_pdf_ocr_timeout,
            )
        except TimeoutError as e:
            for task in tasks:
                task.cancel()
            raise RuntimeError(
                f"Qwen PDF OCR 超时（{config.chat_pdf_ocr_timeout} 秒）: {path.name}"
            ) from e

        page_texts: list[tuple[int, str]] = []
        failures: list[str] = []
        for page_number, result in enumerate(results, start=1):
            if isinstance(result, Exception):
                message = str(result).strip() or result.__class__.__name__
                failures.append(f"第{page_number}页: {message}")
                continue

            result_page, text = result
            if text.strip():
                page_texts.append((result_page, text.strip()))
            else:
                failures.append(f"第{page_number}页: 未返回可用文本")

        if not page_texts:
            detail = "；".join(failures[:3])
            raise RuntimeError(f"Qwen PDF OCR 未返回任何文本: {path.name}; {detail}")

        page_texts.sort(key=lambda item: item[0])
        output_parts = [f"Page {page_number}\n{text}" for page_number, text in page_texts]
        if failures:
            logger.warning(f"Qwen PDF OCR 部分页面失败: {path.name}; {'; '.join(failures)}")
            output_parts.append("OCR提示: 以下页面未能成功识别：" + "；".join(failures))

        text = "\n\n".join(output_parts).strip()
        self._write_qwen_pdf_ocr_cache_text(path, text)
        return text

    @staticmethod
    def _pdf_page_count(path: Path) -> int:
        try:
            import pypdfium2 as pdfium
        except ImportError as e:
            raise RuntimeError("缺少 pypdfium2，无法将扫描 PDF 渲染为图片") from e

        pdf = pdfium.PdfDocument(str(path))
        try:
            return len(pdf)
        finally:
            close = getattr(pdf, "close", None)
            if close:
                close()

    async def _extract_pdf_page_with_qwen(
        self,
        client: AsyncOpenAI,
        path: Path,
        page_index: int,
        page_count: int,
    ) -> tuple[int, str]:
        image_data_url = await asyncio.to_thread(
            self._render_pdf_page_image_data_url,
            path,
            page_index,
        )
        text = await self._ocr_pdf_page_image_with_qwen(
            client,
            image_data_url,
            page_index + 1,
            page_count,
        )
        return page_index + 1, text

    async def _ocr_pdf_page_image_with_qwen(
        self,
        client: AsyncOpenAI,
        image_data_url: str,
        page_number: int,
        page_count: int,
    ) -> str:
        prompt = self._qwen_pdf_ocr_prompt(page_number, page_count)
        max_retries = 2
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                completion = await client.chat.completions.create(
                    model=config.chat_pdf_ocr_model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": image_data_url},
                                    "min_pixels": 256 * 256,
                                    "max_pixels": config.chat_pdf_ocr_max_pixels,
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                    max_tokens=config.chat_pdf_ocr_max_tokens,
                    temperature=0.01,
                )
                content = self._normalise_llm_content(completion.choices[0].message.content)
                if content.strip():
                    return content.strip()
                last_error = RuntimeError("模型返回内容为空")
            except Exception as e:
                last_error = e

            if attempt < max_retries:
                await asyncio.sleep(min(2**attempt, 5))

        raise RuntimeError(f"第{page_number}页 Qwen OCR 失败: {last_error}")

    def _render_pdf_page_image_data_url(self, path: Path, page_index: int) -> str:
        try:
            import pypdfium2 as pdfium
            from PIL import Image
        except ImportError as e:
            raise RuntimeError("缺少 pypdfium2 或 Pillow，无法渲染扫描 PDF 页面") from e

        pdf = pdfium.PdfDocument(str(path))
        try:
            page = pdf[page_index]
            bitmap = None
            try:
                scale = max(config.chat_pdf_ocr_render_dpi, 72) / 72
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
            finally:
                close_page = getattr(page, "close", None)
                if close_page:
                    close_page()
                close_bitmap = getattr(bitmap, "close", None)
                if close_bitmap:
                    close_bitmap()
        finally:
            close_pdf = getattr(pdf, "close", None)
            if close_pdf:
                close_pdf()

        if image.mode != "RGB":
            image = image.convert("RGB")

        image = self._resize_image_for_qwen(image)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=config.chat_pdf_ocr_jpeg_quality, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    @staticmethod
    def _resize_image_for_qwen(image: Any) -> Any:
        max_pixels = max(config.chat_pdf_ocr_max_pixels, 256 * 256)
        width, height = image.size
        pixels = width * height
        if pixels <= max_pixels:
            return image

        from PIL import Image

        ratio = (max_pixels / pixels) ** 0.5
        new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        return image.resize(new_size, resampling)

    @staticmethod
    def _qwen_pdf_ocr_prompt(page_number: int, page_count: int) -> str:
        return (
            f"请对这张扫描版 PDF 第 {page_number}/{page_count} 页进行高精度 OCR，"
            "输出适合后续问答和检索的中文 Markdown。\n"
            "要求：\n"
            "1. 尽量完整识别页面中的标题、正文、页眉页脚、编号、印章文字、表格和脚注。\n"
            "2. 保留原始阅读顺序；表格尽量用 Markdown 表格或分行键值对表示。\n"
            "3. 不要总结，不要改写，不要补充页面外的信息。\n"
            "4. 看不清或无法确认的文字用 [?] 标记，不要猜测。\n"
            "5. 只输出 OCR 结果本身。"
        )

    def _read_cached_qwen_pdf_ocr_text(self, path: Path) -> str:
        cache_path = self._qwen_pdf_ocr_cache_path(path)
        if not cache_path.exists():
            return ""
        try:
            return self._read_text_file(cache_path).strip()
        except Exception as e:
            logger.debug(f"读取 Qwen PDF OCR 缓存失败: {cache_path}, error={e}")
            return ""

    def _write_qwen_pdf_ocr_cache_text(self, path: Path, text: str) -> None:
        if not text.strip():
            return
        cache_path = self._qwen_pdf_ocr_cache_path(path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")

    def _qwen_pdf_ocr_cache_path(self, path: Path) -> Path:
        stem = "".join(char if char not in '\\/:*?"<>|' else "_" for char in path.stem)[:120]
        fingerprint = self._file_fingerprint(path)[:16]
        return self.parser_output_dir / "qwen_pdf_ocr" / f"{stem}_{fingerprint}.md"

    @staticmethod
    def _file_fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _normalise_llm_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        pieces.append(text)
                elif isinstance(item, str):
                    pieces.append(item)
            return "\n".join(pieces)
        return "" if content is None else str(content)

    def _extract_pdf_text_with_mineru(self, path: Path) -> str:
        """从 MinerU 输出中抽取 PDF 文本；必要时仅为当前附件触发一次解析。"""
        cached_text = self._read_cached_mineru_pdf_text(path)
        if cached_text.strip():
            logger.info(f"复用已存在的 MinerU PDF 解析结果: {path.name}")
            return cached_text

        if not config.chat_pdf_ocr_enabled:
            return ""

        mineru = shutil.which("mineru")
        if mineru is None:
            local_mineru = Path.cwd() / ".venv" / "bin" / "mineru"
            if local_mineru.exists():
                mineru = str(local_mineru)
        if mineru is None:
            raise RuntimeError("PDF 无文本层，且未找到 mineru 命令，无法执行 OCR 解析")

        self.parser_output_dir.mkdir(parents=True, exist_ok=True)
        method = getattr(config, "chat_pdf_ocr_method", "ocr").strip() or "ocr"
        backend = self._mineru_backend()
        cmd = [
            mineru,
            "-p",
            str(path),
            "-o",
            str(self.parser_output_dir),
            "-m",
            method,
            "-l",
            "ch",
        ]
        if backend:
            cmd.extend(["-b", backend])

        logger.info(
            f"PDF 无文本层，开始为当前对话附件执行 MinerU OCR: "
            f"{path.name}, method={method}, backend={backend or 'default'}"
        )
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=config.chat_pdf_ocr_timeout,
                check=False,
                encoding="utf-8",
                errors="ignore",
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"MinerU OCR 超时（{config.chat_pdf_ocr_timeout} 秒）: {path.name}"
            ) from e

        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                f"MinerU OCR 失败: {path.name}, returncode={result.returncode}, "
                f"stderr={stderr[-1000:]}"
            )

        parsed_text = self._read_cached_mineru_pdf_text(path)
        if not parsed_text.strip():
            raise RuntimeError(f"MinerU OCR 已完成，但未找到可用 Markdown 输出: {path.name}")
        return parsed_text

    def _read_cached_mineru_pdf_text(self, path: Path) -> str:
        """读取 RAG-Anything/MinerU 已生成的 Markdown 或 content_list 文本。"""
        if not self.parser_output_dir.exists():
            return ""

        markdown_text = self._read_cached_mineru_markdown(path)
        if markdown_text.strip():
            return markdown_text

        return self._read_cached_mineru_content_list(path)

    def _mineru_cache_candidates(self, path: Path, pattern: str) -> list[Path]:
        stem = path.stem
        candidates: list[Path] = []
        for candidate in self.parser_output_dir.rglob(pattern):
            if candidate.name.startswith("._"):
                continue
            parent_names = {parent.name for parent in candidate.parents}
            if candidate.stem == stem or any(stem in name for name in parent_names):
                candidates.append(candidate)
        return sorted(
            candidates,
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        )

    def _read_cached_mineru_markdown(self, path: Path) -> str:
        for candidate in self._mineru_cache_candidates(path, "*.md"):
            try:
                text = self._read_text_file(candidate).strip()
            except Exception as e:
                logger.debug(f"读取 MinerU Markdown 失败: {candidate}, error={e}")
                continue
            if text:
                return text
        return ""

    def _read_cached_mineru_content_list(self, path: Path) -> str:
        for candidate in self._mineru_cache_candidates(path, "*content_list*.json"):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception as e:
                logger.debug(f"读取 MinerU content_list 失败: {candidate}, error={e}")
                continue

            lines = self._content_list_to_text(data)
            if lines.strip():
                return lines
        return ""

    @classmethod
    def _content_list_to_text(cls, data: Any) -> str:
        chunks: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                text = value.get("text") or value.get("content")
                if isinstance(text, str) and text.strip():
                    page_idx = value.get("page_idx")
                    prefix = f"Page {page_idx}\n" if page_idx is not None else ""
                    chunks.append(prefix + text.strip())
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(data)
        return "\n\n".join(dict.fromkeys(chunks))

    @staticmethod
    def _extract_image_metadata_text(path: Path, ocr_error: str | None = None) -> str:
        lines = [
            f"图片文件: {path.name}",
            f"文件路径: {path}",
            f"图片主题: {path.stem}",
        ]

        try:
            from PIL import Image

            with Image.open(path) as image:
                lines.extend(
                    [
                        f"图片格式: {image.format or path.suffix.lstrip('.').upper()}",
                        f"图片尺寸: {image.width}x{image.height}",
                        f"图片模式: {image.mode}",
                    ]
                )
        except Exception as e:
            logger.debug(f"读取图片元数据失败: {path}, error={e}")

        if ocr_error:
            lines.append(f"OCR状态: Qwen 视觉 OCR 调用失败，已使用图片元数据兜底。错误: {ocr_error}")
        else:
            lines.append("OCR状态: 图片 OCR 已关闭，当前仅写入图片元数据。")
        return "\n".join(lines)

    @staticmethod
    def _image_data_url(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(path.name)
        if not mime_type:
            mime_type = "application/octet-stream"

        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @classmethod
    def _extract_modern_office_text(cls, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".docx":
            return cls._extract_docx_text(path)
        if suffix == ".pptx":
            return cls._extract_pptx_text(path)
        if suffix == ".xlsx":
            return cls._extract_xlsx_text(path)
        raise ValueError(f"不支持 Office 文本兜底解析: {path}")

    @classmethod
    def _extract_legacy_office_text(cls, path: Path) -> str:
        text = cls._extract_with_textutil(path)
        if text.strip():
            return text

        logger.warning(f"textutil 未能提取旧版 Office 文本，尝试 LibreOffice 转新版格式: {path}")
        return cls._extract_legacy_office_text_with_libreoffice(path)

    @staticmethod
    def _extract_with_textutil(path: Path) -> str:
        textutil = shutil.which("textutil")
        if textutil is None:
            return ""

        try:
            result = subprocess.run(
                [textutil, "-convert", "txt", "-stdout", str(path)],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except Exception as e:
            logger.warning(f"textutil 提取旧版 Office 文本失败: {path}, error={e}")
            return ""

        if result.returncode != 0:
            stderr = cls_output = result.stderr.decode("utf-8", errors="ignore")
            logger.warning(f"textutil 返回非 0: {path}, stderr={stderr or cls_output}")
            return ""

        for encoding in ("utf-8", "gbk", "latin-1", "cp1252"):
            try:
                return result.stdout.decode(encoding)
            except UnicodeDecodeError:
                continue
        return result.stdout.decode("utf-8", errors="ignore")

    @classmethod
    def _extract_legacy_office_text_with_libreoffice(cls, path: Path) -> str:
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice is None:
            raise RuntimeError("无法解析旧版 Office 文件：未找到 textutil 或 LibreOffice")

        target_ext_map = {
            ".doc": "docx",
            ".ppt": "pptx",
            ".xls": "xlsx",
        }
        target_ext = target_ext_map.get(path.suffix.lower())
        if target_ext is None:
            raise RuntimeError(f"不支持旧版 Office 文件类型: {path.suffix}")

        with tempfile.TemporaryDirectory() as output_dir, tempfile.TemporaryDirectory() as profile_dir:
            cmd = [
                soffice,
                "--headless",
                "--nologo",
                "--nofirststartwizard",
                "--nodefault",
                "--nolockcheck",
                f"-env:UserInstallation={Path(profile_dir).resolve().as_uri()}",
                "--convert-to",
                target_ext,
                "--outdir",
                output_dir,
                str(path),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                encoding="utf-8",
                errors="ignore",
            )
            converted_files = list(Path(output_dir).glob(f"*.{target_ext}"))
            if result.returncode != 0 or not converted_files:
                raise RuntimeError(
                    f"LibreOffice 转换旧版 Office 文件失败: {path.name}, "
                    f"returncode={result.returncode}, stderr={result.stderr.strip()}"
                )

            return cls._extract_modern_office_text(converted_files[0])

    @classmethod
    def _extract_docx_text(cls, path: Path) -> str:
        paragraph_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        text_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
        tab_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tab"
        break_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br"
        carriage_return_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}cr"

        document_parts = ["word/document.xml"]
        optional_prefixes = (
            "word/header",
            "word/footer",
            "word/footnotes",
            "word/endnotes",
            "word/comments",
        )

        paragraphs: list[str] = []
        with zipfile.ZipFile(path) as archive:
            part_names = archive.namelist()
            for name in sorted(part_names):
                if name != "word/document.xml" and not name.startswith(optional_prefixes):
                    continue
                if not name.endswith(".xml") or name not in part_names:
                    continue
                if name not in document_parts:
                    document_parts.append(name)

            for part in document_parts:
                try:
                    root = ElementTree.fromstring(archive.read(part))
                except Exception as e:
                    logger.debug(f"跳过无法解析的 docx XML: {part}, error={e}")
                    continue

                for paragraph in root.iter(paragraph_tag):
                    pieces: list[str] = []
                    for element in paragraph.iter():
                        if element.tag == text_tag and element.text:
                            pieces.append(element.text)
                        elif element.tag == tab_tag:
                            pieces.append("\t")
                        elif element.tag in {break_tag, carriage_return_tag}:
                            pieces.append("\n")
                    text = "".join(pieces).strip()
                    if text:
                        paragraphs.append(text)

        return "\n".join(paragraphs)

    @staticmethod
    def _extract_pptx_text(path: Path) -> str:
        text_tag = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
        slides: list[str] = []

        with zipfile.ZipFile(path) as archive:
            slide_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            for index, name in enumerate(slide_names, start=1):
                try:
                    root = ElementTree.fromstring(archive.read(name))
                except Exception as e:
                    logger.debug(f"跳过无法解析的 pptx XML: {name}, error={e}")
                    continue

                text = "\n".join(
                    element.text.strip()
                    for element in root.iter(text_tag)
                    if element.text and element.text.strip()
                )
                if text:
                    slides.append(f"Slide {index}\n{text}")

        return "\n\n".join(slides)

    @classmethod
    def _extract_xlsx_text(cls, path: Path) -> str:
        spreadsheet_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        row_tag = f"{spreadsheet_ns}row"
        cell_tag = f"{spreadsheet_ns}c"
        value_tag = f"{spreadsheet_ns}v"
        text_tag = f"{spreadsheet_ns}t"

        with zipfile.ZipFile(path) as archive:
            shared_strings = cls._read_xlsx_shared_strings(archive, text_tag)
            sheet_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )

            sheets: list[str] = []
            for sheet_index, name in enumerate(sheet_names, start=1):
                try:
                    root = ElementTree.fromstring(archive.read(name))
                except Exception as e:
                    logger.debug(f"跳过无法解析的 xlsx XML: {name}, error={e}")
                    continue

                rows: list[str] = []
                for row in root.iter(row_tag):
                    values: list[str] = []
                    for cell in row.iter(cell_tag):
                        values.append(
                            cls._read_xlsx_cell_value(
                                cell,
                                value_tag=value_tag,
                                text_tag=text_tag,
                                shared_strings=shared_strings,
                            )
                        )
                    row_text = "\t".join(value for value in values if value)
                    if row_text:
                        rows.append(row_text)

                if rows:
                    sheets.append(f"Sheet {sheet_index}\n" + "\n".join(rows))

        return "\n\n".join(sheets)

    @staticmethod
    def _read_xlsx_shared_strings(
        archive: zipfile.ZipFile,
        text_tag: str,
    ) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []

        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        shared_strings: list[str] = []
        for item in root:
            text = "".join(
                element.text or ""
                for element in item.iter(text_tag)
            ).strip()
            shared_strings.append(text)
        return shared_strings

    @staticmethod
    def _read_xlsx_cell_value(
        cell: ElementTree.Element,
        value_tag: str,
        text_tag: str,
        shared_strings: list[str],
    ) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return "".join(element.text or "" for element in cell.iter(text_tag)).strip()

        value_element = cell.find(value_tag)
        if value_element is None or value_element.text is None:
            return ""

        raw_value = value_element.text.strip()
        if cell_type == "s":
            try:
                return shared_strings[int(raw_value)]
            except (ValueError, IndexError):
                return raw_value

        return raw_value

    @staticmethod
    def _doc_id(path: Path) -> str:
        normalized = path.as_posix()
        return "doc-" + hashlib.md5(normalized.encode("utf-8")).hexdigest()


rag_anything_service = RAGAnythingService()
