# Toolchain end-to-end benchmark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether an agent answering questions over a real long-form corpus via SMOS's
semantic-query/recall tools spends meaningfully fewer tokens than an agent answering the same
questions via raw file reads, at comparable answer quality.

**Architecture:** A 20-page Kubernetes-docs corpus lives in `sandbox/toolchain-benchmark/corpus/`
(gitignored). It is ingested once into an isolated `.smos/` store under that same directory via a
deterministic Python script (not a subagent — ingestion is setup, not what's being measured). Two
subagents then answer the same 10 pre-written, cross-document-synthesis questions: a baseline
`Agent`-tool dispatch reading the raw corpus files, and a headless `claude -p` subprocess (launched
with cwd = the sandbox directory, so it gets its own SMOS MCP server bound to the isolated store)
using only SMOS tools. Token usage is read from each run's own usage report. The orchestrator scores
both answer sets against hand-written reference answers.

**Tech Stack:** Python (corpus ingestion script, reusing `smos.ingest.transformers.markdown_transformer`
and `smos.ingest.adapters.markdown.MarkdownAdapter`), Git (sparse shallow clone for corpus sourcing),
Claude Code CLI headless mode (`claude -p --output-format json`) for the isolated SMOS-tool run, the
`Agent` tool for the baseline run.

## Global Constraints

- No SMOS production code changes — this is a measurement exercise against the system as it stands
  post-Phase-5.5a (spec §2).
- No blanket `--dangerously-skip-permissions` / `bypassPermissions` for the headless run — scope
  `--allowedTools` to exactly the tools needed (confirmed sufficient: allowlisted tools run
  without a prompt even in headless `-p` mode with no TTY; see Task 4).
- Corpus/store/payload data (bulky, regenerable) stays gitignored; the question set, ingestion
  script, and final write-up are tracked in git (spec left this open — resolved in Task 1 below).
- 10 questions, all requiring synthesis across 2+ ingested pages — no single-hop lookups (spec §4.4).

---

### Task 1: Corpus sourcing + `.gitignore` refinement

**Files:**
- Create: `sandbox/toolchain-benchmark/corpus/*.md` (20 files, gitignored)
- Create: `sandbox/toolchain-benchmark/corpus/SOURCE.md` (gitignored)
- Create: `sandbox/toolchain-benchmark/scripts/pull_corpus.sh` (tracked)
- Modify: `.gitignore`

**Interfaces:**
- Produces: `sandbox/toolchain-benchmark/corpus/` containing exactly the 20 files listed below,
  used by Task 2 (ingestion script) and Task 5 (baseline subagent).

The `.gitignore` currently ignores `sandbox/` wholesale. Refine it to track reusable methodology
(scripts, the question set, results) while still ignoring bulky/regenerable data (pulled corpus
text, the generated `.smos/` store, JSON payload dumps).

- [ ] **Step 1: Refine `.gitignore`**

Replace the existing blanket `sandbox/` line with:

```gitignore
# Sandbox — isolated scratch projects (e.g. toolchain benchmark). Corpus text,
# generated .smos/ stores, and raw payload dumps are bulky/regenerable and
# stay untracked; scripts, question sets, and write-ups are tracked.
sandbox/*/corpus/
sandbox/*/.smos/
sandbox/*/payloads/
sandbox/*/*.json
```

- [ ] **Step 2: Write the corpus-pull script**

Create `sandbox/toolchain-benchmark/scripts/pull_corpus.sh`:

```bash
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
```

- [ ] **Step 3: Run it and verify**

```bash
chmod +x sandbox/toolchain-benchmark/scripts/pull_corpus.sh
./sandbox/toolchain-benchmark/scripts/pull_corpus.sh
ls sandbox/toolchain-benchmark/corpus/*.md | wc -l
```

Expected: `21` (20 pages + `SOURCE.md`... note `SOURCE.md` doesn't match `*.md`'s exclusion, so the
count of *content* pages is 20 and `ls corpus/*.md | wc -l` reports 21 total `.md` files). Spot-check
one file's content matches what a browser shows at
`https://github.com/kubernetes/website/blob/b424574dfa5ed98d3bf4e0ff4510a7499a604c61/content/en/docs/concepts/workloads/pods/disruptions.md`.

- [ ] **Step 4: Commit**

```bash
git add .gitignore sandbox/toolchain-benchmark/scripts/pull_corpus.sh
git commit -m "feat: add corpus-pull script for toolchain benchmark, refine sandbox gitignore"
```

(The `corpus/` contents themselves are gitignored per Step 1 — nothing to add there.)

---

### Task 2: Question set + reference answers

**Files:**
- Create: `sandbox/toolchain-benchmark/questions.md` (tracked)

**Interfaces:**
- Consumes: `sandbox/toolchain-benchmark/corpus/*.md` (Task 1) — every fact cited below was
  verified against the real pulled text at commit `b424574dfa5ed98d3bf4e0ff4510a7499a604c61`
  before this plan was written.
- Produces: the 10-question set + reference answers that Tasks 5 and 6 dispatch against and score
  against, respectively.

- [ ] **Step 1: Write the question set**

Create `sandbox/toolchain-benchmark/questions.md`:

```markdown
# Toolchain benchmark — question set

10 questions, each requiring synthesis across 2+ corpus pages. Reference answers are grounded in
the actual pulled text (commit b424574dfa5ed98d3bf4e0ff4510a7499a604c61) — not shown to either
subagent, used only for scoring in Task 6.

## Q1 — Rolling updates vs. PodDisruptionBudget
**Pages:** deployment.md, disruptions.md
**Question:** A Deployment uses the default RollingUpdate strategy and has a PodDisruptionBudget
attached to its Pods. During a rolling update, do the Pods that go unavailable count against the
PDB's budget, and does the PDB block or slow the rollout itself?
**Reference answer:** Pods deleted/unavailable during a rolling upgrade do count against the
disruption budget's accounting, but workload resources like Deployments and StatefulSets are
*not* limited by PDBs when doing rolling upgrades — the rollout's own pace is governed by the
Deployment's `maxUnavailable`/`maxSurge` fields (25%/25% by default), not the PDB.

## Q2 — StatefulSet identity vs. scale-down blocking
**Pages:** statefulset.md, scale-stateful-set.md
**Question:** The scaling task page says you cannot scale down a StatefulSet while any of its
managed Pods is unhealthy. Which StatefulSet-specific properties from the concept page explain
why this restriction exists (and wouldn't apply the same way to a stateless ReplicaSet)?
**Reference answer:** StatefulSet Pods have a stable ordinal identity (`name-0`, `name-1`, ...)
and Pods are terminated/created in strict ordinal order; each Pod also gets its own PVC via
`VolumeClaimTemplate`. Because termination is ordered and identity-bound rather than
interchangeable, the controller can't safely proceed past an unhealthy Pod the way a ReplicaSet
(whose Pods are fungible) could.

## Q3 — HPA scale-up vs. scale-down asymmetry
**Pages:** horizontal-pod-autoscale.md, horizontal-pod-autoscale-walkthrough.md
**Question:** Given the walkthrough's `kubectl autoscale deployment php-apache --cpu=50% --min=1
--max=10` command, if load spikes and then immediately drops back down, would the HPA add and
remove replicas at the same speed?
**Reference answer:** No. Scaling up has no default stabilization window — the HPA acts as soon
as metrics indicate a need. Scaling down has a 5-minute (300s) default stabilization window
(`--horizontal-pod-autoscaler-downscale-stabilization`), so replica removal lags behind a load
drop by design, to prevent flapping.

## Q4 — ClusterIP vs. headless Service DNS
**Pages:** service.md, dns-pod-service.md
**Question:** How does DNS resolution differ between a normal `ClusterIP` Service and a headless
Service (`clusterIP: None`) for the same set of Pods?
**Reference answer:** A normal Service gets a single A/AAAA record resolving to its stable
cluster (virtual) IP. A headless Service has no cluster IP allocated at all — its DNS name
instead resolves directly to multiple A/AAAA records, one per backing Pod IP, with no VIP and no
kube-proxy load-balancing involved.

## Q5 — Ingress requires a Service
**Pages:** ingress.md, service.md
**Question:** Can an Ingress route traffic directly to a set of Pods, bypassing a Service object?
**Reference answer:** No — an Ingress backend is specified as a `service.name` + `service.port`;
it routes to an already-existing Service, not directly to Pods. Ingress is not itself a kind of
Service (it's a separate API object that an Ingress controller reads), and typically fronts a
`ClusterIP`-type Service internally.

## Q6 — NetworkPolicy scope vs. DNS
**Pages:** network-policies.md, dns-pod-service.md
**Question:** If a NetworkPolicy applies a default-deny egress rule to a set of Pods and does not
explicitly allow any egress, can those Pods still resolve other Services by DNS name?
**Reference answer:** No — NetworkPolicy selects Pods directly via `podSelector` (independent of
any Service definition), and a default-deny-all egress policy also blocks DNS traffic unless DNS
egress is explicitly allowed in the policy, since DNS lookups are themselves outbound network
traffic from the Pod.

## Q7 — Dynamically-provisioned PV reclaim vs. emptyDir lifecycle
**Pages:** persistent-volumes.md, volumes.md
**Question:** If a Pod uses an `emptyDir` volume and, separately, a PVC backed by a
dynamically-provisioned PersistentVolume, and the Pod is deleted and replaced — what happens to
the data in each case?
**Reference answer:** The `emptyDir`'s data is destroyed permanently — it's scoped to the Pod's
lifetime, not the container's, so it doesn't survive Pod deletion (only container restarts).
The dynamically-provisioned PV defaults to the `Delete` reclaim policy, meaning when its bound
PVC is deleted, the PV *and* the underlying external storage asset are deleted too — but only
when the PVC itself is deleted, not merely when the Pod using it is deleted and replaced (a new
Pod can rebind the same PVC).

## Q8 — revisionHistoryLimit vs. rollback
**Pages:** deployment.md, update-deployment-rolling.md
**Question:** The task page shows recovering from a bad rollout with `kubectl rollout undo
deployment/nginx-deployment --to-revision=1`. If a Deployment has `.spec.revisionHistoryLimit`
set to `0`, what happens when you try to run that same command later?
**Reference answer:** It fails to roll back to that revision. `revisionHistoryLimit: 0` means old
ReplicaSets (each of which stores one revision's Pod template) are cleaned up as soon as they're
scaled to 0 — per the concept page's Clean Up Policy section — so there's no retained ReplicaSet
left for `kubectl rollout undo` to roll back to; the default is 10 revisions retained.

## Q9 — StorageClass binding mode vs. topology
**Pages:** storage-classes.md, persistent-volumes.md
**Question:** A StatefulSet's `VolumeClaimTemplate` creates a PVC against a StorageClass that
uses the default binding mode, in a multi-zone cluster with zone-local storage. What problem can
occur, and what StorageClass setting avoids it?
**Reference answer:** The default `volumeBindingMode` is `Immediate`, which provisions/binds the
PV as soon as the PVC is created — independent of which node the Pod ends up scheduled to. In a
multi-zone cluster with topology-constrained (zone-local) storage, this can provision a volume in
a zone the Pod's node never lands in. Setting `volumeBindingMode: WaitForFirstConsumer` on the
StorageClass delays binding/provisioning until a Pod using the PVC is actually scheduled, so the
volume is created in the right zone.

## Q10 — StatefulSet ordinal termination vs. drain time under a PDB
**Pages:** disruptions.md, statefulset.md
**Question:** The PDB walkthrough example notes that for a StatefulSet, a terminating Pod's
replacement (same name, different UID) can't be created until the original Pod fully terminates
— unlike the plain Deployment example given alongside it. Which StatefulSet property explains
this, and how does it affect how long a PDB-constrained drain takes for a StatefulSet compared to
a Deployment?
**Reference answer:** StatefulSet Pods terminate and are recreated in strict ordinal order (from
the largest ordinal down), and a replacement Pod reuses the same name but isn't created until the
old one has fully terminated — Deployment/ReplicaSet Pods have no such ordering or identity
constraint and can be replaced as soon as capacity allows. Combined with a restrictive PDB, a
StatefulSet drain can take strictly longer: each Pod's replacement must wait for full termination
before the next node's PDB-gated eviction can even be attempted, whereas a Deployment's
replacement Pods can start immediately.
```

- [ ] **Step 2: Commit**

```bash
git add sandbox/toolchain-benchmark/questions.md
git commit -m "feat: add toolchain benchmark question set + reference answers"
```

---

### Task 3: Deterministic corpus ingestion into the isolated sandbox store

**Files:**
- Create: `sandbox/toolchain-benchmark/scripts/ingest_corpus.py` (tracked)
- Create (generated, gitignored): `sandbox/toolchain-benchmark/.smos/faiss.index`,
  `sandbox/toolchain-benchmark/.smos/metadata.db`

**Interfaces:**
- Consumes: `sandbox/toolchain-benchmark/corpus/*.md` (Task 1); `markdown_to_payload(text, system,
  native_id, doc_name=None) -> IngestionPayload` from
  `smos.ingest.transformers.markdown_transformer`; `MarkdownAdapter` from
  `smos.ingest.adapters.markdown`; `do_ingest_document_schema(payload, store, adapter=None) ->
  dict` from `smos.tools.ingest_tools`; `VectorStore(persist_path: Path)` from
  `smos.memory.vector_store`.
- Produces: a populated `.smos/` store at `sandbox/toolchain-benchmark/.smos/` — the same
  directory a `smos-server` subprocess opens automatically when launched with that cwd (see Task 4).

Ingestion runs as a plain Python script, not a subagent — building 20 correct `IngestionPayload`
JSON blobs is mechanical, and getting it right is setup for the experiment, not part of what's
being measured (subagent quality is only measured in Tasks 4-5's *answering* runs).

- [ ] **Step 1: Write the ingestion script**

Create `sandbox/toolchain-benchmark/scripts/ingest_corpus.py`:

```python
"""Ingest sandbox/toolchain-benchmark/corpus/*.md into an isolated .smos/ store,
via the same do_ingest_document_schema() path production ingestion uses (Phase
5.3/5.5a) — not the lossy recursive-scan path — so the benchmark measures the
real structure-preserving, reference-extracting, domain-tagged retrieval SMOS
actually ships, not a degraded fallback.

Run from anywhere; paths are resolved relative to this file. Idempotent:
do_ingest_document_schema upserts by (system, native_id), so re-running after
a corpus change resegments/replaces rather than duplicating.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SANDBOX_DIR = _SCRIPT_DIR.parent
_CORPUS_DIR = _SANDBOX_DIR / "corpus"
_STORE_DIR = _SANDBOX_DIR / ".smos"
_REPO_ROOT = _SANDBOX_DIR.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


def main() -> None:
    pages = sorted(p for p in _CORPUS_DIR.glob("*.md") if p.name != "SOURCE.md")
    if not pages:
        raise SystemExit(f"No corpus pages found in {_CORPUS_DIR} — run pull_corpus.sh first")

    store = VectorStore(persist_path=_STORE_DIR)
    adapter = MarkdownAdapter()

    for page in pages:
        text = page.read_text(encoding="utf-8")
        payload = markdown_to_payload(
            text, system="kubernetes-docs", native_id=page.stem, doc_name=page.stem
        )
        payload.domain_tags = ["kubernetes"]
        result = do_ingest_document_schema(payload, store, adapter=adapter)
        status = result.get("status", "?")
        print(f"{page.name}: {status} (doc_id={result.get('doc_id', '?')})")
        if status not in ("complete", "resumed"):
            print(f"  WARNING non-complete status, full result: {result}")

    print(f"\nIngested {len(pages)} pages into {_STORE_DIR}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

```bash
python sandbox/toolchain-benchmark/scripts/ingest_corpus.py
```

Expected: 20 lines each ending `complete (doc_id=...)`, no `WARNING` lines, final line
`Ingested 20 pages into .../sandbox/toolchain-benchmark/.smos`.

- [ ] **Step 3: Verify store isolation**

Confirm the sandbox store is separate from this repo's own dogfood stores (`data/` and this
session's `.smos/`) — different file, different size, and *not* growing this repo's own store:

```bash
ls -la sandbox/toolchain-benchmark/.smos/
ls -la .smos/ data/
```

Expected: `sandbox/toolchain-benchmark/.smos/metadata.db` exists and is a distinct file from
`.smos/metadata.db` / `data/metadata.db` at the repo root (different path, different mtime/size —
the repo-root ones should be untouched by this script, since it only ever opens
`_STORE_DIR = sandbox/toolchain-benchmark/.smos`).

- [ ] **Step 4: Spot-check retrieval works before trusting Task 5's run**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from smos.memory.vector_store import VectorStore
from smos.compression.context_builder import build_compressed_context
store = VectorStore(persist_path='sandbox/toolchain-benchmark/.smos')
result = build_compressed_context('PodDisruptionBudget rolling update', store, k=5)
print(result)
"
```

Expected: results referencing `disruptions.md` and/or `configure-pdb.md` content — confirms the
ingested segments are actually retrievable, not just stored.

- [ ] **Step 5: Commit**

```bash
git add sandbox/toolchain-benchmark/scripts/ingest_corpus.py
git commit -m "feat: add deterministic ingestion script for toolchain benchmark sandbox store"
```

(`.smos/` itself stays gitignored per Task 1's `.gitignore` update.)

---

### Task 4: Baseline run — raw `Read` subagent

**Files:**
- Create (gitignored): `sandbox/toolchain-benchmark/baseline_result.json`

**Interfaces:**
- Consumes: `sandbox/toolchain-benchmark/corpus/*.md` (Task 1), the 10 questions from
  `sandbox/toolchain-benchmark/questions.md` (Task 2, question text only — not the reference
  answers).
- Produces: `baseline_result.json` — the raw answer text and usage numbers Task 6 scores and
  compares.

- [ ] **Step 1: Dispatch the baseline subagent**

Use the `Agent` tool directly (not a Bash/CLI call — no MCP isolation needed here since `Read` has
no store to isolate):

```
Agent({
  description: "Toolchain benchmark baseline (raw Read)",
  subagent_type: "general-purpose",
  model: "haiku",
  run_in_background: false,
  prompt: `You are answering 10 questions about Kubernetes documentation using ONLY the Read and
Glob tools — do not use any MCP tools, do not use prior Kubernetes knowledge you weren't able to
verify by reading these files. The corpus is 20 markdown files in
C:\\private\\semantic_memory\\sandbox\\toolchain-benchmark\\corpus\\ — read whichever files you
need per question (use Glob to list them first).

Answer these 10 questions, one paragraph each, citing which file(s) you drew the answer from:

1. A Deployment uses the default RollingUpdate strategy and has a PodDisruptionBudget attached
   to its Pods. During a rolling update, do the Pods that go unavailable count against the PDB's
   budget, and does the PDB block or slow the rollout itself?
2. The scaling task page says you cannot scale down a StatefulSet while any of its managed Pods
   is unhealthy. Which StatefulSet-specific properties explain why this restriction exists (and
   wouldn't apply the same way to a stateless ReplicaSet)?
3. Given the walkthrough's \`kubectl autoscale deployment php-apache --cpu=50% --min=1 --max=10\`
   command, if load spikes and then immediately drops back down, would the HPA add and remove
   replicas at the same speed?
4. How does DNS resolution differ between a normal ClusterIP Service and a headless Service
   (clusterIP: None) for the same set of Pods?
5. Can an Ingress route traffic directly to a set of Pods, bypassing a Service object?
6. If a NetworkPolicy applies a default-deny egress rule to a set of Pods and does not explicitly
   allow any egress, can those Pods still resolve other Services by DNS name?
7. If a Pod uses an emptyDir volume and, separately, a PVC backed by a dynamically-provisioned
   PersistentVolume, and the Pod is deleted and replaced — what happens to the data in each case?
8. The task page shows recovering from a bad rollout with \`kubectl rollout undo
   deployment/nginx-deployment --to-revision=1\`. If a Deployment has
   .spec.revisionHistoryLimit set to 0, what happens when you try to run that same command later?
9. A StatefulSet's VolumeClaimTemplate creates a PVC against a StorageClass that uses the default
   binding mode, in a multi-zone cluster with zone-local storage. What problem can occur, and
   what StorageClass setting avoids it?
10. The PDB walkthrough example notes that for a StatefulSet, a terminating Pod's replacement
    (same name, different UID) can't be created until the original Pod fully terminates — unlike
    the plain Deployment example given alongside it. Which StatefulSet property explains this,
    and how does it affect how long a PDB-constrained drain takes for a StatefulSet compared to a
    Deployment?

Format your final response as numbered answers, one per question, each citing the source
filename(s).`
})
```

- [ ] **Step 2: Save the result**

Write the `Agent` tool's returned text and usage metadata to
`sandbox/toolchain-benchmark/baseline_result.json`:

```json
{
  "run": "baseline-raw-read",
  "model": "haiku",
  "answers_text": "<paste the Agent tool's returned message text verbatim>",
  "usage": "<paste the Agent tool's returned usage metadata verbatim>"
}
```

- [ ] **Step 3: Verify**

Read the saved file back and confirm all 10 answers are present and each cites at least one
corpus filename. If any answer cites zero files or looks like unverified prior knowledge, note it
— that's a Task 6 scoring input (§7 threat-to-validity check), not a reason to redo this task.

---

### Task 5: SMOS-tool run — isolated headless subagent

**Files:**
- Create (gitignored): `sandbox/toolchain-benchmark/smos_result.json`

**Interfaces:**
- Consumes: the populated `.smos/` store from Task 3, the 10 questions from
  `sandbox/toolchain-benchmark/questions.md` (Task 2).
- Produces: `smos_result.json` — same shape as Task 4's output, for Task 6 to compare.

- [ ] **Step 1: Write the prompt to a file**

Long prompts are awkward to pass inline through shell quoting — write it to a scratch file first.
Create `sandbox/toolchain-benchmark/smos_prompt.txt` (gitignored) with the same 10 questions as
Task 4's prompt, but the instruction paragraph replaced with:

```
You are answering 10 questions about Kubernetes documentation using ONLY the SMOS MCP tools
(tool_semantic_query, tool_recall, tool_reconstruct) — do not use Read, do not use prior
Kubernetes knowledge you weren't able to verify through a tool result. The corpus has already
been ingested into your SMOS memory store, tagged with domain "kubernetes" — query it, don't
assume you need to find or read any files.

[... same 10 numbered questions as Task 4 ...]

Format your final response as numbered answers, one per question, each mentioning which SMOS tool
call(s) surfaced the supporting content.
```

- [ ] **Step 2: Dispatch via headless `claude -p` from the sandbox cwd**

This is the isolation mechanism from spec §3-4.2 — cwd must be
`sandbox/toolchain-benchmark/` so the SMOS MCP server (user-scope, cwd-bound at startup) opens
*this* directory's `.smos/`, not the repo root's:

```bash
cd sandbox/toolchain-benchmark
claude -p "$(cat smos_prompt.txt)" \
  --model haiku \
  --output-format json \
  --allowedTools "mcp__smos__tool_semantic_query mcp__smos__tool_recall mcp__smos__tool_reconstruct" \
  > smos_result_raw.json
cd ../..
```

- [ ] **Step 3: Verify the run actually used SMOS tools, not a silent fallback**

```bash
python -c "
import json
with open('sandbox/toolchain-benchmark/smos_result_raw.json') as f:
    data = json.load(f)
print('is_error:', data['is_error'])
print('num_turns:', data['num_turns'])
print('permission_denials:', data['permission_denials'])
print()
print(data['result'][:2000])
"
```

Expected: `is_error: False`, `num_turns` > 1 (multiple tool-call round-trips, not a single-shot
answer from parametric knowledge), `permission_denials` is an empty list (confirms
`--allowedTools` didn't block a needed call), and the printed answer text references specific
retrieved content. If `num_turns` is 1, the run likely answered from pretrained knowledge without
querying — flag this in Task 6, don't silently rerun with looser scoping.

- [ ] **Step 4: Extract usage and save the final result**

```bash
python -c "
import json
with open('sandbox/toolchain-benchmark/smos_result_raw.json') as f:
    data = json.load(f)
out = {
    'run': 'smos-tools-isolated',
    'model': 'haiku',
    'answers_text': data['result'],
    'usage': data['usage'],
    'total_cost_usd': data['total_cost_usd'],
    'num_turns': data['num_turns'],
}
with open('sandbox/toolchain-benchmark/smos_result.json', 'w') as f:
    json.dump(out, f, indent=2)
print('saved smos_result.json')
"
```

---

### Task 6: Score both runs and write the comparison

**Files:**
- Create: `docs/toolchain-benchmark-results.md` (tracked)
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: `sandbox/toolchain-benchmark/baseline_result.json` (Task 4),
  `sandbox/toolchain-benchmark/smos_result.json` (Task 5), the reference answers in
  `sandbox/toolchain-benchmark/questions.md` (Task 2).
- Produces: the benchmark's actual deliverable — a written comparison, tracked in git so future
  sessions don't need to re-run the experiment to see the conclusion.

- [ ] **Step 1: Score each question**

For each of the 10 questions, the orchestrator (this session) reads the baseline answer, the SMOS
answer, and the reference answer side by side, and records a one-line verdict: `comparable` /
`baseline better` / `smos better`, with a one-sentence reason — per spec §4.5, no separate judge
subagent, no numeric rubric beyond this. Also flag, per question, whether either answer shows
signs of unverified parametric knowledge rather than corpus-grounded content (spec §7) — e.g. an
answer that's generically correct about Kubernetes but doesn't match the specific numbers/fields
in the reference answer (25%/25% defaults, 5-minute stabilization window, `Delete` reclaim
policy, etc. — details a model would only reliably get right by reading the actual retrieved
text).

- [ ] **Step 2: Write the comparison doc**

Create `docs/toolchain-benchmark-results.md` with: total token usage for each run (from the saved
`usage` blocks — input/output/cache tokens and `total_cost_usd`), the per-question verdict table
from Step 1, and a short overall conclusion (did SMOS win, lose, or wash on this corpus, and
why — tie the answer back to the §7 validity threat if either run looks like it leaned on
pretrained knowledge rather than retrieval).

- [ ] **Step 3: Update `docs/HANDOFF.md`**

Replace the "Next session: full toolchain end-to-end benchmark" section (currently describing the
benchmark as not-yet-started) with a short pointer to the completed
`docs/toolchain-benchmark-results.md`, and restore the "Next up" framing to whichever of Track A'
(5.5b) or Track B (Phase 4) makes sense to pick up next.

- [ ] **Step 4: Commit**

```bash
git add docs/toolchain-benchmark-results.md docs/HANDOFF.md
git commit -m "docs: record toolchain benchmark results (SMOS tools vs raw file reading)"
```

---

## Self-review notes

- **Spec coverage:** §4.1 corpus → Task 1. §3-4.2 isolation mechanism → Task 5 Step 2 (with the
  ingestion step moved to a deterministic script per the signature-reading finding below — a
  refinement within spec §4.3's explicitly-left-open "exact tool choice is an implementation-time
  decision"). §4.3 ingestion → Task 3. §4.4 questions → Task 2. §4.5 scoring → Task 6 Step 1. §4.6
  token measurement → Task 4 Step 2 / Task 5 Step 4 (exact JSON keys confirmed by smoke-testing
  `claude -p --output-format json` during plan-writing, not guessed). §4.7 output → Task 6 Step 2.
  §7 validity threat → Task 4 Step 3 and Task 6 Step 1.
- **Deviation from spec worth flagging explicitly:** §4.3 left "`tool_ingest_document` vs
  `tool_recursive_semantic_ingest`" as an implementation-time choice. Reading both tools' real
  signatures (`smos/server.py:402,467`) surfaced that `tool_recursive_semantic_ingest` is the
  *lossy per-file-summary* path (bypasses the Phase 5.1-5.5a segmentation/reference-extraction/
  domain-tagging machinery entirely) — using it would benchmark the wrong ingestion path for a
  test whose stated goal is measuring SMOS's actual value proposition. Task 3 uses
  `do_ingest_document_schema` (the schema-driven path) directly via a Python script instead of
  through either MCP tool, since `tool_ingest_document` requires pre-built JSON payloads anyway
  and building those is deterministic, not a subagent-quality question.
- **Placeholder scan:** no TBD/TODO; the one open item ("exact `--allowedTools` list") was
  resolved during planning by reading `claude --help` and confirming allowlisted tools skip
  permission prompts even in headless `-p` mode.
- **Type/name consistency:** `markdown_to_payload`, `MarkdownAdapter`, `do_ingest_document_schema`,
  `VectorStore(persist_path=...)` signatures in Task 3 match `smos/ingest/transformers/
  markdown_transformer.py`, `smos/ingest/adapters/markdown.py`, `smos/tools/ingest_tools.py:623`,
  and `smos/memory/vector_store.py:28` as read directly from source during planning, not assumed.
