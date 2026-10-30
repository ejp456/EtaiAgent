import os
import time
import math
import unittest
from dataclasses import dataclass, field
from typing import List, Dict, Any

from llm import get_bge_small_model, BGE_EMBEDDINGS_FILE
from memory_search import (
    MemorySearchEngine,
    SearchResult,
)


@dataclass
class EvalQuery:
    query_id: str
    category: str
    query: str
    relevance_grades: Dict[str, int]
    expected_snippet_terms: List[str] = field(default_factory=list)
    match_any_term: bool = False


BENCHMARK_QUERIES: List[EvalQuery] = [
    # --- General Memory Queries ---
    EvalQuery(
        query_id="Q1_exact_metric",
        category="Exact Metric / Technical Spec",
        query="What is the 99.999% latency percentile and ops/sec throughput?",
        relevance_grades={"project_alpha.md": 3},
        expected_snippet_terms=["99.999%", "1ms"],
    ),
    EvalQuery(
        query_id="Q2_rust_concurrency",
        category="Deep Section Lookup",
        query="How does the Rust storage engine handle thread-safe in-memory indexing and connection pooling?",
        relevance_grades={"project_alpha.md": 3},
        expected_snippet_terms=["arc<mutex", "raii"],
        match_any_term=True,
    ),
    EvalQuery(
        query_id="Q3_light_audit_bars",
        category="Sub-Bullet Fact Lookup",
        query="What brightness percentage were Bar light 1 and Bar light 2 set to during the light audit?",
        relevance_grades={"light_audit.md": 3},
        expected_snippet_terms=["20%"],
    ),
    EvalQuery(
        query_id="Q4_office_lamp_green",
        category="Action Audit Lookup",
        query="What color and average brightness was the Office Lamp adjusted to?",
        relevance_grades={"light_audit.md": 3},
        expected_snippet_terms=["36%", "green"],
    ),
    EvalQuery(
        query_id="Q5_paraphrased_pet",
        category="Vocabulary Mismatch",
        query="Who is our furry canine companion?",
        relevance_grades={"memory.txt": 3},
        expected_snippet_terms=["evie"],
    ),
    EvalQuery(
        query_id="Q6_semantic_siblings",
        category="Pure Neural Semantic (Family)",
        query="Who are my siblings?",
        relevance_grades={"family_members.txt": 3},
        expected_snippet_terms=["tali", "avi"],
    ),
    EvalQuery(
        query_id="Q7_bedtime_calming",
        category="Conceptual / Temporal Rule",
        query="What color should the Go light be set to late at night (11pm) for calming bedtime sleep?",
        relevance_grades={"combined_preferences.txt": 3, "10pm_go_light_reminder.txt": 1},
        expected_snippet_terms=["11pm", "blue"],
    ),
    EvalQuery(
        query_id="Q8_morning_routine",
        category="Inline Rule Disambiguation",
        query="What happens to the Go light at 6am in the morning?",
        relevance_grades={"combined_preferences.txt": 3},
        expected_snippet_terms=["6am", "pink"],
    ),
    EvalQuery(
        query_id="Q9_code_red_consolidation",
        category="Duplicate Consolidation & Anti-Redundancy",
        query="What should happen to the Go lamp when I say code red or at 10pm?",
        relevance_grades={
            "combined_preferences.txt": 3,
            "user_preferences.txt": 1,
            "10pm_go_light_reminder.txt": 1,
        },
        expected_snippet_terms=["red"],
    ),
    EvalQuery(
        query_id="Q10_semantic_borough",
        category="Pure Neural Semantic (Geography)",
        query="Which New York City borough do I live in?",
        relevance_grades={"user_location.txt": 3, "etai_resume.md": 3},
        expected_snippet_terms=["brooklyn"],
    ),
    # --- Resume Queries ---
    EvalQuery(
        query_id="R1_google_ml_signals",
        category="Resume: Google ML Scale",
        query="How many machine learning inference signals were stored in the internal ML storage solution at Google?",
        relevance_grades={"etai_resume.md": 3},
        expected_snippet_terms=["200 billion"],
    ),
    EvalQuery(
        query_id="R2_ugc_deletion_propagation",
        category="Resume: UGC Architecture",
        query="How did we ensure eventual consistency of deletion signals from ancestors to descendants in the User Generated Content Datastore?",
        relevance_grades={"etai_resume.md": 3},
        expected_snippet_terms=["deletion signals", "100 billion", "ancestors"],
        match_any_term=True,
    ),
    EvalQuery(
        query_id="R3_onelogin_ldap_macos",
        category="Resume: OneLogin Security/LDAP",
        query="What Ruby on Rails directory search API and macOS authentication feature were built at OneLogin?",
        relevance_grades={"etai_resume.md": 3},
        expected_snippet_terms=["ldap", "certificates"],
        match_any_term=True,
    ),
    EvalQuery(
        query_id="R4_intraedge_record_processing",
        category="Resume: Data Warehouse",
        query="Which Chandler Arizona job parsed XML files with 100,000+ records into a data warehouse?",
        relevance_grades={"etai_resume.md": 3},
        expected_snippet_terms=["xml", "data warehouse"],
    ),
    EvalQuery(
        query_id="R5_usmc_radar_repair",
        category="Resume: Military Service",
        query="What systems and schematics did Etai troubleshoot in the United States Marine Corps at Twentynine Palms?",
        relevance_grades={"etai_resume.md": 3},
        expected_snippet_terms=["radar"],
    ),
    EvalQuery(
        query_id="R6_education_asu",
        category="Resume: Education & Degree",
        query="Where did Etai graduate with a Bachelor of Science in Computer Science?",
        relevance_grades={"etai_resume.md": 3},
        expected_snippet_terms=["arizona state university"],
    ),
    EvalQuery(
        query_id="R7_contact_street_address",
        category="Resume: Contact Information",
        query="What is Etai's street address, apartment number, and phone number in Brooklyn?",
        relevance_grades={"etai_resume.md": 3, "user_location.txt": 1},
        expected_snippet_terms=["184 kent ave", "480.298.9994"],
        match_any_term=True,
    ),
    EvalQuery(
        query_id="R8_military_medal_award",
        category="Resume: Awards & Commendations",
        query="What military medal or award did Etai receive?",
        relevance_grades={"etai_resume.md": 3},
        expected_snippet_terms=["navy-marine corps achievement medal"],
    ),
]


def compute_dcg(grades: List[int]) -> float:
    dcg = 0.0
    for i, g in enumerate(grades):
        if g > 0:
            dcg += (math.pow(2.0, g) - 1.0) / math.log2(i + 2.0)
    return dcg


def evaluate_engine(engine: MemorySearchEngine, queries: List[EvalQuery] = BENCHMARK_QUERIES, top_k: int = 3) -> Dict[str, Any]:
    hits_at_1 = 0.0
    recalls_at_3 = 0.0
    mrr_sum = 0.0
    ndcg_sum = 0.0
    snippet_hits = 0.0
    total_latency_ms = 0.0
    details = []

    for eq in queries:
        t0 = time.perf_counter()
        results = engine.search(eq.query, top_k=top_k)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        total_latency_ms += elapsed_ms

        returned_files = [os.path.basename(r.doc_id) for r in results]
        grades = [eq.relevance_grades.get(f, 0) for f in returned_files]

        max_grade = max(eq.relevance_grades.values())
        primary_targets = {f for f, g in eq.relevance_grades.items() if g == max_grade}

        hit1 = 1.0 if (returned_files and returned_files[0] in primary_targets) else 0.0
        hits_at_1 += hit1

        rec3 = 1.0 if any(f in primary_targets for f in returned_files) else 0.0
        recalls_at_3 += rec3

        rr = 0.0
        for rank_idx, f in enumerate(returned_files):
            if f in primary_targets:
                rr = 1.0 / (rank_idx + 1.0)
                break
        mrr_sum += rr

        dcg = compute_dcg(grades)
        ideal_grades = sorted(eq.relevance_grades.values(), reverse=True)[:top_k]
        idcg = compute_dcg(ideal_grades)
        ndcg = (dcg / idcg) if idcg > 0 else 0.0
        ndcg_sum += ndcg

        top_snippet_lower = results[0].snippet.lower() if results else ""
        if eq.expected_snippet_terms and hit1 > 0:
            matched_terms = sum(1 for term in eq.expected_snippet_terms if term.lower() in top_snippet_lower)
            if eq.match_any_term:
                s_hit = 1.0 if matched_terms >= 1 else 0.0
            else:
                s_hit = matched_terms / len(eq.expected_snippet_terms)
        else:
            s_hit = 0.0
        snippet_hits += s_hit

        details.append({
            "query_id": eq.query_id,
            "query": eq.query,
            "top_doc": returned_files[0] if returned_files else "none",
            "snippet": results[0].snippet if results else "",
            "hit_at_1": hit1,
            "snippet_hit": round(s_hit, 2),
            "latency_ms": round(elapsed_ms, 2),
        })

    n = float(len(queries))
    return {
        "hit_at_1": round(hits_at_1 / n, 3),
        "recall_at_3": round(recalls_at_3 / n, 3),
        "mrr": round(mrr_sum / n, 3),
        "ndcg_at_3": round(ndcg_sum / n, 3),
        "snippet_hit_rate": round(snippet_hits / n, 3),
        "avg_latency_ms": round(total_latency_ms / n, 2),
        "details": details,
    }


class TestMemorySearchEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        get_bge_small_model()
        cls.engine = MemorySearchEngine(memories_dir="memories")
        cls.startup_stats = cls.engine.warmup_on_startup()
        cls.results = evaluate_engine(cls.engine)

    def test_startup_warmup_is_fast_and_loads_from_cache(self):
        self.assertLess(self.startup_stats["total_startup_ms"], 25.0)
        self.assertEqual(self.startup_stats["cache_misses"], 0)
        self.assertGreater(self.startup_stats["chunks_indexed"], 30)

    def test_accuracy_metrics(self):
        self.assertEqual(self.results["hit_at_1"], 1.0)
        self.assertEqual(self.results["recall_at_3"], 1.0)
        self.assertEqual(self.results["mrr"], 1.0)
        self.assertGreaterEqual(self.results["ndcg_at_3"], 0.99)
        self.assertGreaterEqual(self.results["snippet_hit_rate"], 0.88)

    def test_resume_queries_pinpoint_exact_bullets(self):
        r1 = self.engine.search("How many machine learning inference signals were stored at Google?", top_k=1)
        self.assertEqual(os.path.basename(r1[0].doc_id), "etai_resume.md")
        self.assertIn("200 billion", r1[0].snippet)

        r6 = self.engine.search("Where did Etai graduate with a Bachelor of Science in Computer Science?", top_k=1)
        self.assertEqual(os.path.basename(r6[0].doc_id), "etai_resume.md")
        self.assertIn("arizona state university", r6[0].snippet.lower())


if __name__ == "__main__":
    get_bge_small_model()
    engine = MemorySearchEngine(memories_dir="memories")
    startup_stats = engine.warmup_on_startup()
    res = evaluate_engine(engine)

    print("\n" + "=" * 108)
    print("UNIFIED MEMORY SEARCH ENGINE EVALUATION (BAAI/bge-small-en-v1.5 across 18 Queries)")
    print("=" * 108)
    print(f"Startup Time: {startup_stats['total_startup_ms']} ms | Chunks Indexed: {startup_stats['chunks_indexed']} | Vectors Cached: {startup_stats['vectors_cached']}")
    print("-" * 108)
    print(f"Hit@1:            {res['hit_at_1']:.1%}")
    print(f"Recall@3:         {res['recall_at_3']:.1%}")
    print(f"MRR:              {res['mrr']:.3f}")
    print(f"nDCG@3:           {res['ndcg_at_3']:.3f}")
    print(f"Snippet Hit Rate: {res['snippet_hit_rate']:.1%}")
    print(f"Avg Query Latency:{res['avg_latency_ms']:.2f} ms")
    print("=" * 108 + "\n")

    unittest.main(verbosity=2)
