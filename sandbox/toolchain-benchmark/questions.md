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
