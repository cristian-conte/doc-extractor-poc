#!/usr/bin/env bash
# Build the corpus, run it through the pipeline, score it, render the queue.
#
#   ./run.sh corpus     regenerate the documents and their ground truth
#   ./run.sh extract    run the pipeline (calls the model; costs money)
#   ./run.sh eval       re-score and re-render from cached model responses
#   ./run.sh all        everything
set -euo pipefail
cd "$(dirname "$0")"

RUN="${RUN:-final}"

corpus()  { python3 corpus/generate.py; }
extract() { python3 run_pipeline.py --run "$RUN" --workers 5; }
evaluate() {
  python3 run_pipeline.py --run "$RUN" --from-cache >/dev/null
  python3 evaluate.py --run "$RUN"
  python3 report.py --run "$RUN"
}

case "${1:-all}" in
  corpus)  corpus ;;
  extract) extract ;;
  eval)    evaluate ;;
  all)     corpus && extract && evaluate ;;
  *) echo "usage: $0 {corpus|extract|eval|all}" >&2; exit 2 ;;
esac
