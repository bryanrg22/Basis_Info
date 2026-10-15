"""Repro 3: the SAME lifecycle but through the project's FirestoreCheckpointer backed by an
in-memory fake Firestore (production-mode checkpointing). Also checks state round-trip fidelity."""
import asyncio, os
import agentic.graph.workflow as wf
import agentic.firestore.checkpointer as ck

# ---- fake firestore -----------------------------------------------------------
class Doc:
    def __init__(self, store, path): self.store, self.path = store, path
    def get(self):
        d = self.store.get(self.path)
        class Snap:
            exists = d is not None
            id = self.path.split("/")[-1]
            def to_dict(s): return dict(d) if d is not None else None
        return Snap()
    def set(self, data): self.store[self.path] = data
    def delete(self): self.store.pop(self.path, None)
    def collection(self, name): return Coll(self.store, f"{self.path}/{name}")
class Coll:
    def __init__(self, store, path): self.store, self.path = store, path
    def document(self, id): return Doc(self.store, f"{self.path}/{id}")
    def order_by(self, *a, **k): return self
    def limit(self, n): return self
    def stream(self): return iter([])
class FakeDB:
    def __init__(self): self.store = {}
    def collection(self, name): return Coll(self.store, name)
db = FakeDB()
ck.get_firestore_client = lambda: db

calls = []
def mk(name, patch):
    async def node(state):
        calls.append(name); return {**state, **patch(state)}
    return node
wf.load_study_node = mk("load_study", lambda s: {"user_id": "u1"})
wf.resource_extraction_node = mk("resource_extraction", lambda s: {"current_stage": "resource_extraction", "needs_review": True,
    "appraisal_resources": {"ingested": True, "nested": {"a": [1, 2]}}, "rooms": [{"id": "r1", "room_type": "kitchen"}]})
wf.analyze_rooms_node = mk("analyze_rooms", lambda s: {})
wf.reviewing_rooms_node = mk("reviewing_rooms", lambda s: {})
wf.process_assets_node = mk("process_assets", lambda s: {})
wf.complete_workflow_node = mk("complete", lambda s: {})
wf.error_handler_node = mk("error_handler", lambda s: {})

os.environ["GCS_BUCKET_NAME"] = "fake-bucket"   # production branch: FirestoreCheckpointer()
cfg = {"configurable": {"thread_id": "study_9"}}

async def main():
    app = wf.create_workflow()           # new FirestoreCheckpointer instance, like run_workflow()
    print("checkpointer type:", type(app.checkpointer).__name__)
    calls.clear()
    s = await app.ainvoke(wf.create_initial_state("study_9"), cfg)
    print("[start] nodes:", calls, "| stage:", s["current_stage"])
    doc = db.store.get("workflow_checkpoints/study_9")
    print("[start] stored keys:", sorted(doc.keys()) if doc else None)
    print("[start] stored channel_versions keys:", sorted(doc["channel_versions"].keys()))
    print("[start] stored versions_seen keys:", sorted(doc["versions_seen"].keys()))
    print("[start] writes subcollection docs:", [k for k in db.store if "/writes/" in k][:5])

    app2 = wf.create_workflow()          # new instance, like resume_workflow()
    snap = app2.get_state(cfg)
    print("[reload] state.values stage:", snap.values.get("current_stage"), "| rooms:", snap.values.get("rooms"), "| next:", snap.next)
    calls.clear()
    update = {"study_id": "study_9", "engineer_approved": True, "engineer_corrections": [], "needs_review": False, "pending_jobs": [], "current_stage": "processing_assets"}
    s = await app2.ainvoke(update, cfg)
    print("[resume] nodes:", calls, "| stage:", s["current_stage"], "| rooms preserved:", s.get("rooms"))
    print("[resume] appraisal_resources round-trip:", s.get("appraisal_resources"))

    # round-trip of a non-JSON value (datetime) through _serialize_values
    from datetime import datetime
    c = ck.FirestoreCheckpointer()
    ser = c._serialize_values({"when": datetime(2026, 1, 1), "ok": [1]})
    print("[serde] datetime ->", ser["when"], "-> back:", repr(c._deserialize_values(ser)["when"]))
asyncio.run(main())
