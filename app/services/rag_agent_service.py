"""RAG Agent 服务 - 基于 LangGraph 的智能代理

使用 langchain_qwq 的 ChatQwen 原生集成，
支持真正的流式输出和更好的模型适配。
"""

import base64
import mimetypes
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator, Dict, Sequence

from langchain.agents import create_agent
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from loguru import logger
from typing_extensions import TypedDict
from langchain_qwq import ChatQwen

from app.config import config
from app.models.request import ChatAttachment
from app.services.rag_anything_service import IMAGE_EXTENSIONS
from app.tools import get_current_time, retrieve_knowledge
from app.agent.mcp_client import get_mcp_client_with_retry

# 阿里千问大模型和langchain集成参考： https://docs.langchain.com/oss/python/integrations/chat/qwen
# 注意：需要配置环境变量 DASHSCOPE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1 否则默认访问的是新加坡站点
# 同时也需要配置环境变量 DASHSCOPE_API_KEY=your_api_key


class AgentState(TypedDict):
    """Agent 状态"""
    messages: Annotated[Sequence[BaseMessage], add_messages]


def trim_messages_middleware(state: AgentState) -> dict[str, Any] | None:
    """
    修剪消息历史，只保留最近的几条消息以适应上下文窗口

    策略：
    - 保留第一条系统消息（System Message）
    - 保留最近的 6 条消息（3 轮对话）
    - 当消息少于等于 7 条时，不做修剪

    Args:
        state: Agent 状态

    Returns:
        包含修剪后消息的字典，如果无需修剪则返回 None
    """
    messages = state["messages"]

    # 如果消息数量较少，无需修剪
    if len(messages) <= 7:
        return None

    # 提取第一条系统消息
    first_msg = messages[0]

    # 保留最近的 6 条消息（确保包含完整的对话轮次）
    recent_messages = messages[-6:] if len(messages) % 2 == 0 else messages[-7:]

    # 构建新的消息列表
    new_messages = [first_msg] + list(recent_messages)

    logger.debug(f"修剪消息历史: {len(messages)} -> {len(new_messages)} 条")

    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *new_messages
        ]
    }


class RagAgentService:
    """RAG Agent 服务 - 使用 LangGraph + ChatQwen 原生集成"""

    def __init__(self, streaming: bool = True):
        """初始化 RAG Agent 服务

        Args:
            streaming: 是否启用流式输出，默认为 True
        """
        self.model_name = config.rag_model
        self.vision_model_name = config.rag_vision_model
        self.streaming = streaming
        self.system_prompt = self._build_system_prompt()


        self.model = ChatQwen(
            model=self.model_name,
            api_key=config.dashscope_api_key,
            base_url=config.dashscope_api_base,
            temperature=0.7,
            streaming=streaming,
        )
        self.vision_model = ChatQwen(
            model=self.vision_model_name,
            api_key=config.dashscope_api_key,
            base_url=config.dashscope_api_base,
            temperature=0.7,
            streaming=streaming,
        )

        # 定义基础工具
        self.tools = [retrieve_knowledge, get_current_time]

        # MCP 客户端（延迟初始化，使用全局管理）
        self.mcp_tools: list = []

        # 创建内存检查点（用于会话管理）
        self.checkpointer = MemorySaver()
        self.session_attachment_contexts: dict[str, str] = {}
        self.session_image_attachments: dict[str, list[ChatAttachment]] = {}

        # Agent 初始化（会在异步方法中完成）
        self.agent = None
        self.vision_agent = None
        self._agent_initialized = False

        logger.info(
            "RAG Agent 服务初始化完成 (ChatQwen), "
            f"model={self.model_name}, vision_model={self.vision_model_name}, streaming={streaming}"
        )

    def update_session_attachments(
        self,
        session_id: str,
        attachment_context: str | None,
        attachments: list[ChatAttachment],
    ) -> None:
        """记录当前会话最近一次上传的附件上下文，供后续轮次复用。"""
        if not attachments:
            return

        if attachment_context:
            self.session_attachment_contexts[session_id] = attachment_context

        image_attachments = [
            attachment
            for attachment in attachments
            if self._is_image_attachment(attachment)
        ]
        if image_attachments:
            self.session_image_attachments[session_id] = image_attachments
        else:
            self.session_image_attachments.pop(session_id, None)

        logger.info(
            f"[会话 {session_id}] 已更新活动附件上下文: "
            f"附件数={len(attachments)}, 图片数={len(image_attachments)}"
        )

    def get_session_attachment_context(self, session_id: str) -> str | None:
        """获取会话最近一次上传的附件上下文。"""
        return self.session_attachment_contexts.get(session_id)

    def get_session_image_attachments(self, session_id: str) -> list[ChatAttachment]:
        """获取会话最近一次上传的图片附件。"""
        return self.session_image_attachments.get(session_id, [])

    async def _initialize_agent(self):
        """异步初始化 Agent（包括 MCP 工具）"""
        if self._agent_initialized:
            return

        # 使用全局 MCP 客户端管理器（带重试拦截器）
        mcp_client = await get_mcp_client_with_retry()

        # 获取 MCP 工具
        mcp_tools = await mcp_client.get_tools()
        logger.info(f"成功加载 {len(mcp_tools)} 个 MCP 工具")

        # 将 MCP 工具添加到实例变量中
        self.mcp_tools = mcp_tools

        # 合并所有工具
        all_tools = self.tools + self.mcp_tools

        self.agent = create_agent(
            self.model,
            tools=all_tools,
            checkpointer=self.checkpointer,
        )
        self.vision_agent = create_agent(
            self.vision_model,
            tools=all_tools,
            checkpointer=self.checkpointer,
        )

        self._agent_initialized = True


        if all_tools:
            tool_names = [tool.name if hasattr(tool, "name") else str(tool) for tool in all_tools]
            logger.info(f"可用工具列表: {', '.join(tool_names)}")

    def _build_system_prompt(self) -> str:
        """
        构建系统提示词

        注意：LangChain 框架会自动将工具信息传递给 LLM，
        因此系统提示词中无需列举具体的工具列表。

        Returns:
            str: 系统提示词
        """
        from textwrap import dedent

        return dedent("""
            你是一个专业的AI助手，能够使用多种工具来帮助用户解决问题。

            工作原则:
            1. 理解用户需求，选择合适的工具来完成任务
            2. 当需要获取实时信息或专业知识时，主动使用相关工具
            3. 基于工具返回的结果提供准确、专业的回答
            4. 如果工具无法提供足够信息，请诚实地告知用户

            回答要求:
            - 保持友好、专业的语气
            - 回答简洁明了，重点突出
            - 基于事实，不编造信息
            - 如有不确定的地方，明确说明

            请根据用户的问题，灵活使用可用工具，提供高质量的帮助。
        """).strip()

    async def query(
        self,
        question: str,
        session_id: str,
        attachment_context: str | None = None,
    ) -> str:
        """
        非流式处理用户问题（一次性返回完整答案）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）
            attachment_context: 当前轮上传附件的检索上下文

        Returns:
            str: 完整答案
        """
        try:
            await self._initialize_agent()

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（非流式）: {question}")

            # 构建消息列表（系统提示 + 可选附件上下文 + 用户问题）
            messages = [
                SystemMessage(content=self.system_prompt),
            ]
            if attachment_context:
                messages.append(SystemMessage(content=attachment_context))
            messages.append(HumanMessage(content=question))

            # 构建 Agent 输入
            agent_input = {"messages": messages}

            # 配置 thread_id（用于会话持久化）
            config_dict = {
                "configurable": {
                    "thread_id": session_id
                }
            }

            result = await self.agent.ainvoke(
                input=agent_input,
                config=config_dict,
            )

            # 提取最终答案
            messages_result = result.get("messages", [])
            if messages_result:
                last_message = messages_result[-1]
                answer = last_message.content if hasattr(last_message, 'content') else str(last_message)

                # 记录工具调用
                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    tool_names = [tc.get("name", "unknown") for tc in last_message.tool_calls]
                    logger.info(f"[会话 {session_id}] Agent 调用了工具: {tool_names}")

                logger.info(f"[会话 {session_id}] RAG Agent 查询完成（非流式）")
                return self._message_content_to_text(answer)

            logger.warning(f"[会话 {session_id}] Agent 返回结果为空")
            return ""

        except Exception as e:
            logger.error(f"[会话 {session_id}] RAG Agent 查询失败（非流式）: {e}")
            raise

    async def query_with_image_attachments(
        self,
        question: str,
        session_id: str,
        attachments: list[ChatAttachment],
        attachment_context: str | None = None,
    ) -> str:
        """使用视觉模型和完整 Agent 能力处理图片附件。"""
        try:
            await self._initialize_agent()
            assert self.vision_agent is not None

            logger.info(
                f"[会话 {session_id}] 视觉 Agent 收到图片附件查询（非流式）: "
                f"{question}, model={self.vision_model_name}"
            )

            messages = self._build_agent_messages(
                question=question,
                attachment_context=attachment_context,
                attachments=attachments,
            )
            agent_input = {"messages": messages}
            config_dict = {"configurable": {"thread_id": session_id}}

            result = await self.vision_agent.ainvoke(
                input=agent_input,
                config=config_dict,
            )

            messages_result = result.get("messages", [])
            if messages_result:
                last_message = messages_result[-1]
                answer = last_message.content if hasattr(last_message, "content") else str(last_message)
                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    tool_names = [tc.get("name", "unknown") for tc in last_message.tool_calls]
                    logger.info(f"[会话 {session_id}] 视觉 Agent 调用了工具: {tool_names}")
                logger.info(f"[会话 {session_id}] 视觉 Agent 查询完成（非流式）")
                return self._message_content_to_text(answer)

            logger.warning(f"[会话 {session_id}] 视觉 Agent 返回结果为空")
            return ""
        except Exception as e:
            logger.error(f"[会话 {session_id}] 视觉 Agent 查询失败（非流式）: {e}")
            raise

    async def query_with_attachment_context(
        self,
        question: str,
        session_id: str,
        attachment_context: str,
    ) -> str:
        """直接基于当前对话附件内容回答，不初始化 RAG/MCP Agent。"""
        try:
            logger.info(f"[会话 {session_id}] 附件直读查询（非流式）: {question}")
            messages = [
                SystemMessage(content=self.system_prompt),
                SystemMessage(content=attachment_context),
                HumanMessage(content=question),
            ]
            response = await self.model.ainvoke(messages)
            answer = response.content if hasattr(response, "content") else str(response)
            return answer if isinstance(answer, str) else str(answer)
        except Exception as e:
            logger.error(f"[会话 {session_id}] 附件直读查询失败（非流式）: {e}")
            raise

    async def query_image_attachments_stream(
        self,
        question: str,
        session_id: str,
        attachments: list[ChatAttachment],
        attachment_context: str | None = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式使用视觉模型和完整 Agent 能力处理图片附件。"""
        try:
            await self._initialize_agent()
            assert self.vision_agent is not None

            logger.info(
                f"[会话 {session_id}] 视觉 Agent 收到图片附件查询（流式）: "
                f"{question}, model={self.vision_model_name}"
            )

            messages = self._build_agent_messages(
                question=question,
                attachment_context=attachment_context,
                attachments=attachments,
            )
            agent_input = {"messages": messages}
            config_dict = {"configurable": {"thread_id": session_id}}

            async for token, metadata in self.vision_agent.astream(
                input=agent_input,
                config=config_dict,
                stream_mode="messages",
            ):
                node_name = metadata.get("langgraph_node", "unknown") if isinstance(metadata, dict) else "unknown"
                message_type = type(token).__name__

                if message_type in ("AIMessage", "AIMessageChunk"):
                    content_blocks = getattr(token, "content_blocks", None)
                    if content_blocks and isinstance(content_blocks, list):
                        for block in content_blocks:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_content = block.get("text", "")
                                if text_content:
                                    yield {
                                        "type": "content",
                                        "data": text_content,
                                        "node": node_name,
                                    }
                        continue

                    content = getattr(token, "content", "")
                    if isinstance(content, str) and content:
                        yield {
                            "type": "content",
                            "data": content,
                            "node": node_name,
                        }

            logger.info(f"[会话 {session_id}] 视觉 Agent 查询完成（流式）")
            yield {"type": "complete"}

        except Exception as e:
            logger.error(f"[会话 {session_id}] 视觉 Agent 查询失败（流式）: {e}")
            yield {
                "type": "error",
                "data": str(e),
            }
            raise

    async def query_stream(
        self,
        question: str,
        session_id: str,
        attachment_context: str | None = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式处理用户问题（逐步返回答案片段）

        Args:
            question: 用户问题
            session_id: 会话ID（作为 thread_id）
            attachment_context: 当前轮上传附件的检索上下文

        Yields:
            Dict[str, Any]: 包含流式数据的字典
                - type: "content" | "tool_call" | "complete" | "error"
                - data: 具体内容
        """
        try:
            await self._initialize_agent()

            logger.info(f"[会话 {session_id}] RAG Agent 收到查询（流式）: {question}")

            # 构建消息列表（系统提示 + 可选附件上下文 + 用户问题）
            messages = [
                SystemMessage(content=self.system_prompt),
            ]
            if attachment_context:
                messages.append(SystemMessage(content=attachment_context))
            messages.append(HumanMessage(content=question))

            # 构建 Agent 输入
            agent_input = {"messages": messages}

            # 配置 thread_id（用于会话持久化）
            config_dict = {
                "configurable": {
                    "thread_id": session_id
                }
            }

            async for token, metadata in self.agent.astream(
                input=agent_input,
                config=config_dict,
                stream_mode="messages",
            ):
                node_name = metadata.get('langgraph_node', 'unknown') if isinstance(metadata, dict) else 'unknown'
                message_type = type(token).__name__

                if message_type in ("AIMessage", "AIMessageChunk"):
                    content_blocks = getattr(token, 'content_blocks', None)

                    if content_blocks and isinstance(content_blocks, list):
                        for block in content_blocks:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                text_content = block.get('text', '')
                                if text_content:
                                    yield {
                                        "type": "content",
                                        "data": text_content,
                                        "node": node_name
                                    }

            logger.info(f"[会话 {session_id}] RAG Agent 查询完成（流式）")
            yield {"type": "complete"}

        except Exception as e:
            logger.error(f"[会话 {session_id}] RAG Agent 查询失败（流式）: {e}")
            yield {
                "type": "error",
                "data": str(e)
            }
            raise

    async def query_attachment_context_stream(
        self,
        question: str,
        session_id: str,
        attachment_context: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式直接读取当前对话附件内容，不初始化 RAG/MCP Agent。"""
        try:
            logger.info(f"[会话 {session_id}] 附件直读查询（流式）: {question}")
            messages = [
                SystemMessage(content=self.system_prompt),
                SystemMessage(content=attachment_context),
                HumanMessage(content=question),
            ]

            async for token in self.model.astream(messages):
                content_blocks = getattr(token, "content_blocks", None)
                if content_blocks and isinstance(content_blocks, list):
                    for block in content_blocks:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_content = block.get("text", "")
                            if text_content:
                                yield {"type": "content", "data": text_content, "node": "attachment_context"}
                    continue

                content = getattr(token, "content", "")
                if isinstance(content, str) and content:
                    yield {"type": "content", "data": content, "node": "attachment_context"}

            logger.info(f"[会话 {session_id}] 附件直读查询完成（流式）")
            yield {"type": "complete"}

        except Exception as e:
            logger.error(f"[会话 {session_id}] 附件直读查询失败（流式）: {e}")
            yield {
                "type": "error",
                "data": str(e)
            }
            raise

    def get_session_history(self, session_id: str) -> list:
        """
        获取会话历史（从 MemorySaver checkpointer 中读取）

        Args:
            session_id: 会话ID（即 thread_id）

        Returns:
            list: 消息历史列表 [{"role": "user|assistant", "content": "...", "timestamp": "..."}]
        """
        try:
            # 使用 checkpointer 的 get 方法获取最新的检查点
            config = {"configurable": {"thread_id": session_id}}
            
            # 获取该 thread 的最新检查点
            checkpoint_tuple = self.checkpointer.get(config)
            
            if not checkpoint_tuple:
                logger.info(f"获取会话历史: {session_id}, 消息数量: 0")
                return []
            
            # checkpoint_tuple 可能是命名元组或普通元组，安全地提取 checkpoint
            # 通常第一个元素是 checkpoint 数据
            if hasattr(checkpoint_tuple, 'checkpoint'):
                checkpoint_data = checkpoint_tuple.checkpoint  # type: ignore
            else:
                # 如果是普通元组，第一个元素是 checkpoint
                checkpoint_data = checkpoint_tuple[0] if checkpoint_tuple else {}
            
            # 从检查点中提取消息
            messages = checkpoint_data.get("channel_values", {}).get("messages", [])
            
            # 转换为前端需要的格式
            history = []
            for msg in messages:
                # 跳过系统消息
                if isinstance(msg, SystemMessage):
                    continue
                    
                role = "user" if isinstance(msg, HumanMessage) else "assistant"
                content = msg.content if hasattr(msg, 'content') else str(msg)
                
                # 提取时间戳（如果有的话）
                timestamp = getattr(msg, 'timestamp', None)
                if timestamp:
                    history.append({
                        "role": role,
                        "content": content,
                        "timestamp": timestamp
                    })
                else:
                    from datetime import datetime
                    history.append({
                        "role": role,
                        "content": content,
                        "timestamp": datetime.now().isoformat()
                    })
            
            logger.info(f"获取会话历史: {session_id}, 消息数量: {len(history)}")
            return history
            
        except Exception as e:
            logger.error(f"获取会话历史失败: {session_id}, 错误: {e}")
            return []

    def clear_session(self, session_id: str) -> bool:
        """
        清空会话历史（从 MemorySaver checkpointer 中删除）

        Args:
            session_id: 会话ID（即 thread_id）

        Returns:
            bool: 是否成功
        """
        try:
            # 使用 checkpointer 的 delete_thread 方法删除该 thread 的所有检查点
            self.checkpointer.delete_thread(session_id)
            self.session_attachment_contexts.pop(session_id, None)
            self.session_image_attachments.pop(session_id, None)
            
            logger.info(f"已清除会话历史: {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"清空会话历史失败: {session_id}, 错误: {e}")
            return False

    def _build_agent_messages(
        self,
        question: str,
        attachment_context: str | None = None,
        attachments: list[ChatAttachment] | None = None,
    ) -> list[BaseMessage]:
        """构建 Agent 消息；图片附件会进入同一条多模态用户消息。"""
        messages: list[BaseMessage] = [SystemMessage(content=self.system_prompt)]
        if attachment_context:
            messages.append(SystemMessage(content=attachment_context))

        user_content = self._build_multimodal_user_content(question, attachments or [])
        messages.append(HumanMessage(content=user_content))
        return messages

    def _build_multimodal_user_content(
        self,
        question: str,
        attachments: list[ChatAttachment],
    ) -> str | list[dict[str, Any]]:
        """把图片附件编码为 OpenAI 兼容的 image_url content blocks。"""
        image_blocks: list[dict[str, Any]] = []
        for index, attachment in enumerate(attachments, start=1):
            if not self._is_image_attachment(attachment):
                continue

            image_path = self._resolve_uploaded_attachment_path(attachment)
            if image_path is None:
                logger.warning(
                    f"跳过无法读取的图片附件: filename={attachment.filename}, "
                    f"path={attachment.file_path}"
                )
                continue

            image_blocks.extend(
                [
                    {
                        "type": "text",
                        "text": f"图片附件 {index}: {attachment.filename}",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": self._image_data_url(image_path)},
                        "min_pixels": 256 * 256,
                        "max_pixels": 32 * 32 * 8192,
                    },
                ]
            )

        if not image_blocks:
            return question

        prompt = (
            f"{question}\n\n"
            "请直接查看并理解随后的图片附件，同时结合当前对话附件文本、RAG 检索结果和可用工具回答。"
            "如果需要知识库、时间或运维信息，请照常调用工具；不要因为有图片附件而跳过工具调用。"
        )
        return [{"type": "text", "text": prompt}, *image_blocks]

    @staticmethod
    def _is_image_attachment(attachment: ChatAttachment) -> bool:
        content_type = (attachment.content_type or "").lower()
        suffix = Path(attachment.filename or attachment.file_path).suffix.lower()
        return content_type.startswith("image/") or suffix in IMAGE_EXTENSIONS

    @staticmethod
    def _resolve_uploaded_attachment_path(attachment: ChatAttachment) -> Path | None:
        raw_path = Path(attachment.file_path)
        candidate = raw_path if raw_path.is_absolute() else Path.cwd() / raw_path
        try:
            resolved = candidate.resolve(strict=True)
            upload_root = Path.cwd().joinpath("uploads").resolve(strict=True)
        except OSError:
            return None

        if not resolved.is_file() or not resolved.is_relative_to(upload_root):
            return None
        return resolved

    @staticmethod
    def _image_data_url(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(path.name)
        if not mime_type:
            mime_type = "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _message_content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
                elif isinstance(block, str):
                    text_parts.append(block)
            if text_parts:
                return "".join(text_parts)
        return str(content)

    async def cleanup(self):
        """清理资源"""
        try:
            logger.info("清理 RAG Agent 服务资源...")
            # MCP 客户端由全局管理器统一管理，无需手动清理
            logger.info("RAG Agent 服务资源已清理")
        except Exception as e:
            logger.error(f"清理资源失败: {e}")


# 全局单例 - 启用流式输出
rag_agent_service = RagAgentService(streaming=True)
