#!/usr/bin/env bash
# Pulls a fixed, verified set of Kubernetes docs pages into corpus/.
# Re-running this script is idempotent (rm -rf's corpus/ first) and always
# pulls the exact commit below, not "latest", so the corpus never drifts
# out from under the question set in questions.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CORPUS_DIR="$REPO_ROOT/sandbox/toolchain-benchmark/corpus"
PINNED_COMMIT="b424574dfa5ed98d3bf4e0ff4510a7499a604c61"

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

git clone --filter=blob:none --sparse https://github.com/kubernetes/website.git "$SCRATCH/website"
cd "$SCRATCH/website"
git checkout "$PINNED_COMMIT"
git sparse-checkout set \
  content/en/docs/concepts/workloads \
  content/en/docs/concepts/services-networking \
  content/en/docs/concepts/storage \
  content/en/docs/tasks/run-application

rm -rf "$CORPUS_DIR"
mkdir -p "$CORPUS_DIR"

# 20 pages, hand-picked for real cross-document relationships (see
# questions.md for which pages each question spans). Source path -> flat
# destination basename (all 20 basenames are unique, verified at plan-time).
declare -A PAGES=(
  ["content/en/docs/concepts/workloads/controllers/deployment.md"]="deployment.md"
  ["content/en/docs/concepts/workloads/controllers/statefulset.md"]="statefulset.md"
  ["content/en/docs/concepts/workloads/controllers/replicaset.md"]="replicaset.md"
  ["content/en/docs/concepts/workloads/controllers/daemonset.md"]="daemonset.md"
  ["content/en/docs/concepts/workloads/controllers/job.md"]="job.md"
  ["content/en/docs/concepts/workloads/controllers/cron-jobs.md"]="cron-jobs.md"
  ["content/en/docs/concepts/workloads/pods/disruptions.md"]="disruptions.md"
  ["content/en/docs/concepts/workloads/pods/pod-lifecycle.md"]="pod-lifecycle.md"
  ["content/en/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale.md"]="horizontal-pod-autoscale.md"
  ["content/en/docs/concepts/services-networking/service.md"]="service.md"
  ["content/en/docs/concepts/services-networking/ingress.md"]="ingress.md"
  ["content/en/docs/concepts/services-networking/network-policies.md"]="network-policies.md"
  ["content/en/docs/concepts/services-networking/dns-pod-service.md"]="dns-pod-service.md"
  ["content/en/docs/concepts/storage/persistent-volumes.md"]="persistent-volumes.md"
  ["content/en/docs/concepts/storage/volumes.md"]="volumes.md"
  ["content/en/docs/concepts/storage/storage-classes.md"]="storage-classes.md"
  ["content/en/docs/tasks/run-application/configure-pdb.md"]="configure-pdb.md"
  ["content/en/docs/tasks/run-application/update-deployment-rolling.md"]="update-deployment-rolling.md"
  ["content/en/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough.md"]="horizontal-pod-autoscale-walkthrough.md"
  ["content/en/docs/tasks/run-application/scale-stateful-set.md"]="scale-stateful-set.md"
)

for src in "${!PAGES[@]}"; do
  dst="${PAGES[$src]}"
  cp "$SCRATCH/website/$src" "$CORPUS_DIR/$dst"
done

cat > "$CORPUS_DIR/SOURCE.md" <<EOF
# Corpus source

Pulled from https://github.com/kubernetes/website at commit \`$PINNED_COMMIT\`.
License: CC BY 4.0 (https://github.com/kubernetes/website/blob/main/LICENSE).
20 pages from content/en/docs/{concepts/workloads,concepts/services-networking,concepts/storage,tasks/run-application}.
Re-pull with: sandbox/toolchain-benchmark/scripts/pull_corpus.sh
EOF

echo "Pulled $(ls "$CORPUS_DIR"/*.md | wc -l) corpus pages + SOURCE.md into $CORPUS_DIR"
