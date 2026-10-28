"""报告合成与引用校验包。

不在包初始化时 eager import synth：synth 依赖 app.llm.base，而 app.llm
包初始化会导入 heuristic，heuristic 又依赖 synth；懒加载可避免循环导入。
"""

__all__ = ["render_compose_result"]


def __getattr__(name: str):
    if name == "render_compose_result":
        from app.report.synth import render_compose_result

        return render_compose_result
    raise AttributeError(name)