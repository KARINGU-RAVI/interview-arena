"""Wire the 12 nodes into one LangGraph StateGraph.

extract -> gap_analysis -> start_gate(pause) -> plan_questions -> ask_question(pause)
-> evaluate_answer -> route_next: more questions? ask_question : follow_up / final_evaluation
-> follow_up -> evaluate_follow_up(pause) -> final_evaluation -> build_report
-> send_gate(pause) -> deliver -> (gmail: back to send_gate | finish: END)

InMemorySaver saves the state after every step, so a paused session can be resumed with
graph.invoke(Command(resume=...), {"configurable": {"thread_id": ...}}, context=SessionContext).
Build the graph ONCE and reuse it (Streamlit will cache it); each session has its own thread_id.
"""

from __future__ import annotations

import threading

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from graph import nodes
from graph.state import InterviewState, SessionContext


def build_graph():
    g = StateGraph(InterviewState, context_schema=SessionContext)

    g.add_node("extract", nodes.extract)
    g.add_node("gap_analysis", nodes.gap_analysis)
    g.add_node("start_gate", nodes.start_gate)
    g.add_node("plan_questions", nodes.plan_questions_node)
    g.add_node("ask_question", nodes.ask_question)
    g.add_node("evaluate_answer", nodes.evaluate_answer)
    g.add_node("follow_up", nodes.follow_up)
    g.add_node("evaluate_follow_up", nodes.evaluate_follow_up)
    g.add_node("final_evaluation", nodes.final_evaluation)
    g.add_node("build_report", nodes.build_report)
    g.add_node("send_gate", nodes.send_gate)
    g.add_node("deliver", nodes.deliver)

    g.add_edge(START, "extract")
    g.add_edge("extract", "gap_analysis")
    g.add_edge("gap_analysis", "start_gate")
    g.add_edge("start_gate", "plan_questions")
    g.add_edge("plan_questions", "ask_question")
    g.add_edge("ask_question", "evaluate_answer")
    # The loop: route_next is a conditional EDGE (a function), not a node.
    g.add_conditional_edges(
        "evaluate_answer", nodes.route_next, ["ask_question", "follow_up", "final_evaluation"]
    )
    g.add_edge("follow_up", "evaluate_follow_up")
    g.add_edge("evaluate_follow_up", "final_evaluation")
    g.add_edge("final_evaluation", "build_report")
    g.add_edge("build_report", "send_gate")
    g.add_edge("send_gate", "deliver")
    # Gmail draft -> back to send_gate (download / finish still available); Finish -> END.
    g.add_conditional_edges("deliver", nodes.route_after_deliver, {"send_gate": "send_gate", "end": END})

    return g.compile(checkpointer=InMemorySaver())


_shared = None
_shared_lock = threading.Lock()


def get_shared_graph():
    """One graph (and checkpointer) per process. The Streamlit app caches this; tests can
    reach the same instance to set up a session state."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = build_graph()
        return _shared
