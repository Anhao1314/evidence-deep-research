"""真实运行捕获：生成 docs/artifacts/demo/* 并由产物自动写 docs/demo.md。

设计原则：
- 页面上的每个数字、每段报告、每条 Trace 都来自本脚本刚刚执行的 run；
- 正常路径使用真实 MCP stdio 知识库链路（McpKBClient）；
- 信息不足路径使用内置语料，展示“没有对象时声明缺口”；
- 失败路径明确注入故障（KB 连接重置 + 搜索超时），用于展示重试/降级/Trace，
  不是伪造一次线上事故。

运行：
    python scripts/capture_demo.py
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings
from app.domain_registry import DomainRegistry
from app.graph.factory import build_default_app, build_research_app
from app.llm.base import ComposeResult
from app.llm.heuristic import HeuristicProvider
from app.llm.resilient import ResilientProvider
from app.memory.store import MemoryStore
from app.tools.kb_client import InProcessKBClient
from app.tools.search import DemoSearchProvider
from app.trace.store import TraceStore
from eval.provenance import git_commit, git_dirty, now_iso, source_manifest
from knowledge_base.corpus import load_corpus
from knowledge_base.retriever import KBRetriever, ScoredDoc

ARTIFACT_DIR = PROJECT_ROOT / "docs" / "artifacts" / "demo"
RUNTIME_DIR = ARTIFACT_DIR / "_runtime"


def _settings(name: str, *, mcp: bool, retries: int = 2,
              max_plan_rounds: int = 2, corpus_dir: Path | None = None,
              web_dir: Path | None = None, rag_top_k: int = 8,
              kb_enabled: bool = True) -> Settings:
    return Settings(
        mode="offline",
        mcp_kb_enabled=mcp,
        kb_enabled=kb_enabled,
        tool_max_retries=retries,
        tool_retry_base_delay=0.0,
        max_plan_rounds=max_plan_rounds,
        rag_top_k=rag_top_k,
        kb_corpus_dir=corpus_dir or (PROJECT_ROOT / "data" / "corpus"),
        web_demo_dir=web_dir or (PROJECT_ROOT / "data" / "web_demo"),
        memory_db_path=RUNTIME_DIR / f"{name}-memory.sqlite3",
        trace_db_path=RUNTIME_DIR / f"{name}-trace.sqlite3",
    )


def _save_scenario(name: str, payload: dict, final_state: dict, *, kb_backend: str) -> dict:
    scenario_dir = ARTIFACT_DIR / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    report = final_state["report"]
    (scenario_dir / "report.md").write_text(report.markdown, encoding="utf-8")
    (scenario_dir / "report.json").write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace = payload["trace"]
    (scenario_dir / "trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    meta = {
        "case_id": payload["case_id"],
        "title": payload["title"],
        "question": payload["question"],
        "run_id": payload["run_id"],
        "fault_injection": payload.get("fault_injection"),
        "constraint_check": payload.get("constraint_check"),
        "controlled_fixture": payload.get("controlled_fixture"),
        "fixture_note": payload.get("fixture_note"),
        "kb_backend": kb_backend,
        "report_id": report.report_id,
        "routes_used": report.meta.routes_used,
        "routes_attempted": report.meta.routes_attempted,
        "degraded": report.meta.degraded,
        "plan_rounds": report.meta.plan_rounds,
        "step_count": report.meta.step_count,
        "information_gaps": report.meta.information_gaps,
        "evidence_count": len(report.evidences),
        "source_count": len(report.sources),
        "trace_event_count": trace["event_count"],
        "token_usage": trace["token_usage"],
        "plan_event_count": sum(1 for e in trace["events"] if e["event_type"] == "plan"),
        "dispatch_event_count": sum(1 for e in trace["events"] if e["event_type"] == "dispatch"),
        "constraint_summary": (
            report.constraint_summary.model_dump() if report.constraint_summary else None
        ),
        "guard_audit": report.guard_audit.model_dump() if report.guard_audit else None,
        "unverified_claims": [c.model_dump() for c in report.unverified_claims],
        "evidence_disposition": {
            "adopted": sum(1 for d in report.evidence_disposition if d.status == "adopted"),
            "excluded": sum(1 for d in report.evidence_disposition if d.status == "excluded"),
            "excluded_reasons": sorted({d.reason for d in report.evidence_disposition
                                        if d.status == "excluded"}),
        },
    }
    (scenario_dir / "scenario.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def run_happy() -> dict:
    runtime = build_default_app(_settings("happy", mcp=True))
    try:
        run_id = "demo-happy"
        final = runtime.graph.invoke(
            {"question": "RAG 系统的向量库选 Qdrant 还是 Milvus？", "user_id": "demo",
             "run_id": run_id},
            config={"recursion_limit": 128},
        )
        trace = runtime.trace.get(run_id)
        payload = {
            "case_id": "happy",
            "title": "正常路径：双对象对比研究",
            "question": "RAG 系统的向量库选 Qdrant 还是 Milvus？",
            "run_id": run_id,
            "trace": {
                "trace_id": run_id,
                "event_count": len(trace),
                "token_usage": sum(e.token_usage for e in trace),
                "events": [e.model_dump() for e in trace],
            },
        }
        return _save_scenario("happy", payload, final,
                              kb_backend=type(runtime.kb_client).__name__)
    finally:
        runtime.close()


def run_insufficient() -> dict:
    runtime = build_default_app(_settings("insufficient", mcp=False))
    try:
        run_id = "demo-insufficient"
        final = runtime.graph.invoke(
            {"question": "怎么评估一个开源项目的社区健康度？", "user_id": "demo",
             "run_id": run_id},
            config={"recursion_limit": 128},
        )
        trace = runtime.trace.get(run_id)
        payload = {
            "case_id": "insufficient",
            "title": "信息不足：领域外问题",
            "question": "怎么评估一个开源项目的社区健康度？",
            "run_id": run_id,
            "trace": {
                "trace_id": run_id,
                "event_count": len(trace),
                "token_usage": sum(e.token_usage for e in trace),
                "events": [e.model_dump() for e in trace],
            },
        }
        return _save_scenario("insufficient", payload, final,
                              kb_backend=type(runtime.kb_client).__name__)
    finally:
        runtime.close()


def run_constraints() -> dict:
    """约束型 fixture：验证约束真正改变结论方向，而不是只在末尾追加关键词。"""
    fixture = PROJECT_ROOT / "data" / "fixtures" / "constraint_selection"
    question = (
        "我们已有 PostgreSQL 团队，准备上线 500 万条 768 维向量的 RAG，"
        "团队 5 人、自托管、预算有限，并尽量不引入新数据库。"
        "Atlas、Helios 和 pgvector 应该选哪个？"
    )
    runtime = build_default_app(_settings(
        "constraints", mcp=False, corpus_dir=fixture,
        web_dir=fixture / "web", rag_top_k=20,
    ))
    try:
        run_id = "demo-constraints"
        final = runtime.graph.invoke(
            {"question": question, "user_id": "demo", "run_id": run_id},
            config={"recursion_limit": 160},
        )
        trace = runtime.trace.get(run_id)
        payload = {
            "case_id": "constraints",
            "title": "约束真正影响结论（离线合成 fixture）",
            "question": question,
            "run_id": run_id,
            "controlled_fixture": True,
            "fixture_note": (
                "这些名称仅作为合成 fixture 中的评测对象，结构化属性为测试构造，"
                "不代表同名真实产品的真实能力，也不构成真实技术选型建议。"
            ),
            "trace": {
                "trace_id": run_id,
                "event_count": len(trace),
                "token_usage": sum(e.token_usage for e in trace),
                "events": [e.model_dump() for e in trace],
            },
        }
        return _save_scenario("constraints", payload, final,
                              kb_backend=type(runtime.kb_client).__name__)
    finally:
        runtime.close()


class _ReplanKB:
    """首轮只返回 performance/cost；按查询中的“生态/成熟度”补文档。"""

    def __init__(self, docs):
        self._by_aspect = {}
        for doc in docs:
            if doc.object_name == "atlas":
                self._by_aspect.setdefault(doc.aspect, []).append(doc)

    def search(self, query, top_k=4, *, topic=None):
        docs = list(self._by_aspect.get("performance", [])) + list(
            self._by_aspect.get("cost", [])
        )
        if "生态" in query or "ecosystem" in query:
            docs += self._by_aspect.get("ecosystem", [])
        if "成熟度" in query or "maturity" in query:
            docs += self._by_aspect.get("maturity", [])
        return [ScoredDoc(doc=d, score=1.0) for d in docs[:top_k]]


class _EmptySearch:
    def search(self, query, top_k=4, *, topic=None):
        return []


def run_replan() -> dict:
    """脚本化检索 fixture：首轮缺口 -> verify -> 第二轮补充检索 -> 收敛。"""
    fixture = PROJECT_ROOT / "data" / "fixtures" / "constraint_selection"
    settings = _settings("replan", mcp=False, max_plan_rounds=2,
                         corpus_dir=fixture, web_dir=fixture / "web")
    docs = load_corpus(fixture)
    registry = DomainRegistry.from_kb_docs(docs)
    trace_store = TraceStore(settings.trace_db_path)
    memory_store = MemoryStore(settings.memory_db_path)
    graph = build_research_app(
        settings=settings,
        kb_client=_ReplanKB(docs),
        search_provider=_EmptySearch(),
        llm_provider=HeuristicProvider(registry, max_subtasks=settings.max_subtasks),
        registry=registry,
        memory_store=memory_store,
        trace_store=trace_store,
    )
    run_id = "demo-replan"
    try:
        final = graph.invoke(
            {"question": "Atlas 向量数据库的性能、生态、成本和成熟度怎么样？",
             "user_id": "demo", "run_id": run_id},
            config={"recursion_limit": 160},
        )
        trace = trace_store.get(run_id)
        payload = {
            "case_id": "replan",
            "title": "确定性 replan：首轮缺口到第二轮补充检索",
            "question": "Atlas 向量数据库的性能、生态、成本和成熟度怎么样？",
            "run_id": run_id,
            "controlled_fixture": True,
            "fixture_note": "脚本化 KB 只返回部分维度；仅证明离线 replan 行为与 Trace，不代表在线 replan 质量。",
            "trace": {
                "trace_id": run_id,
                "event_count": len(trace),
                "token_usage": sum(e.token_usage for e in trace),
                "events": [e.model_dump() for e in trace],
            },
        }
        return _save_scenario("replan", payload, final,
                              kb_backend="ScriptedKBClient（fixture）")
    finally:
        trace_store.close()
        memory_store.close()


_GUARD_RAW = """# 技术选型研究报告（模型原始输出，受控 fixture）

## 结论摘要

根据证据 [E1]，该系统在千万级向量规模下有性能数据。

Milvus 的 GPU 索引吞吐提升 10 倍 [E99]。

Qdrant 是最好的选择。
"""


class _GuardPrimary:
    """受控主模型输出：一条合法引用、一条非法引用、一条无引用事实性结论。"""

    def __init__(self, registry: DomainRegistry):
        self._heuristic = HeuristicProvider(registry)

    def plan(self, ctx):
        return self._heuristic.plan(ctx)

    def compose(self, ctx):
        return ComposeResult(markdown=_GUARD_RAW, raw_markdown=_GUARD_RAW, summary="raw")

    def rewrite(self, markdown, invalid_ids):
        return markdown  # 故意不修复，触发结论隔离


def run_guard() -> dict:
    settings = _settings("guard", mcp=False)
    retriever = KBRetriever.from_dir(settings.kb_corpus_dir)
    registry = DomainRegistry.from_kb_docs(retriever.docs)
    trace_store = TraceStore(settings.trace_db_path)
    memory_store = MemoryStore(settings.memory_db_path)
    provider = ResilientProvider(
        _GuardPrimary(registry),
        fallback=HeuristicProvider(registry, max_subtasks=settings.max_subtasks),
        trace_store=trace_store,
    )
    graph = build_research_app(
        settings=settings,
        kb_client=InProcessKBClient(retriever),
        search_provider=DemoSearchProvider.from_dir(settings.web_demo_dir),
        llm_provider=provider,
        registry=registry,
        memory_store=memory_store,
        trace_store=trace_store,
    )
    run_id = "demo-guard"
    try:
        final = graph.invoke(
            {"question": "Qdrant 和 Milvus 怎么选？", "user_id": "demo",
             "run_id": run_id},
            config={"recursion_limit": 128},
        )
        trace = trace_store.get(run_id)
        payload = {
            "case_id": "guard",
            "title": "引用 Guard：隔离非法/无引用结论（受控模型输出）",
            "question": "Qdrant 和 Milvus 怎么选？",
            "run_id": run_id,
            "controlled_fixture": True,
            "fixture_note": (
                "模型输出由脚本固定注入，包含合法引用、非法引用、无引用事实性结论；"
                "用于验证 Guard 行为，不是在线模型效果。"
            ),
            "trace": {
                "trace_id": run_id,
                "event_count": len(trace),
                "token_usage": sum(e.token_usage for e in trace),
                "events": [e.model_dump() for e in trace],
            },
        }
        return _save_scenario("guard", payload, final,
                              kb_backend="InProcessKBClient")
    finally:
        trace_store.close()
        memory_store.close()


class _FaultyKBClient:
    def search(self, query, top_k=4, *, topic=None):
        raise ConnectionError("demo fault injection: MCP KB connection reset")


class _FaultySearchProvider:
    def search(self, query, top_k=4, *, topic=None):
        raise TimeoutError("demo fault injection: search API timeout")


def run_failure() -> dict:
    settings = _settings("failure", mcp=False, retries=1, max_plan_rounds=1)
    retriever_docs = load_corpus(settings.kb_corpus_dir)
    registry = DomainRegistry.from_kb_docs(retriever_docs)
    trace_store = TraceStore(settings.trace_db_path)
    memory_store = MemoryStore(settings.memory_db_path)
    graph = build_research_app(
        settings=settings,
        kb_client=_FaultyKBClient(),
        search_provider=_FaultySearchProvider(),
        llm_provider=HeuristicProvider(registry, max_subtasks=settings.max_subtasks),
        registry=registry,
        memory_store=memory_store,
        trace_store=trace_store,
    )
    run_id = "demo-failure"
    try:
        final = graph.invoke(
            {"question": "Qdrant 和 Milvus 哪个更适合生产环境？", "user_id": "demo",
             "run_id": run_id},
            config={"recursion_limit": 128},
        )
        trace = trace_store.get(run_id)
        payload = {
            "case_id": "failure",
            "title": "真实失败：两路检索均失败（故障注入）",
            "question": "Qdrant 和 Milvus 哪个更适合生产环境？",
            "run_id": run_id,
            "fault_injection": {
                "kb_client": "ConnectionError('demo fault injection: MCP KB connection reset')",
                "search_provider": "TimeoutError('demo fault injection: search API timeout')",
                "tool_max_retries": 1,
                "note": "故障由脚本显式注入，用于展示重试、降级、缺口与 Trace；"
                        "不代表真实线上事故。",
            },
            "trace": {
                "trace_id": run_id,
                "event_count": len(trace),
                "token_usage": sum(e.token_usage for e in trace),
                "events": [e.model_dump() for e in trace],
            },
        }
        return _save_scenario("failure", payload, final, kb_backend="FaultyKBClient（注入）")
    finally:
        trace_store.close()
        memory_store.close()


def _payload_excerpt(payload: dict, limit: int = 200) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    text = text.replace("|", "\\|").replace("`", "'").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _trace_table(trace: dict) -> str:
    lines = [
        "| seq | 节点 | 事件 | payload（截断） |",
        "| --- | --- | --- | --- |",
    ]
    for event in trace["events"]:
        lines.append(
            f"| {event['seq']} | `{event['node']}` | `{event['event_type']}` | "
            f"`{_payload_excerpt(event['payload'])}` |"
        )
    return "\n".join(lines)


def _find(trace: dict, event_type: str) -> list[dict]:
    return [e for e in trace["events"] if e["event_type"] == event_type]


def _counts_bullets(trace: dict, plan_rounds: int) -> str:
    dispatch = _find(trace, "dispatch")
    merge = _find(trace, "merge")
    synth = _find(trace, "synthesize")
    verify = _find(trace, "verify")
    tool_calls = _find(trace, "tool_call")
    retries = _find(trace, "retry")
    degrades = _find(trace, "degrade")
    bullets = [
        (f"- `dispatch` 扇出：{dispatch[0]['payload'].get('fan_out') if dispatch else 0} 个子任务；"
        f"目标 {dispatch[0]['payload'].get('targets') if dispatch else []}"),
        (f"- `tool_call` 次数：{len(tool_calls)}；`retry` 事件：{len(retries)}；"
        f"`degrade` 事件：{len(degrades)}"),
        (f"- `merge`：原始证据 {merge[0]['payload'].get('raw_evidences') if merge else 0} 条，"
        f"去重后 {merge[0]['payload'].get('deduped') if merge else 0} 条"),
        (f"- `verify`：缺口 {len(verify[0]['payload'].get('gaps', [])) if verify else 0} 条，"
        f"replan_focus={'非空' if (verify and verify[0]['payload'].get('replan')) else '空'}；"
        f"最终计划轮数 {plan_rounds}（是否 replan 还受 max_plan_rounds 护栏约束）"),
        (f"- `synthesize`：证据 {synth[0]['payload'].get('evidences') if synth else 0} 条，"
        f"引用 {synth[0]['payload'].get('citations') if synth else 0} 处，"
        f"来源 {synth[0]['payload'].get('sources') if synth else 0} 条，"
        f"降级={synth[0]['payload'].get('degraded') if synth else None}"),
        f"- Trace 事件总数：{trace['event_count']}；Token：{trace['token_usage']}",
    ]
    return "\n".join(bullets)


def _section(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def _fence(markdown: str) -> str:
    body = "\n".join(line.rstrip() for line in markdown.rstrip().splitlines())
    return "```text\n" + body + "\n```"


def render_demo_md(manifest: dict, scenarios: dict[str, dict]) -> str:
    happy = scenarios["happy"]
    insufficient = scenarios["insufficient"]
    failure = scenarios["failure"]
    constraints = scenarios["constraints"]
    replan = scenarios["replan"]
    guard = scenarios["guard"]
    happy_report = (ARTIFACT_DIR / "happy" / "report.md").read_text(encoding="utf-8")
    insufficient_report = (ARTIFACT_DIR / "insufficient" / "report.md").read_text(encoding="utf-8")
    failure_report = (ARTIFACT_DIR / "failure" / "report.md").read_text(encoding="utf-8")
    constraints_report = (ARTIFACT_DIR / "constraints" / "report.md").read_text(encoding="utf-8")
    (ARTIFACT_DIR / "replan" / "report.md").read_text(encoding="utf-8")
    guard_report = (ARTIFACT_DIR / "guard" / "report.md").read_text(encoding="utf-8")
    happy_trace = json.loads((ARTIFACT_DIR / "happy" / "trace.json").read_text(encoding="utf-8"))
    insufficient_trace = json.loads(
        (ARTIFACT_DIR / "insufficient" / "trace.json").read_text(encoding="utf-8")
    )
    failure_trace = json.loads((ARTIFACT_DIR / "failure" / "trace.json").read_text(encoding="utf-8"))
    constraints_trace = json.loads(
        (ARTIFACT_DIR / "constraints" / "trace.json").read_text(encoding="utf-8")
    )
    replan_trace = json.loads((ARTIFACT_DIR / "replan" / "trace.json").read_text(encoding="utf-8"))
    guard_trace = json.loads((ARTIFACT_DIR / "guard" / "trace.json").read_text(encoding="utf-8"))

    lines = [
        "# 演示样例（真实运行产物，自动生成）",
        "",
        ("本页由 `python scripts/capture_demo.py` 生成，**不要手工编辑**。"
        "页面中的报告正文、事件流与计数全部来自 `docs/artifacts/demo/` 下的运行产物："),
        "",
        "| 产物 | 路径 |",
        "| --- | --- |",
        "| 正常路径报告 | [`happy/report.md`](artifacts/demo/happy/report.md) |",
        "| 正常路径 Trace | [`happy/trace.json`](artifacts/demo/happy/trace.json) |",
        "| 信息不足报告 | [`insufficient/report.md`](artifacts/demo/insufficient/report.md) |",
        "| 失败路径报告 | [`failure/report.md`](artifacts/demo/failure/report.md) |",
        "| 失败路径 Trace | [`failure/trace.json`](artifacts/demo/failure/trace.json) |",
        "| 约束型问题报告 | [`constraints/report.md`](artifacts/demo/constraints/report.md) |",
        "| Replan 报告 | [`replan/report.md`](artifacts/demo/replan/report.md) |",
        "| Guard 报告 | [`guard/report.md`](artifacts/demo/guard/report.md) |",
        "| 运行清单 | [`manifest.json`](artifacts/demo/manifest.json) |",
        "",
        "## 生成环境",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
        f"| 生成时间 | `{manifest['generated_at']}` |",
        f"| Git commit | `{manifest['git_commit']}` |",
        f"| 工作区有未提交改动 | `{manifest['git_dirty']}` |",
        f"| Python | `{manifest['python']}` |",
        f"| 平台 | `{manifest['platform']}` |",
        f"| 运行模式 | `{manifest['mode']}`（启发式规划器 + 内置/合成语料，0 token） |",
        f"| 源码树指纹 | `{manifest.get('source_manifest', {}).get('source_tree_fingerprint')}` |",
        "",
        "复现：",
        "",
        "```bash",
        "python -m venv .venv && source .venv/bin/activate",
        'python -m pip install -e ".[dev]"',
        "python scripts/capture_demo.py",
        "```",
        "",
        "## 场景一：正常路径（真实 MCP 知识库 + 并行扇出）",
        "",
        f"**问题**：{happy['question']}",
        "",
        (f"知识库后端：`{happy['kb_backend']}`；检索路：`{happy['routes_used']}`；"
        f"证据 {happy['evidence_count']} 条；来源 {happy['source_count']} 条；"
        f"计划轮数 {happy['plan_rounds']}；降级 {happy['degraded']}。"),
        "",
        "### 真实报告正文",
        "",
        _fence(happy_report),
        "",
        "### 关键行为（从 Trace 计算）",
        "",
        _counts_bullets(happy_trace, happy["plan_rounds"]),
        "",
        "### 完整 Trace 事件流",
        "",
        _trace_table(happy_trace),
        "",
        "## 场景二：信息不足（无关证据被排除）",
        "",
        f"**问题**：{insufficient['question']}",
        "",
        ("该问题不在内置知识域内。检索层确实返回了文档，但范围门把它们标记为 "
        "`unscoped_question` 并在 merge 阶段排除；原始结果保留在 Trace，"
        "最终报告不把无关证据包装成研究依据。"),
        "",
        f"- 检索到并写入 disposition：{insufficient['evidence_disposition']['adopted'] + insufficient['evidence_disposition']['excluded']} 条",
        (f"- 采用：{insufficient['evidence_disposition']['adopted']}；"
        f"排除：{insufficient['evidence_disposition']['excluded']}；"
        f"排除原因：{insufficient['evidence_disposition']['excluded_reasons']}"),
        (f"- 最终证据 {insufficient['evidence_count']} 条，来源 {insufficient['source_count']} 条，"
        f"信息缺口 {len(insufficient.get('information_gaps', []))} 条"),
        "",
        "### 结论摘要（真实报告片段）",
        "",
        _fence(_section(insufficient_report, "结论摘要")),
        "",
        "### 信息缺口（真实报告片段）",
        "",
        _fence(_section(insufficient_report, "信息缺口")),
        "",
        ("完整报告：[`insufficient/report.md`](artifacts/demo/insufficient/report.md)。"
        "该场景只证明预置 fixture 下的证据排除行为，不是在线通用语义相关率。"),
        "",
        (f"Trace：{insufficient_trace['event_count']} 个事件，"
        f"Token {insufficient_trace['token_usage']}；完整事件流见 "
        "[`insufficient/trace.json`](artifacts/demo/insufficient/trace.json)。"),
        "",
        "## 场景三：真实失败（显式故障注入）",
        "",
        f"**问题**：{failure['question']}",
        "",
        "该场景由脚本显式注入两路检索故障：",
        "",
        f"- KB：`{failure['fault_injection']['kb_client']}`",
        f"- Web：`{failure['fault_injection']['search_provider']}`",
        f"- `tool_max_retries={failure['fault_injection']['tool_max_retries']}`",
        "",
        f"> {failure['fault_injection']['note']}",
        "",
        ("即使两路都失败，主图仍产出报告：没有证据时明确说“无法给出有依据的结论”，"
        "并把失败写入信息缺口，而不是包装成成功。"),
        "",
        "### 真实报告正文",
        "",
        _fence(failure_report),
        "",
        "### 关键行为（从 Trace 计算）",
        "",
        _counts_bullets(failure_trace, failure["plan_rounds"]),
        "",
        "### 完整 Trace 事件流",
        "",
        _trace_table(failure_trace),
        "",
        "## 场景四：用户约束真正影响结论（离线合成 fixture）",
        "",
        f"**问题**：{constraints['question']}",
        "",
        f"> {constraints.get('fixture_note', '')}",
        "",
        ("结构化约束核验结果："
        f"verdict=`{constraints['constraint_summary']['verdict']}`，"
        f"推荐={constraints['constraint_summary']['recommended_objects']}，"
        f"排除={constraints['constraint_summary']['excluded']}，"
        f"未知={constraints['constraint_summary']['unknown_objects']}。"),
        "",
        "### 约束核对与结论（真实报告片段）",
        "",
        _fence(_section(constraints_report, "约束核对")),
        "",
        ("完整报告：[`constraints/report.md`](artifacts/demo/constraints/report.md)。"
        f"Trace：{constraints_trace['event_count']} 个事件。"),
        "",
        "## 场景五：确定性 replan（脚本化检索 fixture）",
        "",
        f"**问题**：{replan['question']}",
        "",
        f"> {replan.get('fixture_note', '')}",
        "",
        (f"- 计划轮数：{replan['plan_rounds']}；Trace 中 `plan` 事件："
        f"{len(_find(replan_trace, 'plan'))} 次；`dispatch`："
        f"{len(_find(replan_trace, 'dispatch'))} 次"),
        (f"- 第二轮子任务维度："
        f"{sorted({s['aspect'] for e in _find(replan_trace, 'plan')[1:] for s in e['payload'].get('subtasks', [])})}"),
        (f"- 第二轮检索词："
        f"{[e['payload'].get('query') for e in _find(replan_trace, 'tool_call')][-4:]}"),
        f"- 首轮 verify 缺口：{_find(replan_trace, 'verify')[0]['payload'].get('gaps') if _find(replan_trace, 'verify') else []}",
        f"- 最终 verify 缺口：{_find(replan_trace, 'verify')[-1]['payload'].get('gaps') if _find(replan_trace, 'verify') else []}",
        f"- 最终信息缺口：{replan['information_gaps']}",
        "",
        ("完整报告：[`replan/report.md`](artifacts/demo/replan/report.md)；"
        f"Trace：{replan_trace['event_count']} 个事件。该测试只证明离线 replan 行为，不代表在线 replan 质量。"),
        "",
        "## 场景六：引用 Guard（受控模型输出）",
        "",
        f"> {guard.get('fixture_note', '')}",
        "",
        "### Guard 前原始模型输出",
        "",
        _fence((guard.get("guard_audit") or {}).get("raw_markdown", "")),
        "",
        "### Guard 后最终报告",
        "",
        _fence(guard_report),
        "",
        "### 结构化差异",
        "",
        _fence((guard.get("guard_audit") or {}).get("diff", "")),
        "",
        "### 被隔离结论",
        "",
        "| location | claim | invalid IDs | reason |",
        "| --- | --- | --- | --- |",
    ]
    for claim in (guard.get("guard_audit") or {}).get("unverified_claims", []):
        lines.append(
            f"| `{claim['location']}` | {claim['text'][:120]} | "
            f"{claim['invalid_citation_ids']} | {claim['reason']} |"
        )
    ga = guard.get("guard_audit") or {}
    lines += [
        "",
        (f"- 检查结论 {ga.get('claims_checked', 0)} 条；隔离 {ga.get('claims_quarantined', 0)} 条；"
        f"重写尝试 {ga.get('rewrite_attempted')}，接受 {ga.get('rewrite_accepted')}，"
        f"拒绝原因 `{ga.get('rewrite_rejected_reason')}`"),
        f"- 最终动作：{[a['action'] for a in ga.get('actions', [])]}",
        f"- Guard 事件：{[e['event_type'] for e in guard_trace['events'] if e['payload'].get('stage') == 'citation_guard']}",
        "",
        ("完整报告：[`guard/report.md`](artifacts/demo/guard/report.md)；"
        f"Trace：{guard_trace['event_count']} 个事件。"),
        "",
        "## 这不是在线效果评测",
        "",
        ("以上运行使用离线启发式规划器与内置演示语料，Token 为 0，"
        "不能用来宣称真实模型效果。在线评测方法、指标定义与运行命令见 "
        "[`docs/evaluation.md`](evaluation.md)。"),
        "",
        ("在线评测当前状态：见 [`artifacts/online/NOT_RUN.json`](artifacts/online/NOT_RUN.json)"
        "（若该文件不存在，说明尚未执行过在线 Runner 的 preflight）。"),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if ARTIFACT_DIR.exists():
        shutil.rmtree(ARTIFACT_DIR)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    scenarios = {
        "happy": run_happy(),
        "insufficient": run_insufficient(),
        "failure": run_failure(),
        "constraints": run_constraints(),
        "replan": run_replan(),
        "guard": run_guard(),
    }
    manifest = {
        "generated_at": now_iso(),
        "git_commit": git_commit(PROJECT_ROOT),
        "git_dirty": git_dirty(PROJECT_ROOT),
        "source_manifest": source_manifest(PROJECT_ROOT),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "mode": "offline",
        "note": "所有报告、Trace 与计数均由 scripts/capture_demo.py 真实执行生成。",
        "scenarios": scenarios,
    }
    (ARTIFACT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    demo_md = render_demo_md(manifest, {k: v for k, v in scenarios.items()})
    (PROJECT_ROOT / "docs" / "demo.md").write_text(demo_md, encoding="utf-8")

    shutil.rmtree(RUNTIME_DIR, ignore_errors=True)
    print(f"已写入 docs/demo.md 与 {ARTIFACT_DIR.relative_to(PROJECT_ROOT)}/")
    print(f"happy: {scenarios['happy']['evidence_count']} 证据, "
          f"routes={scenarios['happy']['routes_used']}")
    print(f"failure: degraded={scenarios['failure']['degraded']}, "
          f"gaps={len(scenarios['failure']['information_gaps'])}")
    cs = scenarios["constraints"]["constraint_summary"]
    print(f"constraints: verdict={cs['verdict']}, recommended={cs['recommended_objects']}, "
          f"excluded={cs['excluded']}")
    print(f"replan: plan_rounds={scenarios['replan']['plan_rounds']}, "
          f"plan_events={scenarios['replan']['plan_event_count']}")
    ga = scenarios["guard"]["guard_audit"]
    print(f"guard: quarantined={ga['claims_quarantined']}, "
          f"invalid={ga['invalid_citation_ids']}, fallback={ga['fallback_used']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())