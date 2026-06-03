"""请求数据模型

定义 API 请求的 Pydantic 模型
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class ChatAttachment(BaseModel):
    """当前对话上传的附件信息"""

    filename: str = Field(..., description="附件文件名")
    file_path: str = Field(..., description="服务端保存路径")
    size: Optional[int] = Field(None, description="文件大小")
    doc_id: Optional[str] = Field(None, description="知识库文档 ID")
    indexed: bool = Field(True, description="是否已成功写入知识库")
    index_skipped: bool = Field(False, description="是否按策略跳过知识库写入")
    index_error: Optional[str] = Field(None, description="索引失败原因")
    extracted_text: Optional[str] = Field(None, description="当前对话附件抽取文本")
    text_truncated: bool = Field(False, description="当前对话附件文本是否被截断")
    extraction_error: Optional[str] = Field(None, description="当前对话附件文本抽取失败原因")
    content_type: Optional[str] = Field(None, description="浏览器上报的文件类型")

    class Config:
        populate_by_name = True


class ChatRequest(BaseModel):
    """对话请求"""

    id: str = Field(..., description="会话 ID", alias="Id")
    question: str = Field(..., description="用户问题", alias="Question")
    attachments: List[ChatAttachment] = Field(
        default_factory=list,
        description="当前对话附件",
        alias="Attachments",
    )

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "Id": "session-123",
                "Question": "请总结我刚上传的文件",
                "Attachments": [
                    {
                        "filename": "example.pdf",
                        "file_path": "uploads/example.pdf",
                        "size": 1024,
                        "doc_id": "doc-xxx",
                        "indexed": True,
                    }
                ],
            }
        }


class ClearRequest(BaseModel):
    """清空会话请求"""

    session_id: str = Field(..., description="会话 ID", alias="sessionId")

    class Config:
        populate_by_name = True
