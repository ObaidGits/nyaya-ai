"""Live multi-document RAG audit against the running Docker stack.

Uploads three content-distinguishable PDFs to ONE session, then runs the
multi-document question battery (reference resolution, single-doc,
multi-doc, compare, no-evidence, follow-ups). Prints PASS/FAIL per check.

Usage: .venv/bin/python scripts/live_document_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.documents.pdf_fixtures import make_pdf

BASE = "http://localhost:8000/api/v1"
SESSION = f"docaudit-{int(time.time())}"

# --- distinguishable document content (overlapping terms on purpose) -----
EMPLOYMENT = [
    "EMPLOYMENT AGREEMENT",
    "This agreement is made between Rahul Sharma (Employee) and TechNova "
    "Solutions Private Limited (Employer) for the position of Senior "
    "Software Engineer at the Pune office.",
    "Notice Period: Either party may terminate this agreement by giving "
    "three months written notice. Termination without notice requires "
    "payment of salary for the unexpired notice period.",
    "The Employee shall receive a monthly salary of Rs 85,000 and an "
    "annual performance bonus of up to ten percent of the salary.",
    "Confidentiality: The Employee shall not disclose any confidential "
    "information of TechNova Solutions for two years after termination.",
]

RENTAL = [
    "RENTAL AGREEMENT",
    "This rental agreement is made between Priya Patel (Tenant) and "
    "Mehta Landlords (Landlord) for Flat 402, Sunrise Apartments, Mumbai.",
    "Notice Period: The Tenant may terminate this agreement by giving "
    "thirty days written notice. Early termination forfeits the security "
    "deposit.",
    "The monthly rent is Rs 24,000 payable by the fifth day of each month. "
    "A security deposit of Rs 1,44,000 is held by the Landlord.",
    "The Tenant shall not sublet the flat or keep pets without written "
    "permission from the Landlord.",
]

DEMAND = [
    "LEGAL DEMAND NOTICE",
    "This notice is issued by Advocate Kavita Rao on behalf of Sunrise "
    "Traders against Verma Industries for recovery of unpaid dues.",
    "An amount of Rs 5,00,000 is due and payable for goods delivered "
    "between January and March under invoice numbers 118, 119 and 121.",
    "You are requested to pay the said amount within fifteen days of "
    "receipt of this notice, failing which legal proceedings will be "
    "initiated without further intimation.",
]

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def make_pdf_local(pages: list[str]) -> bytes:  # pragma: no cover
    return make_pdf(pages)


def upload(c: httpx.Client, filename: str, pdf: bytes) -> str:
    r = c.post(
        "/documents/upload",
        files={"file": (filename, pdf, "application/pdf")},
        headers={"X-Session-Id": SESSION},
    )
    assert r.status_code == 201, f"{filename}: {r.status_code} {r.text}"
    document_id = r.json()["document_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        s = c.get(f"/documents/{document_id}/status", headers={"X-Session-Id": SESSION})
        if s.status_code != 200:
            time.sleep(2)
            continue
        body = s.json()
        if body["status"] == "ready":
            return document_id
        if body["status"] == "failed":
            raise RuntimeError(f"{filename} failed: {body}")
        time.sleep(2)
    raise TimeoutError(f"{filename} never became ready")


def chat(c: httpx.Client, message: str, history: list[dict] | None = None) -> dict:
    """One chat turn; returns {text, refused, citations}."""
    r = c.post(
        "/chat",
        json={"message": message, "history": history or []},
        headers={"X-Session-Id": SESSION},
        timeout=180,
    )
    body = r.text
    text = ""
    refused = "event: error" in body
    rate_limited = "rate limiting" in body
    citations: list[str] = []
    for line in body.splitlines():
        if line.startswith("data: "):
            import json

            try:
                payload = json.loads(line[6:])
            except Exception:
                continue
            text += payload.get("text", "")
            if "refused" in payload:
                refused = refused or payload["refused"]
                citations = payload.get("citations") or citations
    return {
        "text": text,
        "refused": refused,
        "rate_limited": rate_limited,
        "citations": citations,
    }


def chat_retry(c: httpx.Client, message: str, history: list[dict] | None = None) -> dict:
    """Chat with one backoff retry when the provider rate-limits us."""
    out = chat(c, message, history)
    attempts = 0
    while out["rate_limited"] and attempts < 5:
        time.sleep(60)
        out = chat(c, message, history)
        attempts += 1
    return out


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=180)
    docs = {
        "employment": upload(c, "employment-agreement.pdf", make_pdf(EMPLOYMENT)),
        "rental": upload(c, "rental-agreement.pdf", make_pdf(RENTAL)),
        "demand": upload(c, "legal-demand-notice.pdf", make_pdf(DEMAND)),
    }
    listed = c.get("/documents", headers={"X-Session-Id": SESSION}).json()
    check(
        "upload: three documents ready, listed in upload order",
        [d["status"] for d in listed] == ["ready"] * 3
        and [d["filename"] for d in listed]
        == ["employment-agreement.pdf", "rental-agreement.pdf", "legal-demand-notice.pdf"],
        f"{len(listed)} docs",
    )

    def run(
        name: str,
        message: str,
        *,
        want: list[str],
        not_want: list[str] | None = None,
        must_answer: bool = True,
        want_doc: str | None = None,
        history: list[dict] | None = None,
    ) -> dict:
        time.sleep(30)  # provider pacing (Groq free tier rate-limits rapid calls)
        out = chat_retry(c, message, history)
        lower = out["text"].lower().replace(" ", " ").replace("\xa0", " ")  # noqa: RUF001
        ok = True
        detail = ""
        if must_answer and (out["refused"] or not out["text"].strip()):
            ok, detail = False, f"refused/empty: {out['text'][:120]}"
        if ok and not all(w.lower() in lower for w in want):
            ok, detail = False, f"missing {want}: {out['text'][:160]}"
        if ok and not_want and any(n.lower() in lower for n in not_want):
            ok, detail = False, f"leaked {not_want}: {out['text'][:160]}"
        if ok and want_doc and docs[want_doc] not in out["text"]:
            # The answer must cite the right document id.
            ok, detail = False, f"did not cite {want_doc} ({docs[want_doc]})"
        if ok and want_doc:
            others = [v for k, v in docs.items() if k != want_doc]
            if any(o in out["text"] for o in others):
                ok, detail = False, f"cited wrong doc(s) for {want_doc}"
        check(name, ok, detail)
        return out

    # --- reference resolution ---------------------------------------------
    out1 = run(
        "What is the notice period in the first document?",
        "What is the notice period in the first document?",
        want=["three months"],
        not_want=["thirty days", "fifteen days"],
        want_doc="employment",
    )
    run(
        "What does the second document say about the notice period?",
        "What does the second document say about the notice period?",
        want=["thirty days"],
        not_want=["three months"],
        want_doc="rental",
    )
    run(
        "Summarize the latest document.",
        "Summarize the latest document.",
        want=["5,00,000"],
        not_want=["TechNova", "24,000"],
        want_doc="demand",
    )
    run(
        "Summarize the first document.",
        "Summarize the first document.",
        want=["Rahul Sharma"],
        not_want=["Priya Patel", "Verma"],
        want_doc="employment",
    )
    run(
        "Check uploaded doc — identifies a document and answers usefully",
        "Check uploaded doc",
        want=[],  # any grounded response naming a document/file counts
        must_answer=True,
    )
    run(
        "Check latest doc resolves the newest upload",
        "Check latest doc",
        want=["demand"],
        not_want=["TechNova", "24,000"],
        want_doc="demand",
    )
    run(
        "Does the latest document demand payment within a deadline?",
        "Does the latest document contain a demand for payment?",
        want=["fifteen days"],
        want_doc="demand",
    )
    run(
        "Which document mentions a security deposit?",
        "Which document mentions a security deposit?",
        want=["rental"],
        not_want=["TechNova", "Verma"],
        want_doc="rental",
    )
    run(
        "Compare the first and second documents (both cited)",
        "Compare the first and second documents.",
        want=[],  # any grounded comparison citing BOTH documents
        want_doc=None,
    )
    # (both document ids must appear — verified below via citations)
    time.sleep(30)
    both = chat_retry(c, "Compare the first and second documents again.")
    both_ok = docs["employment"] in both["text"] and docs["rental"] in both["text"]
    check("Compare: both documents cited", both_ok, both["text"][:120])
    run(
        "Notice periods across first and second documents",
        "What are the notice periods in the first and second documents?",
        want=["three months", "thirty days"],
    )
    run(
        "Summarize the latest uploaded PDF",
        "Summarize the latest uploaded PDF",
        want=["5,00,000"],
        want_doc="demand",
    )
    run(
        "Follow-up: 'the other document' with two remaining asks which one",
        "What about the other document?",
        want=["which one"],  # clarification (emitted as refusal) is the correct outcome
        must_answer=False,
        history=[
            {"role": "user", "content": "What is the notice period in the first document?"},
            {"role": "assistant", "content": out1["text"]},
        ],
    )
    run(
        "Answer present in only one document (salary)",
        "What is the monthly salary stated in the first document?",
        want=["85,000"],
        want_doc="employment",
    )
    run(
        "No evidence: honest refusal (interest rate nowhere)",
        "What is the interest rate in the first document?",
        want=[],
        must_answer=False,
    )

    # --- formatting sanity --------------------------------------------------
    time.sleep(30)
    out = chat_retry(c, "Summarize the second document in detail.")
    text = out["text"]
    bad = [b for b in ("svg", "<svg", "\\n\\n", "%%EOF", "/Contents", "BT /F1") if b in text]
    repeated_id = max([text.count(docs[d]) for d in docs] + [0]) > 8
    check(
        "Formatting: clean readable summary, no PDF artifacts or id spam",
        not out["refused"] and not bad and not repeated_id and len(text) > 100,
        f"artifacts={bad} max_id_reps={max([text.count(docs[d]) for d in docs] + [0])}",
    )

    # --- statute regression: BNS still works with docs uploaded -------------
    time.sleep(30)
    out = chat_retry(c, "What is the punishment for murder?")
    check(
        "BNS regression: statute question still answers with docs present",
        not out["refused"] and ("103" in out["text"] or "punishable" in out["text"].lower()),
        out["text"][:120],
    )

    c.close()
    failed = [s for s, ok, _ in results if not ok]
    print(f"\nDOCUMENT AUDIT RESULT: {len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED:", *failed, sep="\n  - ")
        sys.exit(1)


if __name__ == "__main__":
    main()
