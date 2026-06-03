# SuperBizAgent Python 版本 Makefile
# 用于自动化项目初始化、Docker 管理和文档索引

# ============================================================
# 配置变量
# ============================================================
SERVER_URL = http://localhost:9900
UPLOAD_API = $(SERVER_URL)/api/upload
HEALTH_CHECK_API = $(SERVER_URL)/health
BIND_HOST = 0.0.0.0
PORT = 9900
DOCS_DIR = aiops-docs
KNOWLEDGE_DIR = my_knowledge_document
INDEX_TIMEOUT = 9000
UPLOAD_BUILTIN_DOCS ?= 0
UPLOAD_FORCE ?= 0
UPLOAD_ROOTS ?=
UPLOAD_TITLE ?= 新增/变更文档
MILVUS_CONTAINER = milvus-standalone
RAG_UPDATE_HEADER = X-RAG-Update-Mode
RAG_UPDATE_MODE = make-upload
PUBLIC_TUNNEL_LOG = public_tunnel.log
CLOUDFLARED = $(shell command -v cloudflared 2>/dev/null || \
	if [ -x /opt/homebrew/bin/cloudflared ]; then echo /opt/homebrew/bin/cloudflared; \
	elif [ -x /usr/local/bin/cloudflared ]; then echo /usr/local/bin/cloudflared; fi)

# 颜色输出
GREEN = \033[0;32m
YELLOW = \033[0;33m
RED = \033[0;31m
CYAN = \033[0;36m
NC = \033[0m

.PHONY: help init start stop restart check upload upload-builtin-docs upload-builtins upload-all-docs upload-all clean up down status wait network-url public-url public-status stop-public \
        install install-dev dev run test test-quick format lint fix type-check \
        security pre-commit-install pre-commit check-all coverage docs shell download-embedding-model \
        ipython watch add add-dev remove list-docs test-upload sync logs \
        start-cls stop-cls start-monitor stop-monitor start-api stop-api status-mcp

# ============================================================
# 默认目标：显示帮助信息
# ============================================================
help:
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)  SuperBizAgent Python 版本 - Makefile 命令$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo ""
	@echo "$(CYAN)【一键操作】$(NC)"
	@echo "  $(YELLOW)make init$(NC)         - 🚀 一键初始化（仅启动服务，不更新 RAG）"
	@echo ""
	@echo "$(CYAN)【Docker 管理】$(NC)"
	@echo "  $(YELLOW)make up$(NC)           - 🐳 启动 Milvus 容器"
	@echo "  $(YELLOW)make down$(NC)         - 🛑 停止 Milvus 容器"
	@echo "  $(YELLOW)make status$(NC)       - 📊 查看容器状态"
	@echo ""
	@echo "$(CYAN)【服务管理】$(NC)"
	@echo "  $(YELLOW)make start$(NC)        - 🚀 启动所有服务（MCP + FastAPI）"
	@echo "  $(YELLOW)make stop$(NC)         - 🛑 停止所有服务（MCP + FastAPI）"
	@echo "  $(YELLOW)make restart$(NC)      - 🔄 重启所有服务"
	@echo "  $(YELLOW)make check$(NC)        - 🔍 检查 FastAPI 服务状态"
	@echo "  $(YELLOW)make network-url$(NC)  - 🌐 显示局域网访问地址"
	@echo "  $(YELLOW)make public-url$(NC)   - 🌍 通过 cloudflared 生成公网访问地址"
	@echo "  $(YELLOW)make stop-public$(NC)  - 🛑 停止公网隧道"
	@echo "  $(YELLOW)make status-mcp$(NC)   - 📊 查看 MCP 服务状态"
	@echo ""
	@echo "$(CYAN)【MCP 服务管理】$(NC)"
	@echo "  $(YELLOW)make start-cls$(NC)     - 📋 启动 CLS MCP 服务"
	@echo "  $(YELLOW)make stop-cls$(NC)      - 🛑 停止 CLS MCP 服务"
	@echo "  $(YELLOW)make start-monitor$(NC) - 📊 启动 Monitor MCP 服务"
	@echo "  $(YELLOW)make stop-monitor$(NC)  - 🛑 停止 Monitor MCP 服务"
	@echo "  $(YELLOW)make start-api$(NC)     - 🚀 启动 FastAPI 服务"
	@echo "  $(YELLOW)make stop-api$(NC)      - 🛑 停止 FastAPI 服务"
	@echo ""
	@echo "$(CYAN)【开发模式】$(NC)"
	@echo "  $(YELLOW)make dev$(NC)          - 🔧 开发模式运行（前台，热重载）"
	@echo "  $(YELLOW)make run$(NC)          - 🏭 生产模式运行（前台）"
	@echo ""
	@echo "$(CYAN)【文档管理】$(NC)"
	@echo "  $(YELLOW)make upload$(NC)              - 📤 索引 my_knowledge_document 新增/变更文档"
	@echo "  $(YELLOW)make upload-builtin-docs$(NC) - 🔁 强制重跑 aiops-docs 全部内置文档"
	@echo "  $(YELLOW)make upload-all-docs$(NC)     - 🔁 强制重跑 aiops-docs 和 my_knowledge_document 全部文档"
	@echo "  $(YELLOW)make list-docs$(NC)           - 📚 列出可上传的文档"
	@echo "  $(YELLOW)make test-upload$(NC)         - 🧪 测试上传单个文件"
	@echo ""
	@echo "$(CYAN)【依赖管理】$(NC)"
	@echo "  $(YELLOW)make install$(NC)      - 📦 安装生产依赖"
	@echo "  $(YELLOW)make install-dev$(NC)  - 📦 安装开发依赖"
	@echo "  $(YELLOW)make sync$(NC)         - 🔄 同步依赖"
	@echo "  $(YELLOW)make add PKG=xxx$(NC)  - ➕ 添加依赖包"
	@echo "  $(YELLOW)make download-embedding-model$(NC) - 📥 下载本地 BGE-M3 MLX embedding 模型"
	@echo ""
	@echo "$(CYAN)【代码质量】$(NC)"
	@echo "  $(YELLOW)make format$(NC)       - 🎨 格式化代码"
	@echo "  $(YELLOW)make lint$(NC)         - 🔍 代码检查"
	@echo "  $(YELLOW)make fix$(NC)          - 🔧 自动修复问题"
	@echo "  $(YELLOW)make test$(NC)         - 🧪 运行测试"
	@echo "  $(YELLOW)make check-all$(NC)    - ✅ 运行所有检查"
	@echo ""
	@echo "$(CYAN)【其他】$(NC)"
	@echo "  $(YELLOW)make clean$(NC)        - 🧹 清理临时文件"
	@echo "  $(YELLOW)make shell$(NC)        - 🐍 启动 Python Shell"
	@echo "  $(YELLOW)make coverage$(NC)     - 📊 查看测试覆盖率"
	@echo "  $(YELLOW)make logs$(NC)         - 📜 查看服务日志"
	@echo ""
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)使用示例:$(NC)"
	@echo "  1. 一键初始化: $(YELLOW)make init$(NC)"
	@echo "  2. 启动服务:   $(YELLOW)make start$(NC) (自动启动 CLS + Monitor MCP + FastAPI)"
	@echo "  3. 检查状态:   $(YELLOW)make status-mcp$(NC)"
	@echo "  4. 停止服务:   $(YELLOW)make stop$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"

# ============================================================
# 一键初始化
# ============================================================
init:
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)🚀 开始一键初始化 SuperBizAgent...$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo ""
	@echo "$(YELLOW)步骤 1/2: 启动 FastAPI 和 MCP 服务$(NC)"
	@$(MAKE) start
	@echo ""
	@echo "$(YELLOW)步骤 2/2: 等待服务就绪$(NC)"
	@$(MAKE) wait
	@echo ""
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)✅ 初始化完成！未更新 RAG 知识库$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo ""
	@echo "$(GREEN)🌐 服务访问地址:$(NC)"
	@echo "   API 服务: $(SERVER_URL)"
	@echo "   API 文档: $(SERVER_URL)/docs"
	@echo "   RAG-Anything 存储目录: rag_anything_storage"
	@echo ""
	@echo "$(YELLOW)📚 更新 RAG 知识库请单独执行: make upload$(NC)"
	@echo ""
	@echo "$(YELLOW)💡 提示: 服务正在后台运行$(NC)"
	@echo "   查看日志: $(YELLOW)tail -f server.log$(NC)"
	@echo "   停止服务: $(YELLOW)make stop$(NC)"

# ============================================================
# Docker 管理
# ============================================================

# 启动 Docker 容器（使用 docker compose）
up:
	@echo "$(YELLOW)🐳 检查 Docker 容器状态...$(NC)"
	@if ! docker info > /dev/null 2>&1; then \
		echo "$(YELLOW)⚠️  Docker 未运行，尝试启动 Colima...$(NC)"; \
		colima start 2>/dev/null || (echo "$(RED)❌ 无法启动 Docker，请手动启动$(NC)" && exit 1); \
		sleep 3; \
	fi
	@if docker ps --format '{{.Names}}' | grep -q "^$(MILVUS_CONTAINER)$$"; then \
		echo "$(GREEN)✅ Milvus 容器已经在运行中$(NC)"; \
		docker ps --filter "name=milvus" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | head -10; \
	else \
		echo "$(YELLOW)🚀 启动 Milvus 相关容器...$(NC)"; \
		docker compose -f vector-database.yml up -d; \
		echo "$(YELLOW)⏳ 等待容器启动...$(NC)"; \
		sleep 5; \
		if docker ps --format '{{.Names}}' | grep -q "^$(MILVUS_CONTAINER)$$"; then \
			echo "$(GREEN)✅ Docker 容器启动成功！$(NC)"; \
			echo ""; \
			echo "$(GREEN)📋 运行中的容器:$(NC)"; \
			docker ps --filter "name=milvus" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | head -10; \
			echo ""; \
			echo "$(GREEN)🌐 服务访问地址:$(NC)"; \
			echo "   Milvus: localhost:19530"; \
			echo "   Attu (Web UI): http://localhost:8000"; \
			echo "   MinIO: http://localhost:9001 (admin/minioadmin)"; \
		else \
			echo "$(RED)❌ 容器启动失败$(NC)"; \
			exit 1; \
		fi; \
	fi

# 停止 Docker 容器
down:
	@echo "$(YELLOW)🛑 停止 Docker 容器...$(NC)"
	@if docker ps --format '{{.Names}}' | grep -q "milvus"; then \
		docker compose -f vector-database.yml down; \
		echo "$(GREEN)✅ Docker 容器已停止$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  没有运行中的 Milvus 容器$(NC)"; \
	fi

# 查看容器状态
status:
	@echo "$(YELLOW)📊 Docker 容器状态:$(NC)"
	@echo ""
	@if docker ps -a --format '{{.Names}}' | grep -q "milvus"; then \
		docker ps -a --filter "name=milvus" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; \
		echo ""; \
		running=$$(docker ps --filter "name=milvus" --format '{{.Names}}' | wc -l | tr -d ' '); \
		total=$$(docker ps -a --filter "name=milvus" --format '{{.Names}}' | wc -l | tr -d ' '); \
		echo "$(GREEN)运行中: $$running / $$total$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  没有找到 Milvus 相关容器$(NC)"; \
		echo "$(YELLOW)提示: 请先创建 Milvus 容器$(NC)"; \
	fi

# ============================================================
# MCP 服务管理
# ============================================================

# 启动 CLS MCP 服务
start-cls:
	@echo "$(YELLOW)📋 启动 CLS MCP 服务...$(NC)"
	@if pgrep -f "mcp_servers/cls_server.py" > /dev/null 2>&1; then \
		echo "$(GREEN)✅ CLS MCP 服务已经在运行中$(NC)"; \
	else \
		echo "$(YELLOW)📦 正在启动 CLS MCP 服务（后台运行）...$(NC)"; \
		if command -v screen > /dev/null 2>&1; then \
			screen -dmS superbiz_cls sh -lc 'cd "$(CURDIR)" && exec .venv/bin/python mcp_servers/cls_server.py > mcp_cls.log 2>&1'; \
			sleep 1; \
			pgrep -f "mcp_servers/cls_server.py" | tail -n 1 > mcp_cls.pid 2>/dev/null || true; \
		else \
			nohup .venv/bin/python mcp_servers/cls_server.py > mcp_cls.log 2>&1 & \
			echo $$! > mcp_cls.pid; \
		fi; \
		sleep 2; \
		if pgrep -f "mcp_servers/cls_server.py" > /dev/null 2>&1; then \
			echo "$(GREEN)✅ CLS MCP 服务启动成功$(NC)"; \
			echo "$(YELLOW)   PID: $$(cat mcp_cls.pid)$(NC)"; \
			echo "$(YELLOW)   URL: http://127.0.0.1:8003/mcp$(NC)"; \
			echo "$(YELLOW)   日志: mcp_cls.log$(NC)"; \
		else \
			echo "$(RED)❌ CLS MCP 服务启动失败$(NC)"; \
			echo "$(YELLOW)请检查日志: tail -f mcp_cls.log$(NC)"; \
		fi; \
	fi

# 启动 Monitor MCP 服务
start-monitor:
	@echo "$(YELLOW)📊 启动 Monitor MCP 服务...$(NC)"
	@if pgrep -f "mcp_servers/monitor_server.py" > /dev/null 2>&1; then \
		echo "$(GREEN)✅ Monitor MCP 服务已经在运行中$(NC)"; \
	else \
		echo "$(YELLOW)📦 正在启动 Monitor MCP 服务（后台运行）...$(NC)"; \
		if command -v screen > /dev/null 2>&1; then \
			screen -dmS superbiz_monitor sh -lc 'cd "$(CURDIR)" && exec .venv/bin/python mcp_servers/monitor_server.py > mcp_monitor.log 2>&1'; \
			sleep 1; \
			pgrep -f "mcp_servers/monitor_server.py" | tail -n 1 > mcp_monitor.pid 2>/dev/null || true; \
		else \
			nohup .venv/bin/python mcp_servers/monitor_server.py > mcp_monitor.log 2>&1 & \
			echo $$! > mcp_monitor.pid; \
		fi; \
		sleep 2; \
		if pgrep -f "mcp_servers/monitor_server.py" > /dev/null 2>&1; then \
			echo "$(GREEN)✅ Monitor MCP 服务启动成功$(NC)"; \
			echo "$(YELLOW)   PID: $$(cat mcp_monitor.pid)$(NC)"; \
			echo "$(YELLOW)   URL: http://127.0.0.1:8004/mcp$(NC)"; \
			echo "$(YELLOW)   日志: mcp_monitor.log$(NC)"; \
		else \
			echo "$(RED)❌ Monitor MCP 服务启动失败$(NC)"; \
			echo "$(YELLOW)请检查日志: tail -f mcp_monitor.log$(NC)"; \
		fi; \
	fi

# 停止 Monitor MCP 服务
stop-monitor:
	@echo "$(YELLOW)🛑 停止 Monitor MCP 服务...$(NC)"
	@screen -S superbiz_monitor -X quit 2>/dev/null || true
	@if [ -f mcp_monitor.pid ]; then \
		pid=$$(cat mcp_monitor.pid); \
		if ps -p $$pid > /dev/null 2>&1; then \
			kill $$pid; \
			echo "$(GREEN)✅ Monitor MCP 服务已停止 (PID: $$pid)$(NC)"; \
		else \
			echo "$(YELLOW)⚠️  进程不存在 (PID: $$pid)$(NC)"; \
		fi; \
		rm -f mcp_monitor.pid; \
	else \
		echo "$(YELLOW)⚠️  未找到 mcp_monitor.pid 文件$(NC)"; \
		pkill -f "mcp_servers/monitor_server.py" 2>/dev/null && \
			echo "$(GREEN)✅ 已停止所有 Monitor MCP 进程$(NC)" || \
			echo "$(YELLOW)⚠️  没有运行中的 Monitor MCP 进程$(NC)"; \
	fi

# 检查 MCP 服务状态
status-mcp:
	@echo "$(YELLOW)📊 MCP 服务状态:$(NC)"
	@echo ""
	@echo "$(CYAN)CLS MCP 服务:$(NC)"
	@if pgrep -f "mcp_servers/cls_server.py" > /dev/null 2>&1; then \
		pid=$$(pgrep -f "mcp_servers/cls_server.py"); \
		echo "  状态: $(GREEN)运行中$(NC)"; \
		echo "  PID: $$pid"; \
		echo "  URL: http://127.0.0.1:8003/mcp"; \
		curl -s http://127.0.0.1:8003/mcp > /dev/null 2>&1 && \
			echo "  连接: $(GREEN)✅ 正常$(NC)" || \
			echo "  连接: $(RED)❌ 无法连接$(NC)"; \
	else \
		echo "  状态: $(RED)未运行$(NC)"; \
	fi
	@echo ""
	@echo "$(CYAN)Monitor MCP 服务:$(NC)"
	@if pgrep -f "mcp_servers/monitor_server.py" > /dev/null 2>&1; then \
		pid=$$(pgrep -f "mcp_servers/monitor_server.py"); \
		echo "  状态: $(GREEN)运行中$(NC)"; \
		echo "  PID: $$pid"; \
		echo "  URL: http://127.0.0.1:8004/mcp"; \
		curl -s http://127.0.0.1:8004/mcp > /dev/null 2>&1 && \
			echo "  连接: $(GREEN)✅ 正常$(NC)" || \
			echo "  连接: $(RED)❌ 无法连接$(NC)"; \
	else \
		echo "  状态: $(RED)未运行$(NC)"; \
	fi
	@echo ""
	@echo "$(CYAN)Math MCP 服务:$(NC)"
	@echo "  状态: $(YELLOW)已移除（示例服务）$(NC)"

# ============================================================
# FastAPI 服务管理
# ============================================================

# 启动所有服务（MCP + FastAPI）
start:
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)🚀 启动所有服务$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo ""
	@$(MAKE) start-cls
	@sleep 1
	@echo ""
	@$(MAKE) start-monitor
	@sleep 1
	@echo ""
	@$(MAKE) start-api
	@echo ""
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)✅ 所有服务启动完成！$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"

# 启动 FastAPI 服务
start-api:
	@echo "$(YELLOW)🚀 启动 FastAPI 服务...$(NC)"
	@if curl -s -f $(HEALTH_CHECK_API) > /dev/null 2>&1; then \
		echo "$(GREEN)✅ FastAPI 服务已经在运行中 ($(SERVER_URL))$(NC)"; \
		$(MAKE) network-url; \
	else \
		echo "$(YELLOW)📦 正在启动 FastAPI 服务（后台运行）...$(NC)"; \
		if command -v screen > /dev/null 2>&1; then \
			screen -dmS superbiz_api sh -lc 'cd "$(CURDIR)" && exec .venv/bin/python -m uvicorn app.main:app --host $(BIND_HOST) --port $(PORT) > server.log 2>&1'; \
			sleep 1; \
			pgrep -f "uvicorn app.main:app --host $(BIND_HOST) --port $(PORT)" | tail -n 1 > server.pid 2>/dev/null || true; \
		else \
			nohup .venv/bin/python -m uvicorn app.main:app --host $(BIND_HOST) --port $(PORT) > server.log 2>&1 & \
			echo $$! > server.pid; \
		fi; \
		echo "$(GREEN)✅ FastAPI 服务启动命令已执行$(NC)"; \
		echo "$(YELLOW)   PID: $$(cat server.pid 2>/dev/null || echo 待确认)$(NC)"; \
		echo "$(YELLOW)   本机 URL: $(SERVER_URL)$(NC)"; \
		echo "$(YELLOW)   日志: server.log$(NC)"; \
		$(MAKE) network-url; \
	fi

# 显示局域网访问地址
network-url:
	@lan_ip=$$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || ifconfig 2>/dev/null | awk '/inet / && $$2 != "127.0.0.1" {print $$2; exit}' || hostname -I 2>/dev/null | awk '{print $$1}' || echo ""); \
	token=$$(grep -E '^PUBLIC_ACCESS_TOKEN=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$$//'); \
	token_param=""; \
	if [ -n "$$token" ]; then token_param="?access_token=$$token"; fi; \
	if [ -n "$$lan_ip" ]; then \
		echo "$(GREEN)🌐 局域网访问地址: http://$$lan_ip:$(PORT)/static/index.html$$token_param$(NC)"; \
		echo "$(YELLOW)   其他主机需与当前主机在同一网络，并允许当前主机防火墙放行 $(PORT) 端口。$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  未自动获取到局域网 IP，请在当前主机执行 ifconfig 查看 IP 后访问: http://<当前主机IP>:$(PORT)/static/index.html$$token_param$(NC)"; \
	fi

# 通过 cloudflared 生成公网访问地址（适合不同网络访问）
public-url:
	@echo "$(YELLOW)🌍 准备生成公网访问地址...$(NC)"
	@if [ -z "$(CLOUDFLARED)" ]; then \
		echo "$(RED)❌ 未安装 cloudflared，无法创建公网隧道。$(NC)"; \
		echo "$(YELLOW)macOS 可安装: brew install cloudflared$(NC)"; \
		echo "$(YELLOW)安装后执行: make public-url$(NC)"; \
		exit 1; \
	fi
	@$(MAKE) start-api
	@screen -S superbiz_public_tunnel -X quit 2>/dev/null || true
	@rm -f "$(PUBLIC_TUNNEL_LOG)"
	@screen -dmS superbiz_public_tunnel sh -lc 'cd "$(CURDIR)" && exec "$(CLOUDFLARED)" tunnel --url http://localhost:$(PORT) > "$(PUBLIC_TUNNEL_LOG)" 2>&1'
	@echo "$(YELLOW)⏳ 等待 cloudflared 返回公网地址...$(NC)"
	@for i in $$(seq 1 30); do \
		url=$$(grep -Eo 'https://[-a-zA-Z0-9]+\.trycloudflare\.com' "$(PUBLIC_TUNNEL_LOG)" 2>/dev/null | tail -n 1); \
		if [ -n "$$url" ]; then \
			token=$$(grep -E '^PUBLIC_ACCESS_TOKEN=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$$//'); \
			echo "$(GREEN)🌍 公网访问地址: $$url/static/index.html$(NC)"; \
			if [ -n "$$token" ]; then \
				echo "$(GREEN)🔐 带访问令牌地址: $$url/static/index.html?access_token=$$token$(NC)"; \
			else \
				echo "$(YELLOW)⚠️  当前未配置 PUBLIC_ACCESS_TOKEN，公网地址可被任何知道链接的人访问。$(NC)"; \
			fi; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "$(RED)❌ 未能在 30 秒内获取公网地址，请查看 $(PUBLIC_TUNNEL_LOG)$(NC)"; \
	exit 1

public-status:
	@url=$$(grep -Eo 'https://[-a-zA-Z0-9]+\.trycloudflare\.com' "$(PUBLIC_TUNNEL_LOG)" 2>/dev/null | tail -n 1); \
	if [ -n "$$url" ]; then \
		echo "$(GREEN)🌍 公网访问地址: $$url/static/index.html$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  未找到公网隧道地址，请先执行 make public-url$(NC)"; \
	fi

stop-public:
	@echo "$(YELLOW)🛑 停止公网隧道...$(NC)"
	@screen -S superbiz_public_tunnel -X quit 2>/dev/null && \
		echo "$(GREEN)✅ 公网隧道已停止$(NC)" || \
		echo "$(YELLOW)⚠️  没有运行中的公网隧道$(NC)"

# 停止所有服务（FastAPI + MCP）
stop:
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)🛑 停止所有服务$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo ""
	@$(MAKE) stop-api
	@echo ""
	@$(MAKE) stop-cls
	@echo ""
	@$(MAKE) stop-monitor
	@echo ""
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"
	@echo "$(GREEN)✅ 所有服务已停止！$(NC)"
	@echo "$(GREEN)═══════════════════════════════════════════════════════$(NC)"

# 停止 CLS MCP 服务
stop-cls:
	@echo "$(YELLOW)🛑 停止 CLS MCP 服务...$(NC)"
	@screen -S superbiz_cls -X quit 2>/dev/null || true
	@if [ -f mcp_cls.pid ]; then \
		pid=$$(cat mcp_cls.pid); \
		if ps -p $$pid > /dev/null 2>&1; then \
			kill $$pid; \
			echo "$(GREEN)✅ CLS MCP 服务已停止 (PID: $$pid)$(NC)"; \
		else \
			echo "$(YELLOW)⚠️  进程不存在 (PID: $$pid)$(NC)"; \
		fi; \
		rm -f mcp_cls.pid; \
	else \
		echo "$(YELLOW)⚠️  未找到 mcp_cls.pid 文件$(NC)"; \
		pkill -f "mcp_servers/cls_server.py" 2>/dev/null && \
			echo "$(GREEN)✅ 已停止所有 CLS MCP 进程$(NC)" || \
			echo "$(YELLOW)⚠️  没有运行中的 CLS MCP 进程$(NC)"; \
	fi

# 停止 FastAPI 服务
stop-api:
	@echo "$(YELLOW)🛑 停止 FastAPI 服务...$(NC)"
	@screen -S superbiz_api -X quit 2>/dev/null || true
	@if [ -f server.pid ]; then \
		pid=$$(cat server.pid); \
		if ps -p $$pid -o command= 2>/dev/null | grep -q "uvicorn app.main:app"; then \
			kill $$pid; \
			echo "$(GREEN)✅ FastAPI 服务已停止 (PID: $$pid)$(NC)"; \
		else \
			echo "$(YELLOW)⚠️  PID 无效或不是 FastAPI 进程 (PID: $$pid)，尝试按进程名停止$(NC)"; \
			pkill -f "uvicorn app.main:app" 2>/dev/null && \
				echo "$(GREEN)✅ 已停止所有 uvicorn 进程$(NC)" || \
				echo "$(YELLOW)⚠️  没有运行中的 uvicorn 进程$(NC)"; \
		fi; \
		rm -f server.pid; \
	else \
		echo "$(YELLOW)⚠️  未找到 server.pid 文件$(NC)"; \
		pkill -f "uvicorn app.main:app" 2>/dev/null && \
			echo "$(GREEN)✅ 已停止所有 uvicorn 进程$(NC)" || \
			echo "$(YELLOW)⚠️  没有运行中的 uvicorn 进程$(NC)"; \
	fi

# 重启所有服务
restart:
	@echo "$(YELLOW)🔄 重启所有服务...$(NC)"
	@echo ""
	@$(MAKE) stop
	@sleep 2
	@$(MAKE) start
	@$(MAKE) wait
	@echo ""
	@echo "$(GREEN)✅ 所有服务重启完成！$(NC)"

# 等待服务就绪（最多 60 秒）
wait:
	@echo "$(YELLOW)⏳ 等待服务器就绪...$(NC)"
	@max_attempts=60; \
	attempt=0; \
	while [ $$attempt -lt $$max_attempts ]; do \
		if curl -s -f $(HEALTH_CHECK_API) > /dev/null 2>&1; then \
			echo ""; \
			echo "$(GREEN)✅ 服务器已就绪！($(SERVER_URL))$(NC)"; \
			exit 0; \
		fi; \
		attempt=$$((attempt + 1)); \
		printf "\r$(YELLOW)   等待中... [$$attempt/$$max_attempts]$(NC)"; \
		sleep 1; \
	done; \
	echo ""; \
	echo "$(RED)❌ 服务器启动超时！$(NC)"; \
	echo "$(YELLOW)请检查日志: tail -f server.log$(NC)"; \
	exit 1

# 检查服务状态
check:
	@echo "$(YELLOW)🔍 检查服务器状态...$(NC)"
	@if curl -s -f $(HEALTH_CHECK_API) > /dev/null 2>&1; then \
		echo "$(GREEN)✅ 服务器运行正常 ($(SERVER_URL))$(NC)"; \
		echo ""; \
		echo "$(CYAN)健康检查响应:$(NC)"; \
		curl -s $(HEALTH_CHECK_API) | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2, ensure_ascii=False))" 2>/dev/null || curl -s $(HEALTH_CHECK_API); \
	else \
		echo "$(RED)❌ 服务器未运行或无法连接！$(NC)"; \
		echo "$(YELLOW)请先启动服务: make start$(NC)"; \
		exit 1; \
	fi

# 开发模式运行（前台，热重载）
dev:
	@echo "$(YELLOW)🔧 启动开发服务器（热重载）...$(NC)"
	.venv/bin/python -m uvicorn app.main:app --reload --host $(BIND_HOST) --port $(PORT)

# 生产模式运行（前台）
run:
	@echo "$(YELLOW)🏭 启动生产服务器...$(NC)"
	.venv/bin/python -m uvicorn app.main:app --host $(BIND_HOST) --port $(PORT)

# ============================================================
# 文档管理
# ============================================================

# 上传所有文档
upload:
	@echo "$(YELLOW)📤 开始索引 $(UPLOAD_TITLE)...$(NC)"
	@mkdir -p "$(KNOWLEDGE_DIR)"
	@if [ ! -d "$(DOCS_DIR)" ] && [ ! -d "$(KNOWLEDGE_DIR)" ]; then \
		echo "$(RED)❌ 文档目录不存在！$(NC)"; \
		exit 1; \
	fi
	@tmp_file=$$(mktemp); \
	token=$$(grep -E '^PUBLIC_ACCESS_TOKEN=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$$//'); \
	if [ -n "$(UPLOAD_ROOTS)" ]; then \
		upload_roots="$(UPLOAD_ROOTS)"; \
	elif [ "$(UPLOAD_BUILTIN_DOCS)" = "1" ]; then \
		upload_roots="$(DOCS_DIR) $(KNOWLEDGE_DIR)"; \
	else \
		upload_roots="$(KNOWLEDGE_DIR)"; \
	fi; \
	force_arg=""; \
	if [ "$(UPLOAD_FORCE)" = "1" ]; then force_arg="--force"; fi; \
	.venv/bin/python scripts/upload_candidates.py list $$force_arg $$upload_roots > "$$tmp_file"; \
	total=$$(wc -l < "$$tmp_file" | tr -d ' '); \
	current=0; \
	success=0; \
	failed=0; \
	draw_progress() { \
		current_value=$$1; \
		total_value=$$2; \
		label=$$3; \
		width=30; \
		if [ "$$total_value" -gt 0 ]; then \
			percent=$$((current_value * 100 / total_value)); \
			filled=$$((current_value * width / total_value)); \
		else \
			percent=100; \
			filled=$$width; \
		fi; \
		empty=$$((width - filled)); \
		bar=$$(printf "%*s" "$$filled" "" | tr " " "#"); \
		rest=$$(printf "%*s" "$$empty" "" | tr " " "-"); \
		printf "\r$(CYAN)进度: [%s%s] %3d%% (%d/%d) %s$(NC)\033[K" "$$bar" "$$rest" "$$percent" "$$current_value" "$$total_value" "$$label"; \
	}; \
	if [ "$$total" -eq 0 ]; then \
		echo "$(YELLOW)   未发现新增或变更文件；内置 $(DOCS_DIR) 默认不会重跑。$(NC)"; \
		echo "$(YELLOW)   如需重建内置文档，请执行: make upload-builtin-docs$(NC)"; \
		rm -f "$$tmp_file"; \
		exit 0; \
	fi; \
	draw_progress 0 "$$total" "等待开始"; \
	while IFS= read -r file; do \
		current=$$((current + 1)); \
		filename=$$(basename "$$file"); \
		printf "\n$(YELLOW)  [%s/%s] 索引: %s$(NC)\n" "$$current" "$$total" "$$file"; \
		draw_progress $$((current - 1)) "$$total" "处理中"; \
		payload=$$(.venv/bin/python -c 'import json,sys; print(json.dumps({"file_path": sys.argv[1]}, ensure_ascii=False))' "$$file"); \
		if [ -n "$$token" ]; then \
			response=$$(curl -s -w "\n%{http_code}" --connect-timeout 10 --max-time $(INDEX_TIMEOUT) -X POST "$(SERVER_URL)/api/index_file" \
				-H "Content-Type: application/json" \
				-H "Accept: application/json" \
				-H "X-Access-Token: $$token" \
				-H "$(RAG_UPDATE_HEADER): $(RAG_UPDATE_MODE)" \
				-d "$$payload"); \
		else \
			response=$$(curl -s -w "\n%{http_code}" --connect-timeout 10 --max-time $(INDEX_TIMEOUT) -X POST "$(SERVER_URL)/api/index_file" \
				-H "Content-Type: application/json" \
				-H "Accept: application/json" \
				-H "$(RAG_UPDATE_HEADER): $(RAG_UPDATE_MODE)" \
				-d "$$payload"); \
		fi; \
		http_code=$$(echo "$$response" | tail -n1); \
		body=$$(echo "$$response" | sed '$$d'); \
		if [ "$$http_code" = "200" ]; then \
			success=$$((success + 1)); \
			.venv/bin/python scripts/upload_candidates.py mark "$$file" >/dev/null 2>&1 || true; \
			draw_progress "$$current" "$$total" "完成"; \
		else \
			failed=$$((failed + 1)); \
			draw_progress "$$current" "$$total" "失败"; \
			printf "\n$(RED)      ❌ 失败: %s (HTTP %s)$(NC)\n" "$$file" "$$http_code"; \
			echo "$$body" | head -n 3; \
		fi; \
	done < "$$tmp_file"; \
	rm -f "$$tmp_file"; \
	echo ""; \
	echo "$(GREEN)📊 索引统计:$(NC)"; \
	echo "   总计: $$total 个文件"; \
	echo "   $(GREEN)成功: $$success$(NC)"; \
	if [ $$failed -gt 0 ]; then \
		echo "   $(RED)失败: $$failed$(NC)"; \
	fi

# 强制重跑全部内置文档
upload-builtin-docs:
	@$(MAKE) upload UPLOAD_ROOTS="$(DOCS_DIR)" UPLOAD_FORCE=1 UPLOAD_TITLE="$(DOCS_DIR) 全部内置文档"

upload-builtins: upload-builtin-docs

# 强制重跑全部内置文档和本地知识库文档
upload-all-docs:
	@$(MAKE) upload UPLOAD_ROOTS="$(DOCS_DIR) $(KNOWLEDGE_DIR)" UPLOAD_FORCE=1 UPLOAD_TITLE="全部内置和本地文档"

upload-all: upload-all-docs

# 列出文档
list-docs:
	@echo "$(YELLOW)📚 可上传到 RAG-Anything 的文档:$(NC)"
	@mkdir -p "$(KNOWLEDGE_DIR)"
	@tmp_file=$$(mktemp); \
	find "$(DOCS_DIR)" "$(KNOWLEDGE_DIR)" -type f \( \
		! -name '._*' ! -name '.DS_Store' ! -name '~$$*' ! -path '*/__MACOSX/*' \
	\) \( \
		-iname '*.pdf' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o \
		-iname '*.bmp' -o -iname '*.tiff' -o -iname '*.tif' -o -iname '*.gif' -o \
		-iname '*.webp' -o -iname '*.doc' -o -iname '*.docx' -o -iname '*.ppt' -o \
		-iname '*.pptx' -o -iname '*.xls' -o -iname '*.xlsx' -o -iname '*.txt' -o \
		-iname '*.md' \
	\) -print 2>/dev/null | sort > "$$tmp_file"; \
	if [ -s "$$tmp_file" ]; then \
		while IFS= read -r file; do ls -lh "$$file"; done < "$$tmp_file"; \
	else \
		echo "$(RED)没有找到可上传文件$(NC)"; \
	fi; \
	rm -f "$$tmp_file"

# 测试上传单个文件
test-upload:
	@echo "$(YELLOW)🧪 测试上传单个文件...$(NC)"
	@token=$$(grep -E '^PUBLIC_ACCESS_TOKEN=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$$//'); \
	first_file=$$(find "$(DOCS_DIR)" "$(KNOWLEDGE_DIR)" -type f \( \
		! -name '._*' ! -name '.DS_Store' ! -name '~$$*' ! -path '*/__MACOSX/*' \
	\) \( \
		-iname '*.pdf' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o \
		-iname '*.bmp' -o -iname '*.tiff' -o -iname '*.tif' -o -iname '*.gif' -o \
		-iname '*.webp' -o -iname '*.doc' -o -iname '*.docx' -o -iname '*.ppt' -o \
		-iname '*.pptx' -o -iname '*.xls' -o -iname '*.xlsx' -o -iname '*.txt' -o \
		-iname '*.md' \
	\) -print 2>/dev/null | sort | head -n1); \
	if [ -n "$$first_file" ]; then \
		echo "$(YELLOW)上传文件: $$first_file$(NC)"; \
		if [ -n "$$token" ]; then \
			curl -X POST $(UPLOAD_API) \
				-F "file=@$$first_file" \
				-H "Accept: application/json" \
				-H "X-Access-Token: $$token" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2, ensure_ascii=False))" 2>/dev/null || \
				curl -X POST $(UPLOAD_API) -F "file=@$$first_file" -H "X-Access-Token: $$token"; \
		else \
			curl -X POST $(UPLOAD_API) \
				-F "file=@$$first_file" \
				-H "Accept: application/json" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2, ensure_ascii=False))" 2>/dev/null || \
				curl -X POST $(UPLOAD_API) -F "file=@$$first_file"; \
		fi; \
	else \
		echo "$(RED)测试文件不存在$(NC)"; \
	fi

# ============================================================
# 依赖管理
# ============================================================

install:  ## 安装依赖（生产环境）
	@echo "$(YELLOW)📦 安装依赖...$(NC)"
	pip install -r requirements.txt 2>/dev/null || pip install -e .
	@echo "$(GREEN)✅ 依赖安装完成$(NC)"

install-dev:  ## 安装开发依赖
	@echo "$(YELLOW)📦 安装开发依赖...$(NC)"
	pip install -e ".[dev]" 2>/dev/null || pip install -e .
	@echo "$(GREEN)✅ 开发依赖安装完成$(NC)"

sync:  ## 同步依赖
	@echo "$(YELLOW)🔄 同步依赖...$(NC)"
	pip install -e . --upgrade
	@echo "$(GREEN)✅ 依赖同步完成$(NC)"

download-embedding-model:  ## 下载本地 BGE-M3 MLX embedding 模型
	@echo "$(YELLOW)📥 下载本地 BGE-M3 MLX embedding 模型...$(NC)"
	.venv/bin/python scripts/download_embedding_model.py
	@echo "$(GREEN)✅ 本地 embedding 模型已就绪$(NC)"

add:  ## 添加依赖包 (用法: make add PKG=package_name)
	@echo "$(YELLOW)📦 添加依赖: $(PKG)...$(NC)"
	pip install $(PKG)

add-dev:  ## 添加开发依赖 (用法: make add-dev PKG=package_name)
	@echo "$(YELLOW)📦 添加开发依赖: $(PKG)...$(NC)"
	pip install $(PKG)

remove:  ## 移除依赖包 (用法: make remove PKG=package_name)
	@echo "$(YELLOW)🗑️  移除依赖: $(PKG)...$(NC)"
	pip uninstall $(PKG)

# ============================================================
# 代码质量
# ============================================================

format:  ## 格式化代码
	@echo "$(YELLOW)🎨 格式化代码...$(NC)"
	python3 -m ruff check --select I --fix app/ 2>/dev/null || true
	python3 -m ruff format app/ 2>/dev/null || python3 -m black app/
	@echo "$(GREEN)✅ 格式化完成$(NC)"

lint:  ## 代码检查
	@echo "$(YELLOW)🔍 代码检查...$(NC)"
	python3 -m ruff check app/ 2>/dev/null || python3 -m flake8 app/
	@echo "$(GREEN)✅ 检查完成$(NC)"

fix:  ## 自动修复代码问题
	@echo "$(YELLOW)🔧 自动修复代码问题...$(NC)"
	python3 -m ruff check --fix app/ 2>/dev/null || true
	python3 -m ruff format app/ 2>/dev/null || python3 -m black app/
	@echo "$(GREEN)✅ 修复完成$(NC)"

type-check:  ## 类型检查
	@echo "$(YELLOW)🔍 类型检查...$(NC)"
	python3 -m mypy app/ --ignore-missing-imports
	@echo "$(GREEN)✅ 类型检查完成$(NC)"

security:  ## 安全检查
	@echo "$(YELLOW)🔒 安全检查...$(NC)"
	python3 -m bandit -r app/ -ll
	@echo "$(GREEN)✅ 安全检查完成$(NC)"

test:  ## 运行测试
	@echo "$(YELLOW)🧪 运行测试...$(NC)"
	python3 -m pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html

test-quick:  ## 快速测试
	@echo "$(YELLOW)⚡ 快速测试...$(NC)"
	python3 -m pytest tests/ -v

check-all:  ## 运行所有检查
	@echo "$(YELLOW)🚀 运行所有检查...$(NC)"
	@$(MAKE) format
	@$(MAKE) lint
	@$(MAKE) test
	@echo "$(GREEN)✅ 所有检查通过！$(NC)"

pre-commit-install:  ## 安装 pre-commit hooks
	@echo "$(YELLOW)🔗 安装 pre-commit hooks...$(NC)"
	python3 -m pre_commit install
	python3 -m pre_commit install --hook-type commit-msg
	@echo "$(GREEN)✅ Pre-commit hooks 安装完成$(NC)"

pre-commit:  ## 运行 pre-commit 检查
	@echo "$(YELLOW)🔍 运行 pre-commit 检查...$(NC)"
	python3 -m pre_commit run --all-files

coverage:  ## 查看测试覆盖率报告
	@echo "$(YELLOW)📊 生成覆盖率报告...$(NC)"
	python3 -m pytest tests/ --cov=app --cov-report=html --cov-report=term
	@echo "$(GREEN)✅ 覆盖率报告已生成: htmlcov/index.html$(NC)"
	@open htmlcov/index.html 2>/dev/null || xdg-open htmlcov/index.html 2>/dev/null || echo "请手动打开 htmlcov/index.html"

# ============================================================
# 其他工具
# ============================================================

clean:  ## 清理临时文件
	@echo "$(YELLOW)🧹 清理临时文件...$(NC)"
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf htmlcov/ .coverage
	rm -f server.pid server.log
	rm -f mcp_cls.pid mcp_cls.log
	rm -f mcp_monitor.pid mcp_monitor.log
	rm -rf uploads/*.tmp 2>/dev/null || true
	@echo "$(GREEN)✅ 清理完成$(NC)"

shell:  ## 启动 Python shell
	@echo "$(YELLOW)🐍 启动 Python shell...$(NC)"
	python3 -i -c "import sys; sys.path.insert(0, '.'); from app.config import config; print('环境已加载，config 对象可用')"

ipython:  ## 启动 IPython shell
	@echo "$(YELLOW)🐍 启动 IPython shell...$(NC)"
	python3 -m IPython

docs:  ## 打开 API 文档
	@echo "$(YELLOW)📚 API 文档地址: $(SERVER_URL)/docs$(NC)"
	@open $(SERVER_URL)/docs 2>/dev/null || xdg-open $(SERVER_URL)/docs 2>/dev/null || echo "请手动打开 $(SERVER_URL)/docs"

watch:  ## 监视文件变化并自动运行测试
	@echo "$(YELLOW)👀 监视文件变化...$(NC)"
	python3 -m pytest_watch -- -v

logs:  ## 查看服务日志
	@echo "$(YELLOW)📜 查看服务日志...$(NC)"
	@if [ -f server.log ]; then \
		tail -f server.log; \
	else \
		echo "$(RED)日志文件不存在$(NC)"; \
		echo "$(YELLOW)提示: 使用 make start 启动服务后会生成日志$(NC)"; \
	fi
