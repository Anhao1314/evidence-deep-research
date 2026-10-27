"""全局配置：环境变量前缀 DR_，默认离线模式（无 Key 可跑）。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DR_", env_file=".env", extra="ignore")

    mode: str = "offline"  # offline | online
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "deepseek-chat"

    # 护栏
    max_steps: int = 24
    max_plan_rounds: int = 2
    max_subtasks: int = 8
    evidence_budget: int = 24
    evidence_quote_max_chars: int = 240
    tool_max_retries: int = 2
    tool_retry_base_delay: float = 0.2

    # 数据目录（内置演示语料）
    kb_corpus_dir: Path = PROJECT_ROOT / "data" / "corpus"
    web_demo_dir: Path = PROJECT_ROOT / "data" / "web_demo"
    memory_db_path: Path = PROJECT_ROOT / "data" / "memory.sqlite3"

    # 知识库检索路：true = 自研 MCP Server（stdio 子进程，真实跨进程）
    mcp_kb_enabled: bool = True
    mcp_connect_timeout: float = 30.0
    mcp_call_timeout: float = 30.0

    # Web 搜索路：demo = 内置演示数据；tavily = 在线搜索（需 API Key）
    search_provider: str = "demo"
    tavily_api_key: str | None = None
    tavily_timeout: float = 15.0

    # Trace 与 LLM 弹性
    trace_db_path: Path = PROJECT_ROOT / "data" / "trace.sqlite3"
    llm_max_retries: int = 2
    llm_retry_base_delay: float = 0.5

    @property
    def offline(self) -> bool:
        return self.mode == "offline"
