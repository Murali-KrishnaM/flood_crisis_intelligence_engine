from __future__ import annotations

from pathlib import Path


class PolicyService:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.knowledge = self.root / "knowledge_base"

    def status(self) -> dict:
        docs = []
        for path in sorted(self.knowledge.rglob("*.pdf")):
            category = path.parent.name.replace("_", " ").title()
            docs.append({"name": path.name, "category": category, "status": "AVAILABLE"})
        return {
            "corpus_state": "READY" if docs else "PENDING",
            "retrieval_state": "PENDING",
            "llm_state": "PENDING",
            "verification_state": "PENDING",
            "documents": docs,
            "notice": "Automated policy retrieval and generation are not yet enabled.",
        }
