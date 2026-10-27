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

    @property
    def offline(self) -> bool:
        return self.mode == "offline"
