"""Repro 2: lifecycle of start -> END pause -> resume via re-invocation, using the REAL graph topology
(agentic.graph.workflow.create_workflow) with node bodies replaced by recorders, plus the real
resume_workflow() with a fake FirestoreClient. No network, no Firestore."""
import asyncio, json, os
from langgraph.checkpoint.memory import MemorySaver
import agentic.graph.workflow as wf
import agentic.firestore.client as fsclient
import agentic.graph.corrections as corrections

calls = []
def mk(name, patch):
    async def node(state):
        calls.append(name)
        return {**state, **patch(state)}
    return node

# Recorders mimic each node's *state* effect (not Firestore side effects)
wf.load_study_node = mk("load_study", lambda s: {"user_id": "u1", "rooms": s.get("rooms", [])})
wf.resource_extraction_node = mk("resource_extraction", lambda s: {"current_stage": "resource_extraction", "needs_review": True, "appraisal_resources": {"ingested": True}})
wf.analyze_rooms_node = mk("analyze_rooms", lambda s: {"rooms_ready": True})
wf.reviewing_rooms_node = mk("reviewing_rooms", lambda s: {"current_stage": "reviewing_rooms", "needs_review": True})
wf.process_assets_node = mk("process_assets", lambda s: {"current_stage": "engineering_takeoff", "needs_review": True})
wf.complete_workflow_node = mk("complete", lambda s: {"current_stage": "completed", "needs_review": False})
wf.error_handler_node = mk("error_handler", lambda s: {})

# ---- Part 1: pure LangGraph semantics with a SHARED MemorySaver -------------
saver = MemorySaver()
app = wf.create_workflow(checkpointer=saver)
cfg = {"configurable": {"thread_id": "study_1"}}
print("graph nodes:", list(app.get_graph().nodes.keys()))
edges = [(e.source, e.target) for e in app.get_graph().edges]
print("edges:", edges)
reachable = {"__start__"}; frontier = ["__start__"]
while frontier:
    n = frontier.pop()
    for s, t in edges:
        if s == n and t not in reachable:
            reachable.add(t); frontier.append(t)
print("UNREACHABLE from START:", sorted(set(app.get_graph().nodes) - reachable))

async def part1():
    calls.clear()
    s = await app.ainvoke(wf.create_initial_state("study_1"), cfg)
    print("\n[start] nodes run:", calls, "| current_stage:", s["current_stage"], "| needs_review:", s["needs_review"])
    snap = app.get_state(cfg)
    print("[start] checkpoint next tasks:", snap.next, "(empty = graph finished, nothing pending)")

    # What resume_workflow() does for 'reviewing_rooms' approved: ainvoke(update, same thread)
    calls.clear()
    update = {"study_id": "study_1", "engineer_approved": True, "engineer_corrections": [], "needs_review": False, "pending_jobs": [], "current_stage": "processing_assets"}
    s = await app.ainvoke(update, cfg)
    print("[resume as reviewing_rooms->processing_assets] nodes run:", calls, "| current_stage:", s["current_stage"])

    calls.clear()
    update["current_stage"] = "completed"
    s = await app.ainvoke(update, cfg)
    print("[resume as engineering_takeoff->completed] nodes run:", calls, "| current_stage:", s["current_stage"])

    # Does ainvoke(None) (the documented 'continue from checkpoint' call) do anything?
    calls.clear()
    try:
        s = await app.ainvoke(None, cfg)
        print("[ainvoke(None)] nodes run:", calls, "| current_stage:", s["current_stage"])
    except Exception as e:
        print("[ainvoke(None)] raised:", type(e).__name__, str(e)[:120])

asyncio.run(part1())

# ---- Part 2: the real resume_workflow() with a fake FirestoreClient ----------
written = []
class FakeFS:
    def __init__(self, *a, **k): pass
    def get_study(self, sid): return {"workflowStatus": STAGE, "roomsReady": True, "userId": "u1"}
    def update_study(self, sid, data): written.append(("update_study", data))
    def update_workflow_status(self, sid, st): written.append(("update_workflow_status", st))
fsclient.FirestoreClient = FakeFS

# dev mode: GCS_BUCKET_NAME unset -> create_workflow() builds a NEW MemorySaver per call
os.environ.pop("GCS_BUCKET_NAME", None)

async def part2():
    global STAGE
    for STAGE, approved in [("resource_extraction", False), ("reviewing_rooms", True), ("engineering_takeoff", False)]:
        calls.clear(); written.clear()
        s = await wf.resume_workflow("study_1", engineer_approved=approved)
        print(f"\n[resume_workflow stage={STAGE} engineer_approved={approved}] nodes run: {calls}")
        print("   firestore writes:", written)
        print("   returned current_stage:", s.get("current_stage"), "| engineer_approved in state:", s.get("engineer_approved"), "| appraisal_approved:", s.get("appraisal_approved"))
asyncio.run(part2())
