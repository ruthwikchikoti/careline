"""The multi-node LangGraph orchestration graph (RU-4).

This is the multi-agent face of CareLine: a compiled LangGraph ``StateGraph``
that runs the same safety spine as the headless :class:`~careline.domain.brain.brain.Brain`,
but as explicit, observable agent nodes with a conditional route on the verdict.

```
START → triage → retrieve → reason → verify → gate ─┬─ answer   → END
            │                  │         │           ├─ clarify  → END
            └──────────────────┴─────────┴──────────►└─ escalate → END
   (triage red-flag, reasoner/verifier unavailable: early-exit straight to escalate)
```

The crucial design rule (and the project's core IP): **the graph re-implements no
safety logic.** Every node calls the exact same domain primitive the Brain calls —
``check_red_flag`` / ``check_multi_condition`` in *triage*, ``patient.valid_slice``
in *retrieve*, the injected ``Reasoner`` / ``Verifier`` ports in *reason* / *verify*,
and ``run_gate_chain`` in *gate*. So the graph's verdict is identical to the Brain's
by construction — a property the parity test (RU-5) locks down. Adding the graph can
never change a safety decision; it only adds the agent decomposition and observability
the rubric asks for.

One typed :class:`GraphState` is threaded across every node; a single ``now`` drives
all temporal checks; exactly one terminal node (answer/clarify/escalate) records the
``route`` taken.

Owner: Ruthwik (scope ``graph``).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from careline.domain.enums import ScopeCategory, TraceStatus
from careline.domain.gates.chain import GateContext, run_gate_chain
from careline.domain.model.call_session import CallSession
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.domain.model.patient import Patient, ValidSlice
from careline.domain.model.proposal import ClassifierProposal, VerificationResult
from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier
from careline.domain.rails.red_flag import check_multi_condition, check_red_flag
from careline.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds

#: The explicit agent nodes, in spine order — used for the architecture diagram.
AGENT_NODES: tuple[str, ...] = ("triage", "retrieve", "reason", "verify", "gate")
TERMINAL_NODES: tuple[str, ...] = ("answer", "clarify", "escalate")


class GraphState(TypedDict, total=False):
    """The single typed state threaded across every node.

    Inputs (``question``/``patient``/``now``/``session``/``thresholds``/``trace``) are
    seeded once; each node contributes the slice it computes (``valid_slice``,
    ``proposal``, ``verification``) and the pipeline terminates by setting
    ``decision``. The chosen branch is recorded in ``route``.
    """

    question: str
    patient: Patient
    now: datetime
    session: CallSession | None
    thresholds: Thresholds
    trace: ReasoningTrace
    valid_slice: ValidSlice
    proposal: ClassifierProposal | None
    verification: VerificationResult | None
    decision: Decision | None
    route: str


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def _build_compiled(reasoner: Reasoner, verifier: Verifier):
    """Wire the nodes and edges into a compiled StateGraph."""

    # -- nodes (each delegates to the same domain logic as the Brain) ---------

    def triage(state: GraphState) -> dict:
        question = state["question"]
        trace = state["trace"]

        matched = check_red_flag(question)
        if matched:
            trace.record(
                "red_flag_rail",
                TraceStatus.TERMINAL,
                spec_section="§5.1",
                detail=f"emergency keyword matched: {matched!r}",
            )
            return {
                "decision": Decision.escalate(
                    f"Emergency symptom detected ({matched}) — transferring to your doctor.",
                    scope=ScopeCategory.RED_FLAG,
                    risk=1.0,
                    trace=trace,
                )
            }

        is_cross, groups = check_multi_condition(question)
        if is_cross:
            trace.record(
                "multi_condition_tripwire",
                TraceStatus.TERMINAL,
                spec_section="§5.3",
                detail=f"question spans conditions: {', '.join(groups)}",
            )
            return {
                "decision": Decision.escalate(
                    "Question spans multiple clinical conditions — transferring to your doctor.",
                    scope=ScopeCategory.CROSS_CONDITION,
                    risk=0.95,
                    trace=trace,
                )
            }
        return {}

    def retrieve(state: GraphState) -> dict:
        return {"valid_slice": state["patient"].valid_slice(state["now"])}

    def reason(state: GraphState) -> dict:
        try:
            proposal = reasoner.propose(
                question=state["question"], context=state["valid_slice"]
            )
        except ReasonerUnavailable:
            state["trace"].record(
                "reasoner", TraceStatus.TERMINAL, detail="reasoner unavailable — fail closed"
            )
            return {
                "decision": Decision.escalate(
                    "Unable to process your question safely — transferring to your doctor.",
                    trace=state["trace"],
                )
            }
        return {"proposal": proposal}

    def verify(state: GraphState) -> dict:
        proposal = state["proposal"]
        if proposal is None or not proposal.is_answerable:
            return {"verification": None}
        try:
            result = verifier.verify(
                question=state["question"], proposal=proposal, context=state["valid_slice"]
            )
        except ReasonerUnavailable:
            state["trace"].record(
                "verifier", TraceStatus.TERMINAL, detail="verifier unavailable — fail closed"
            )
            return {
                "decision": Decision.escalate(
                    "Unable to verify an answer safely — transferring to your doctor.",
                    trace=state["trace"],
                )
            }
        return {"verification": result}

    def gate(state: GraphState) -> dict:
        ctx = GateContext(
            question=state["question"],
            proposal=state["proposal"],
            verification=state.get("verification"),
            valid_slice=state["valid_slice"],
            thresholds=state["thresholds"],
            now=state["now"],
            call_session=state.get("session"),
            trace=state["trace"],
        )
        return {"decision": run_gate_chain(ctx)}

    def answer(_state: GraphState) -> dict:
        return {"route": "answer"}

    def clarify(_state: GraphState) -> dict:
        return {"route": "clarify"}

    def escalate(_state: GraphState) -> dict:
        return {"route": "escalate"}

    # -- routing helpers ------------------------------------------------------

    def _terminated_or(next_node: str):
        """Early-exit to escalate if a node already produced a (terminal) decision."""
        def _router(state: GraphState) -> str:
            return "escalate" if state.get("decision") is not None else next_node
        return _router

    def _route_on_verdict(state: GraphState) -> str:
        return state["decision"].verdict.value  # "answer" | "clarify" | "escalate"

    # -- assembly -------------------------------------------------------------

    g = StateGraph(GraphState)
    for name, fn in (
        ("triage", triage),
        ("retrieve", retrieve),
        ("reason", reason),
        ("verify", verify),
        ("gate", gate),
        ("answer", answer),
        ("clarify", clarify),
        ("escalate", escalate),
    ):
        g.add_node(name, fn)

    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", _terminated_or("retrieve"),
                            {"retrieve": "retrieve", "escalate": "escalate"})
    g.add_edge("retrieve", "reason")
    g.add_conditional_edges("reason", _terminated_or("verify"),
                            {"verify": "verify", "escalate": "escalate"})
    g.add_conditional_edges("verify", _terminated_or("gate"),
                            {"gate": "gate", "escalate": "escalate"})
    # The required conditional decision: route on the gate's verdict.
    g.add_conditional_edges("gate", _route_on_verdict,
                            {"answer": "answer", "clarify": "clarify", "escalate": "escalate"})
    for terminal in TERMINAL_NODES:
        g.add_edge(terminal, END)

    return g.compile()


class CompiledBrainGraph:
    """A compiled CareLine graph that exposes the same call surface as the Brain.

    ``run_question`` seeds the typed state, invokes the graph, and returns the
    terminal :class:`Decision` — so the graph is a drop-in for the Brain and the
    parity test can compare them directly.
    """

    def __init__(self, compiled, *, thresholds: Thresholds) -> None:
        self._app = compiled
        self._thresholds = thresholds

    @property
    def compiled(self):
        """The underlying compiled LangGraph (for inspection / drawing)."""
        return self._app

    def run_question(
        self,
        *,
        question: str,
        patient: Patient,
        now: datetime | None = None,
        session: CallSession | None = None,
        trace: ReasoningTrace | None = None,
    ) -> Decision:
        final = self.final_state(
            question=question, patient=patient, now=now, session=session, trace=trace
        )
        return final["decision"]

    def final_state(
        self,
        *,
        question: str,
        patient: Patient,
        now: datetime | None = None,
        session: CallSession | None = None,
        trace: ReasoningTrace | None = None,
    ) -> GraphState:
        """Invoke the graph and return the full final state (for debugging/inspection)."""
        initial: GraphState = {
            "question": question,
            "patient": patient,
            "now": now or datetime.now(timezone.utc),
            "session": session,
            "thresholds": self._thresholds,
            "trace": trace if trace is not None else ReasoningTrace(),
            "decision": None,
        }
        return self._app.invoke(initial)

    def mermaid(self) -> str:
        """Render the compiled topology as a Mermaid diagram (for the arch doc)."""
        return self._app.get_graph().draw_mermaid()


def build_question_graph(
    *,
    reasoner: Reasoner,
    verifier: Verifier,
    thresholds: Thresholds | None = None,
) -> CompiledBrainGraph:
    """Build and compile the multi-node question graph with the injected ports."""
    return CompiledBrainGraph(
        _build_compiled(reasoner, verifier),
        thresholds=thresholds or DEFAULT_THRESHOLDS,
    )


def resolve_llm_config(config=None):
    """Pick the reasoning backend — **a real LLM is the primary path** (RU-6).

    Resolution order:
      1. An explicit ``config`` argument, if given.
      2. ``CARELINE_LLM_BACKEND`` env (the team's documented override).
      3. ``OPENAI_API_KEY`` present → OpenAI (``gpt-5.5``, Responses API).
      4. ``ANTHROPIC_API_KEY`` present → Anthropic (``claude-opus-4-8``).
      5. Otherwise → the keyless heuristic twins, as an offline **fallback** only
         (so CI / the test suite still run with no key).

    A live LLM is preferred whenever a key is available; the heuristic is a
    stand-in, never the intended production reasoner.
    """
    from careline.adapters.factory import LLMConfig, LLMBackend

    if config is not None:
        return config
    if os.environ.get("CARELINE_LLM_BACKEND"):
        return LLMConfig.from_env()

    environment = os.environ.get("CARELINE_ENV", "dev").lower()
    model = os.environ.get("CARELINE_LLM_MODEL") or None
    effort = os.environ.get("CARELINE_LLM_EFFORT", "high")

    if os.environ.get("OPENAI_API_KEY"):
        return LLMConfig(
            backend=LLMBackend.OPENAI,
            environment=environment,
            model=model,
            effort=effort,
            api_key=os.environ["OPENAI_API_KEY"],
        )
    if os.environ.get("ANTHROPIC_API_KEY"):
        return LLMConfig(
            backend=LLMBackend.ANTHROPIC,
            environment=environment,
            model=model,
            effort=effort,
            api_key=os.environ["ANTHROPIC_API_KEY"],
        )
    return LLMConfig()  # heuristic fallback (keyless, offline)


def build_default_graph(config=None, *, thresholds: Thresholds | None = None) -> CompiledBrainGraph:
    """Assemble the graph from the adapter factory — the composition entry point (RU-6).

    Prefers a live LLM backend (OpenAI/Anthropic) when a key is present and falls
    back to the keyless heuristic twins offline — see :func:`resolve_llm_config`.
    This is what the API lifespan wires into ``app.state``. The factory import is
    local so selecting an LLM backend never imports a vendor SDK at module load.
    """
    from careline.adapters.factory import build_reasoner, build_verifier

    config = resolve_llm_config(config)
    return build_question_graph(
        reasoner=build_reasoner(config),
        verifier=build_verifier(config),
        thresholds=thresholds,
    )


__all__ = [
    "GraphState",
    "CompiledBrainGraph",
    "build_question_graph",
    "build_default_graph",
    "resolve_llm_config",
    "AGENT_NODES",
    "TERMINAL_NODES",
]
