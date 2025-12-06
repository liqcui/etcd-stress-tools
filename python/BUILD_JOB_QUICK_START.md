# Build Job Simulation - Quick Start Guide

## Overview

The `create-deployment.py` script now includes realistic build job simulation based on patterns observed in must-gather analysis.

---

## Quick Examples

### 1. Basic Build Job Test

```bash
# Enable build jobs with safe defaults
BUILD_JOB_ENABLED=true ./create-deployment.py --total-namespaces 5

# What happens:
# - 5 namespaces created
# - 20 unique build jobs per namespace (100 total jobs)
# - Each job runs 10 completions with 3 concurrent pods
# - Max concurrent: 15 build pods at a time
# - Build duration: 1-2 minutes each
```

### 2. Simulate Must-Gather Pattern

```bash
# Reproduce the build pattern from must-gather analysis
BUILD_JOB_ENABLED=true \
BUILD_PARALLELISM=4 \
BUILDS_PER_NS=15 \
TOTAL_NAMESPACES=3 \
./create-deployment.py

# What happens:
# - Pattern matches: 20+ builds, 3-5 concurrent (Hour 22:00 in analysis)
# - Each build creates 60-85 processes (matches observation)
# - Staggered start times (1-second delay between batches)
```

### 3. Monitor Build Activity

```bash
# Watch build jobs
watch -n 2 'kubectl get jobs -A -l type=build-job'

# View build pod logs
kubectl logs -f <pod-name> -n deploy-test-1

# Count running builds
kubectl get pods -A -l type=build-job --field-selector=status.phase=Running | wc -l
```

---

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BUILD_JOB_ENABLED` | `false` | Enable build job creation |
| `BUILDS_PER_NS` | `10` | Completions per job |
| `BUILD_PARALLELISM` | `3` | Concurrent build pods |
| `BUILD_TIMEOUT` | `900` | Job timeout (15 minutes) |

### CLI Arguments

```bash
--build-job-enabled      # Enable build jobs
--builds-per-ns 15       # Set completions
--build-parallelism 5    # Set concurrency
--build-timeout 1200     # Set timeout (seconds)
```

---

## Build Job Features

### Realistic Build Phases

Each build pod simulates a complete CI/CD pipeline:

1. **Environment Setup** (2-5 seconds)
2. **Dependency Resolution** (5-15 seconds) - CPU intensive
3. **Source Compilation** (10-30 seconds) - MOST CPU intensive
4. **Unit Tests** (5-15 seconds) - CPU intensive
5. **Integration Tests** (3-10 seconds) - CPU intensive
6. **Artifact Packaging** (2-8 seconds)
7. **Publishing** (2-5 seconds)

**Total build time:** 29-88 seconds (randomized)

### Process Creation

Each build pod creates **60-85 background processes** during compilation, matching the pattern observed in must-gather analysis.

```bash
# During compilation phase, the build script spawns:
for i in $(seq 1 $duration); do
    (dd if=/dev/zero of=/dev/null bs=1M count=10) &  # Worker 1
    (echo "Worker $i" | md5sum > /dev/null) &        # Worker 2
    sleep 1
done
wait  # Synchronization
```

This simulates Maven/Gradle compiler workers.

### Resource Usage

**Per build pod (reduced for testing):**
- Memory request: 64Mi
- Memory limit: 256Mi
- CPU request: 50m (0.05 cores)
- CPU limit: 500m (0.5 cores)

**For 20 concurrent builds:**
- Total memory: 1.3-5GB
- Total processes: ~1,200-1,700

---

## Safety Guidelines

### Safe Configuration (No Issues)

```bash
BUILD_JOB_ENABLED=true \
BUILD_PARALLELISM=3 \
BUILDS_PER_NS=10 \
TOTAL_NAMESPACES=10 \
./create-deployment.py
```

**Max concurrent:** 30 build pods
**Max processes:** ~2,550

### Stress Test (High Load)

```bash
BUILD_JOB_ENABLED=true \
BUILD_PARALLELISM=5 \
BUILDS_PER_NS=15 \
TOTAL_NAMESPACES=5 \
./create-deployment.py
```

**Max concurrent:** 25 build pods
**Max processes:** ~2,125

### ⚠️ Warning: Resource Exhaustion Risk

```bash
# This may trigger fork/exec failures on small clusters
BUILD_JOB_ENABLED=true \
BUILD_PARALLELISM=10 \
BUILDS_PER_NS=20 \
TOTAL_NAMESPACES=10 \
./create-deployment.py
```

**Max concurrent:** 100 build pods
**Max processes:** ~8,500+

---

## Example Output

### Build Job Creation

```
[BUILD_JOB] 2025-12-06 10:15:23 - Created build job build-api-authentication-3-12-5-s-1 in deploy-test-1 (completions: 10, parallelism: 3)
[BUILD_JOB] 2025-12-06 10:15:24 - Created build job build-worker-messaging-2-34-2-s-1 in deploy-test-1 (completions: 10, parallelism: 3)
[BUILD_JOB] 2025-12-06 10:15:25 - Created build job build-scheduler-analytics-1-45-8-s-1 in deploy-test-1 (completions: 10, parallelism: 3)
...
[BUILD_JOB] 2025-12-06 10:16:45 - Created 20/20 build jobs in deploy-test-1 (pattern matches must-gather analysis: 20+ builds with 3 parallel)
```

### Build Pod Logs

```
================================================================
Build Job: build-api-authentication-3-12-5-s-1
Started at: 2025-12-06T15:16:00Z
Namespace: deploy-test-1
Pod: build-api-authentication-3-12-5-s-1-abc12
================================================================

=== Phase 1: Build Environment Setup ===
✓ Environment initialized
✓ Build tools verified
✓ Repository cloned

=== Phase 2: Dependency Resolution ===
[15:16:05] Resolving and downloading dependencies...
[15:16:17] Resolving and downloading dependencies... - DONE
✓ Dependencies resolved: 12 packages

=== Phase 3: Source Compilation ===
[15:16:18] Compiling source code with 4 workers...
[15:16:38] Compiling source code with 4 workers... - DONE
✓ Compilation successful: 20 source files

=== Phase 4: Unit Tests ===
[15:16:39] Running unit test suite...
[15:16:49] Running unit test suite... - DONE
✓ Tests passed: 10 test cases

=== Phase 5: Integration Tests ===
[15:16:50] Running integration tests...
[15:16:58] Running integration tests... - DONE
✓ Integration tests passed: 8 tests

=== Phase 6: Artifact Packaging ===
✓ Artifact packaged: build-api-authentication-3-12-5-s-1.jar
✓ Container image built

=== Phase 7: Publishing Artifacts ===
✓ Published to repository

================================================================
BUILD SUCCESSFUL
================================================================
Total build time: 75 seconds
Completed at: 2025-12-06T15:17:15Z
Artifact: build-api-authentication-3-12-5-s-1.jar
================================================================
```

---

## Monitoring Build Impact

### Watch Pod Status

```bash
# Count pods by phase
kubectl get pods -A -l type=build-job \
  -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase \
  --sort-by=.status.phase

# Watch job completion
kubectl get jobs -A -l type=build-job \
  -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,COMPLETIONS:.status.succeeded/.spec.completions
```

### Monitor CNI Operations

```bash
# Check multus logs for CNI ADD/DEL operations
kubectl logs -n openshift-multus <multus-pod> | grep -E "CNI (ADD|DEL)" | tail -20

# Watch for errors
kubectl logs -n openshift-multus <multus-pod> -f | grep -i error
```

### Check Process Count

```bash
# SSH to a node or use kubectl debug
kubectl debug node/<node-name> -it --image=registry.access.redhat.com/ubi9/ubi

# Inside debug container
chroot /host
ps aux | wc -l          # Current process count
sysctl kernel.pid_max   # PID limit
```

---

## Cleanup

### Delete Build Jobs

```bash
# Delete all build jobs in all namespaces
kubectl delete jobs -A -l type=build-job

# Delete specific namespace's build jobs
kubectl delete jobs -n deploy-test-1 -l type=build-job

# Delete completed jobs only
kubectl delete jobs -A --field-selector status.successful=1 -l type=build-job
```

### Cleanup Script

```bash
# Run with cleanup enabled
BUILD_JOB_ENABLED=true \
./create-deployment.py --total-namespaces 5 --cleanup

# This will:
# 1. Create resources
# 2. Wait for builds to complete
# 3. Delete all namespaces with label deployment-test=true
```

---

## Troubleshooting

### Builds Not Starting

**Check:**
```bash
kubectl describe job <job-name> -n <namespace>
kubectl get events -n <namespace> --sort-by='.lastTimestamp'
```

**Common causes:**
- Resource quotas
- Node resource constraints
- Image pull failures

### Builds Failing

**Check:**
```bash
kubectl logs <build-pod> -n <namespace>
kubectl describe pod <build-pod> -n <namespace>
```

**Common causes:**
- OOMKilled (insufficient memory)
- Active deadline exceeded (timeout)
- Security context restrictions

### Fork/Exec Errors

**Symptom:**
```
resource temporarily unavailable
fork/exec: Resource temporarily unavailable
```

**Diagnosis:**
```bash
# Check kernel PID limit
kubectl debug node/<node> -it --image=registry.access.redhat.com/ubi9/ubi
chroot /host
sysctl kernel.pid_max
ps aux | wc -l

# Check multus logs
kubectl logs -n openshift-multus <multus-pod> | grep -i "resource temporarily"
```

**Solution:**
- Reduce BUILD_PARALLELISM
- Reduce TOTAL_NAMESPACES
- Increase kernel.pid_max on nodes
- See BUILD_JOB_ENHANCEMENT.md for details

---

## Integration with Must-Gather Analysis

After running build simulation:

```bash
# Collect must-gather
oc adm must-gather

# Analyze with 2-hour window tool
./analyze_2hour_window.py /path/to/must-gather

# Export to JSON for comparison
./analyze_2hour_window.py /path/to/must-gather --json build-simulation-results.json
```

This allows you to compare simulated load with real-world patterns.

---

## Next Steps

- See `BUILD_JOB_ENHANCEMENT.md` for detailed architecture
- Review must-gather analysis in `.work/2hour-window-analysis.md`
- Experiment with different BUILD_PARALLELISM values
- Monitor CNI operation rates during builds
- Test with kernel tuning applied
