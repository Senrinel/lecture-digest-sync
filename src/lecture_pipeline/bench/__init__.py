"""ASR Evaluation and Benchmarking Suite."""

from lecture_pipeline.bench.cer import (
    CERResult,
    TermRecallResult,
    compute_cer,
    compute_term_recall,
    normalize_text_for_cer,
)
from lecture_pipeline.bench.report_html import (
    ChannelBenchmarkResult,
    export_benchmark_csv,
    generate_benchmark_html,
)
from lecture_pipeline.bench.runner import run_benchmark

__all__ = [
    "CERResult",
    "ChannelBenchmarkResult",
    "TermRecallResult",
    "compute_cer",
    "compute_term_recall",
    "export_benchmark_csv",
    "generate_benchmark_html",
    "normalize_text_for_cer",
    "run_benchmark",
]
