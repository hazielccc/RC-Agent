"""本地 BGE-M3 MLX embedding 服务."""

import asyncio
import threading
from pathlib import Path
from typing import Sequence

import numpy as np
from langchain_core.embeddings import Embeddings
from loguru import logger

from app.config import config


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class BgeM3MlxEmbeddings(Embeddings):
    """使用本地 MLX 版 BGE-M3 生成 1024 维向量."""

    def __init__(
        self,
        model_path: str,
        model_name: str = "bge-m3-mlx",
        dimension: int = 1024,
        max_tokens: int = 8192,
        batch_size: int = 8,
    ) -> None:
        self.model_path = self._resolve_path(model_path)
        self.model_name = model_name
        self.dimension = dimension
        self.max_tokens = max_tokens
        self.batch_size = max(1, batch_size)
        self._model = None
        self._processor = None
        self._generate = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    @staticmethod
    def _resolve_path(path: str) -> Path:
        model_path = Path(path).expanduser()
        if not model_path.is_absolute():
            model_path = PROJECT_ROOT / model_path
        return model_path.resolve()

    def _ensure_loaded(self) -> None:
        if self._model is not None and self._processor is not None and self._generate is not None:
            return

        with self._load_lock:
            if self._model is not None and self._processor is not None and self._generate is not None:
                return

            if not self.model_path.exists():
                raise RuntimeError(
                    f"本地 embedding 模型未下载: {self.model_path}。"
                    "请先执行 make download-embedding-model。"
                )

            try:
                from mlx_embeddings import generate, load
            except Exception as e:
                raise RuntimeError(
                    "无法加载 MLX embedding 运行库。"
                    "请确认当前主机是 Apple Silicon，并且终端/服务进程可以访问 Metal。"
                ) from e

            logger.info(
                f"加载本地 Embedding 模型: {self.model_name}, path={self.model_path}"
            )
            self._model, self._processor = load(str(self.model_path))
            self._generate = generate
            logger.info(
                f"本地 Embedding 模型加载完成: {self.model_name}, 维度={self.dimension}"
            )

    def embed_numpy(self, texts: Sequence[str]) -> np.ndarray:
        """返回 LightRAG 需要的 numpy 向量矩阵."""
        clean_texts = [text if text is not None else "" for text in texts]
        if not clean_texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        arrays: list[np.ndarray] = []
        for start in range(0, len(clean_texts), self.batch_size):
            batch = clean_texts[start : start + self.batch_size]
            arrays.append(self._embed_batch_numpy(batch))

        embeddings = np.vstack(arrays).astype(np.float32, copy=False)
        if embeddings.shape[1] != self.dimension:
            raise RuntimeError(
                f"本地 embedding 维度不匹配: got={embeddings.shape[1]}, expected={self.dimension}"
            )
        return embeddings

    async def aembed_numpy(self, texts: Sequence[str]) -> np.ndarray:
        return await asyncio.to_thread(self.embed_numpy, list(texts))

    def _embed_batch_numpy(self, texts: Sequence[str]) -> np.ndarray:
        self._ensure_loaded()
        assert self._model is not None
        assert self._processor is not None
        assert self._generate is not None

        with self._infer_lock:
            outputs = self._generate(
                self._model,
                self._processor,
                list(texts),
                max_length=self.max_tokens,
                padding=True,
                truncation=True,
            )

            embeddings = getattr(outputs, "text_embeds", outputs)
            if embeddings is None:
                embeddings = getattr(outputs, "pooler_output", None)
            if embeddings is None:
                raise RuntimeError("本地 embedding 模型未返回 text_embeds")

            try:
                import mlx.core as mx

                mx.eval(embeddings)
            except Exception as e:
                raise RuntimeError("MLX embedding 推理失败") from e

            array = np.asarray(embeddings, dtype=np.float32)

        if array.ndim == 1:
            array = array.reshape(1, -1)
        return array

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        logger.info(f"本地 BGE-M3 MLX 批量嵌入 {len(texts)} 个文档")
        return self.embed_numpy(texts).tolist()

    def embed_query(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")
        return self.embed_numpy([text])[0].tolist()


local_embedding_service = BgeM3MlxEmbeddings(
    model_path=config.local_embedding_model_path,
    model_name=config.local_embedding_model_name,
    dimension=config.local_embedding_dim,
    max_tokens=config.local_embedding_max_tokens,
    batch_size=config.local_embedding_batch_size,
)


async def embed_texts_with_bge_m3_mlx(texts: Sequence[str]) -> np.ndarray:
    """RAG-Anything 使用的模块级 embedding 函数，避免序列化绑定对象。"""
    return await local_embedding_service.aembed_numpy(texts)
