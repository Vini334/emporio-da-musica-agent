"""Evaluation runner (SPEC §12): plays every case of ``eval/cases.yaml`` against the real agent
and writes ``docs/eval_results.md``.

    uv run python -m eval.run                       # all cases
    uv run python -m eval.run --only pedido-sem-contato,promo-inativa-takamine
    uv run python -m eval.run --category preco --delay 5
    uv run python -m eval.run --judge                # + LLM as judge for tone (eval/judge.py)

Each case runs in a fresh conversation (its own thread in an in-memory checkpointer) with the
clock pinned through ``EMPORIO_NOW``. Hand-offs go to a temporary queue, so the evaluation never
touches ``storage/handoffs.jsonl``. A turn whose model call failed (rate limit after the retries,
network) is reported as an *error*, not as a failure of the agent. The exit code is 1 when any
case fails or errors, so the runner can gate a CI job. The judge's scores are informative and do
not affect the status or the exit code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from langgraph.checkpoint.memory import InMemorySaver

from emporio import config
from emporio.agent import MODEL_ERROR_REPLY, Assistant, Reply, build_agent
from eval.checks import Expect, check_turn
from eval.judge import CRITERIA, ToneScore, build_judge, judge_case, judge_model_name

EVAL_DIR = Path(__file__).resolve().parent
CASES_FILE = EVAL_DIR / "cases.yaml"
REPORT_FILE = config.DOCS_DIR / "eval_results.md"
RAW_FILE = config.STORAGE_DIR / "eval_last_run.json"
DEFAULT_NOW = "2026-11-03T10:00:00"  # Tuesday, store open

CATEGORY_LABELS = {
    "catalogo": "Catalog",
    "preco": "Price and promotion",
    "pagamento": "Payment and shipping",
    "pedido": "Orders and LGPD",
    "devolucao": "Exchanges and returns",
    "politicas": "Policies (RAG)",
    "horario": "Opening hours",
    "fora_escopo": "Out of scope",
    "injection": "Injection and social engineering",
    "multi_turno": "Multi-turn",
}
EXCERPT_CHARS = 700
LOWEST_TONE_CASES = 5


# --------------------------------------------------------------------------- cases


@dataclass(frozen=True)
class Turn:
    user: str
    expect: Expect


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    turns: list[Turn]
    now: str = DEFAULT_NOW
    note: str = ""


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    """Parse the YAML. A turn is a string or ``{user, expect}``; the case-level ``expect``
    applies to the last turn."""
    raw_cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases, seen = [], set()
    for raw in raw_cases:
        case_id = raw["id"]
        if case_id in seen:
            raise ValueError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        if raw["category"] not in CATEGORY_LABELS:
            raise ValueError(f"{case_id}: unknown category {raw['category']!r}")
        turns = [
            Turn(t, Expect())
            if isinstance(t, str)
            else Turn(t["user"], Expect.from_dict(t.get("expect")))
            for t in raw["turns"]
        ]
        if "expect" in raw:
            if not turns[-1].expect.is_empty:
                raise ValueError(f"{case_id}: expect on both the case and its last turn")
            turns[-1] = Turn(turns[-1].user, Expect.from_dict(raw["expect"]))
        if all(turn.expect.is_empty for turn in turns):
            raise ValueError(f"{case_id}: case without any expectation")
        cases.append(
            Case(
                id=case_id,
                category=raw["category"],
                turns=turns,
                now=str(raw.get("now", DEFAULT_NOW)),
                note=raw.get("note", ""),
            )
        )
    return cases


# --------------------------------------------------------------------------- running


@dataclass
class TurnResult:
    user: str
    reply: str
    tools: list[dict[str, Any]]
    failures: list[str]
    error: str | None = None


@dataclass
class CaseResult:
    id: str
    category: str
    now: str
    seconds: float
    turns: list[TurnResult] = field(default_factory=list)
    tone: ToneScore | None = None  # filled only with --judge

    @property
    def status(self) -> str:
        if any(turn.error for turn in self.turns):
            return "error"
        return "failed" if any(turn.failures for turn in self.turns) else "passed"

    @property
    def tools_used(self) -> list[str]:
        return [tool["name"] for turn in self.turns for tool in turn.tools]


def run_case(case: Case, agent) -> CaseResult:
    os.environ["EMPORIO_NOW"] = case.now
    assistant = Assistant(agent, session_id=f"eval-{case.id}-{time.time_ns()}")
    result = CaseResult(case.id, case.category, case.now, seconds=0.0)
    started = time.perf_counter()
    for turn in case.turns:
        try:
            reply = assistant.send(turn.user)
        except Exception as exc:  # noqa: BLE001 - any failure is reported, the run goes on
            result.turns.append(TurnResult(turn.user, "", [], [], error=repr(exc)))
            break
        error = "the model failed after the retries" if _is_model_error(reply) else None
        result.turns.append(
            TurnResult(
                user=turn.user,
                reply=reply.text,
                tools=[{"name": c.name, "args": c.args} for c in reply.tool_calls],
                failures=[] if error else check_turn(turn.expect, reply),
                error=error,
            )
        )
        if error:
            break
    result.seconds = round(time.perf_counter() - started, 1)
    return result


def _is_model_error(reply: Reply) -> bool:
    return reply.text == MODEL_ERROR_REPLY


# --------------------------------------------------------------------------- report

_STATUS_ICONS = {"passed": "✅", "failed": "❌", "error": "⚠️"}


def render_report(
    results: list[CaseResult],
    model: str,
    started_at: datetime,
    judge_model: str | None = None,
) -> str:
    total = len(results)
    counts = Counter(r.status for r in results)
    passed = counts["passed"]
    lines = [
        "# Agent evaluation results",
        "",
        "Generated by `uv run python -m eval.run` from "
        "[`eval/cases.yaml`](../eval/cases.yaml). Each case runs in a fresh conversation, with "
        "the clock pinned, against the real model; the checks are deterministic (tools called, "
        "arguments, and snippets that must or must not appear in the reply). The agent answers "
        "in Portuguese, so the expected snippets are in Portuguese. See "
        "[`SPEC.md` §12](SPEC.md#12-quality).",
        "",
        f"- Run: {started_at:%Y-%m-%d %H:%M}",
        f"- Model: `{model}`",
        f"- Cases: {total} · ✅ {passed} passed · ❌ {counts['failed']} failed · "
        f"⚠️ {counts['error']} infrastructure errors",
        f"- **Pass rate: {_pct(passed, total)}**"
        + (
            f" ({_pct(passed, total - counts['error'])} excluding infrastructure errors)"
            if counts["error"]
            else ""
        ),
        "",
        "## By category",
        "",
        "| Category | Cases | Passed | Rate |",
        "|---|---:|---:|---:|",
    ]
    for category, label in CATEGORY_LABELS.items():
        group = [r for r in results if r.category == category]
        if group:
            ok = sum(r.status == "passed" for r in group)
            lines.append(f"| {label} | {len(group)} | {ok} | {_pct(ok, len(group))} |")

    lines += [
        "",
        "## By case",
        "",
        "| Case | Category | Result | Tools called | Time |",
        "|---|---|---|---|---:|",
    ]
    for r in results:
        tools = ", ".join(f"`{name}`" for name in r.tools_used) or "—"
        lines.append(
            f"| `{r.id}` | {CATEGORY_LABELS[r.category]} | {_STATUS_ICONS[r.status]} {r.status} "
            f"| {tools} | {r.seconds:.0f} s |"
        )

    problems = [r for r in results if r.status != "passed"]
    lines += ["", "## Failures and errors", ""]
    if not problems:
        lines.append("None.")
    for r in problems:
        lines += [f"### `{r.id}` ({r.status})", ""]
        for index, turn in enumerate(r.turns, start=1):
            if not (turn.failures or turn.error):
                continue
            lines.append(f"- Turn {index}: _{turn.user}_")
            for failure in turn.failures:
                lines.append(f"  - {failure}")
            if turn.error:
                lines.append(f"  - error: {turn.error}")
            if turn.reply:
                excerpt = " ".join(turn.reply.split())
                if len(excerpt) > EXCERPT_CHARS:
                    excerpt = excerpt[: EXCERPT_CHARS - 1] + "…"
                lines.append(f"  - reply: > {excerpt}")
        lines.append("")
    if judge_model:
        lines += render_tone_section(results, judge_model)
    return "\n".join(lines).rstrip() + "\n"


def render_tone_section(results: list[CaseResult], judge_model: str) -> list[str]:
    judged = [r for r in results if r.tone and r.tone.scores]
    skipped = len(results) - len(judged)
    lines = [
        "## Tone and courtesy (LLM as judge)",
        "",
        f"Scores from 1 to 5 given by `{judge_model}` to each conversation, with the rubric in "
        "[`eval/judge.py`](../eval/judge.py) (Dó's persona, manual §7.1). The judge scores only "
        "the form, not whether the data is correct, and does **not** change the case results "
        "above.",
        "",
    ]
    if not judged:
        return lines + ["No case judged.", ""]
    lines += [
        f"- Cases judged: {len(judged)}"
        + (f" ({skipped} without a score due to a model or judge error)" if skipped else ""),
        f"- **Overall mean: {_mean(r.tone.mean for r in judged)}**",
        "",
        "| Criterion | Mean | Scores ≤ 3 |",
        "|---|---:|---:|",
    ]
    for key, label in CRITERIA.items():
        values = [r.tone.scores[key] for r in judged]
        low = sum(value <= 3 for value in values)
        lines.append(f"| {label} | {_mean(values)} | {low} |")

    lines += [
        "",
        "| Category | " + " | ".join(CRITERIA.values()) + " |",
        "|---|" + "---:|" * len(CRITERIA),
    ]
    for category, label in CATEGORY_LABELS.items():
        group = [r for r in judged if r.category == category]
        if group:
            means = [_mean(r.tone.scores[key] for r in group) for key in CRITERIA]
            lines.append(f"| {label} | " + " | ".join(means) + " |")

    lowest = sorted(judged, key=lambda r: (r.tone.mean, r.id))[:LOWEST_TONE_CASES]
    lines += ["", f"### The {len(lowest)} conversations with the lowest scores", ""]
    for r in lowest:
        scores = " · ".join(f"{CRITERIA[k].lower()} {v}" for k, v in r.tone.scores.items())
        lines.append(f"- `{r.id}` ({scores}): {r.tone.comment}")
    lines.append("")
    return lines


def _mean(values) -> str:
    values = list(values)
    return f"{sum(values) / len(values):.1f}" if values else "—"


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "—"


# --------------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="eval.run", description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, default=CASES_FILE)
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--category", help="run a single category")
    parser.add_argument("--model", help="overrides LLM_MODEL (e.g. google_genai:...)")
    parser.add_argument("--output", type=Path, default=REPORT_FILE, help="Markdown report")
    parser.add_argument("--raw", type=Path, default=RAW_FILE, help="full replies (JSON)")
    parser.add_argument(
        "--delay", type=float, default=0.0, help="pause in seconds between cases (rate limit)"
    )
    parser.add_argument(
        "--judge", action="store_true", help="score each conversation's tone with an LLM judge"
    )
    parser.add_argument(
        "--judge-model", help="judge model (default: JUDGE_MODEL or, without it, LLM_MODEL)"
    )
    return parser.parse_args(argv)


def select(cases: list[Case], only: str | None, category: str | None) -> list[Case]:
    if only:
        wanted = {item.strip() for item in only.split(",") if item.strip()}
        missing = wanted - {case.id for case in cases}
        if missing:
            raise SystemExit(f"unknown cases: {', '.join(sorted(missing))}")
        cases = [case for case in cases if case.id in wanted]
    if category:
        cases = [case for case in cases if case.category == category]
    return cases


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    cases = select(load_cases(args.cases), args.only, args.category)
    if not cases:
        raise SystemExit("no case selected")

    started_at = datetime.now()
    model = config.llm_model()
    judge_model = (args.judge_model or judge_model_name()) if args.judge else None
    judge = build_judge(judge_model) if judge_model else None
    results: list[CaseResult] = []
    with tempfile.TemporaryDirectory() as tmp:
        config.HANDOFFS_FILE = Path(tmp) / "handoffs.jsonl"
        agent = build_agent(checkpointer=InMemorySaver())
        for index, case in enumerate(cases, start=1):
            if index > 1 and args.delay:
                time.sleep(args.delay)
            result = run_case(case, agent)
            if judge is not None:
                result.tone = judge_case(judge, result)
            results.append(result)
            print(
                f"[{index:>2}/{len(cases)}] {_STATUS_ICONS[result.status]} {case.id} "
                f"({result.seconds:.0f} s){_tone_suffix(result.tone)}",
                flush=True,
            )
            for turn in result.turns:
                for failure in turn.failures:
                    print(f"        - {failure}", flush=True)
                if turn.error:
                    print(f"        - error: {turn.error}", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = render_report(results, model, started_at, judge_model)
    args.output.write_text(report, encoding="utf-8")
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    raw = [asdict(r) | {"status": r.status} for r in results]
    args.raw.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(r.status == "passed" for r in results)
    print(f"\n{passed}/{len(results)} cases passed · report at {args.output}")
    return 0 if passed == len(results) else 1


def _tone_suffix(tone: ToneScore | None) -> str:
    if tone is None:
        return ""
    if tone.scores:
        return f" · tone {tone.mean:.1f}"
    return f" · judge: {tone.error}"


if __name__ == "__main__":
    sys.exit(main())
