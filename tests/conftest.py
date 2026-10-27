import pytest

from app.config import Settings


@pytest.fixture
def settings() -> Settings:
    # 显式 offline：测试不读 .env，保证确定性
    return Settings(mode="offline", tool_retry_base_delay=0.0)


@pytest.fixture
def registry(settings):
    from app.domain_registry import DomainRegistry
    from knowledge_base.corpus import load_corpus

    return DomainRegistry.from_kb_docs(load_corpus(settings.kb_corpus_dir))


@pytest.fixture
def research_app(settings):
    from app.graph.factory import build_default_app

    return build_default_app(settings)
