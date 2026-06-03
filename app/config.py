"""配置管理模块

使用 Pydantic Settings 实现类型安全的配置管理
"""

from typing import Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用配置
    app_name: str = "SuperBizAgent"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 9900
    public_access_token: str = ""

    # DashScope 配置
    dashscope_api_key: str = ""  # 默认空字符串，实际使用需从环境变量加载
    dashscope_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen-max"
    dashscope_embedding_model: str = "text-embedding-v4"  # v4 支持多种维度（默认 1024）

    # 本地 Embedding 配置
    embedding_provider: str = "bge-m3-mlx"
    local_embedding_model_name: str = "bge-m3-mlx"
    local_embedding_model_repo: str = "mlx-community/bge-m3-mlx-8bit"
    local_embedding_model_path: str = "./models/bge-m3-mlx-8bit"
    local_embedding_dim: int = 1024
    local_embedding_max_tokens: int = 8192
    local_embedding_batch_size: int = 8

    # 小米 MiMo 配置（OpenAI 兼容模式）
    mimo_api_key: str = ""
    mimo_api_base: str = "https://api.xiaomimimo.com/v1"
    mimo_model: str = "mimo-v2.5"

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 3
    rag_model: str = "qwen-max"  # 使用快速响应模型，不带扩展思考
    rag_vision_model: str = "qwen3.6-plus"  # 图片附件使用的原生视觉语言模型
    local_knowledge_dir: str = "./my_knowledge_document"
    rag_anything_working_dir: str = "./rag_anything_storage"
    rag_anything_parser_output_dir: str = "./rag_anything_output"
    rag_anything_parser: str = "mineru"
    rag_anything_parse_method: str = "auto"
    rag_anything_mineru_backend: str = "pipeline"
    rag_anything_llm_model: str = "mimo-v2.5"
    rag_anything_llm_api_key: str = ""
    rag_anything_llm_api_base: str = ""
    rag_anything_llm_max_async: int = 1
    rag_anything_image_ocr_enabled: bool = True
    rag_anything_image_ocr_model: str = "qwen-vl-ocr-latest"
    rag_anything_image_ocr_timeout: int = 120
    rag_anything_image_ocr_max_tokens: int = 4096
    rag_anything_query_mode: str = "hybrid"
    rag_update_mode_header_value: str = "make-upload"
    chat_attachment_context_max_chars: int = 30000
    chat_pdf_ocr_enabled: bool = True
    chat_pdf_ocr_timeout: int = 900
    chat_pdf_ocr_model: str = "qwen3.6-plus"
    chat_pdf_ocr_concurrency: int = 4
    chat_pdf_ocr_page_timeout: int = 120
    chat_pdf_ocr_max_tokens: int = 4096
    chat_pdf_ocr_render_dpi: int = 160
    chat_pdf_ocr_max_pixels: int = 32 * 32 * 8192
    chat_pdf_ocr_jpeg_quality: int = 90

    # 文档分块配置
    chunk_max_size: int = 800
    chunk_overlap: int = 100

    # MCP 服务配置
    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"

    @property
    def mcp_servers(self) -> Dict[str, Dict[str, Any]]:
        """获取完整的 MCP 服务器配置"""
        return {
            "cls": {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            },
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            }
        }


# 全局配置实例
config = Settings()
