import pytest

from app.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    # 显式 offline：测试不读 .env，保证确定性；
    # 记忆库指向临时文件；MCP 关闭（真链路在 test_mcp_server / test_api 专门覆盖）
    return Settings(
        mode="offline",
        tool_retry_base_delay=0.0,
        memory_db_path=tmp_path / "memory.sqlite3",
        mcp_kb_enabled=False,
    )


@pytest.fixture
def runtime(settings):
    from app.graph.factory import build_default_app

    rt = build_default_app(settings)
    yield rt
    rt.close()


@pytest.fixture
def research_app(runtime):
    return runtime.graph


@pytest.fixture
def registry(settings):
    from app.domain_registry import DomainRegistry
    from knowledge_base.corpus import load_corpus

    return DomainRegistry.from_kb_docs(load_corpus(settings.kb_corpus_dir))
