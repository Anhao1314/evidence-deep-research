"""领域注册表：从内置语料元数据自动构建"本系统懂哪些研究主题"。

离线（启发式）Planner 依赖它把自然语言问题映射到 (topic, objects, aspects)，
因此领域内问题分解精准，领域外问题走泛化降级——这是规则模式的明确边界，
也是 README 设计取舍的一部分。语料更新时注册表随之自动更新。
"""

from pydantic import BaseModel, Field

from knowledge_base.corpus import KBDocument

# 各维度中文显示名（报告与缺口描述使用）
ASPECT_ZH: dict[str, str] = {
    "performance": "性能",
    "ecosystem": "生态",
    "cost": "成本",
    "maturity": "成熟度",
}

# 主题中文显示名（报告标题/表格使用）
TOPIC_DISPLAY: dict[str, str] = {
    "vector-db": "向量数据库",
    "agent-framework": "Agent 编排框架",
    "llm-api": "大模型 API",
}

# 主题级中文线索词：问题里没写英文对象名时靠它命中主题
TOPIC_HINTS_ZH: dict[str, list[str]] = {
    "vector-db": ["向量库", "向量数据库", "embedding", "检索引擎选型"],
    "agent-framework": ["编排框架", "agent 框架", "智能体框架", "langgraph", "编排选型"],
    "llm-api": ["大模型 api", "llm api", "模型 api", "api 选型"],
}

OBJECT_DISPLAY: dict[str, str] = {
    "qdrant": "Qdrant",
    "milvus": "Milvus",
    "langgraph": "LangGraph",
    "custom": "自研编排",
    "deepseek": "DeepSeek",
    "qwen": "Qwen（通义千问）",
}


def object_display(name: str) -> str:
    return OBJECT_DISPLAY.get(name, name.title() if name.isascii() else name)


class TopicInfo(BaseModel):
    topic: str
    objects: list[str] = Field(default_factory=list)
    aspects: list[str] = Field(default_factory=list)


class TopicMatch(BaseModel):
    topic: str
    matched_objects: list[str] = Field(default_factory=list, description="按问题中出现顺序")


class DomainRegistry:
    def __init__(self, topics: dict[str, TopicInfo]) -> None:
        self.topics = topics

    @classmethod
    def from_kb_docs(cls, docs: list[KBDocument]) -> "DomainRegistry":
        """从知识库语料元数据构建（web 演示数据与 KB 语料共享元数据口径）。"""
        topics: dict[str, TopicInfo] = {}
        for d in docs:
            info = topics.setdefault(d.topic, TopicInfo(topic=d.topic))
            if d.object_name and d.object_name not in info.objects:
                info.objects.append(d.object_name)
            if d.aspect not in info.aspects:
                info.aspects.append(d.aspect)
        return cls(topics)

    def display(self, topic: str) -> str:
        return TOPIC_DISPLAY.get(topic, topic)

    def display_object(self, name: str) -> str:
        return object_display(name)

    def match_question(self, question: str) -> TopicMatch | None:
        """对象名（英文技术名）优先，主题中文线索词兜底；取命中得分最高的主题。"""
        q = question.lower()
        best: TopicMatch | None = None
        best_score = 0
        for topic, info in self.topics.items():
            matched = sorted(
                (o for o in info.objects if o.lower() in q),
                key=lambda o: q.index(o.lower()),
            )
            hint_hit = any(kw in q for kw in TOPIC_HINTS_ZH.get(topic, []))
            score = len(matched) * 2 + (1 if hint_hit else 0)
            if score > best_score:
                best = TopicMatch(topic=topic, matched_objects=matched)
                best_score = score
        return best
