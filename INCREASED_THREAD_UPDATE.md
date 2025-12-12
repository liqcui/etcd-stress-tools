# Increased Thread Count Update - Summary

## Overview

Updated both Python and Go implementations to **increase thread/process count per build pod** from 10-15 to **80+** while maintaining fast pod completion time (~15 seconds).

This provides a **balanced approach** that combines:
- High pod creation rate (20 completions × 10 parallelism)
- Moderate thread count per pod (80+ processes)
- Fast pod turnover (~15 seconds per pod)

## Changes Made

### Thread/Process Count Increase

**Previous (Fast Pod Mode)**:
- Processes per pod: 10-15
- Strategy: Minimal processes for maximum pod speed

**Current (Increased Thread Mode)**:
- Processes per pod: **80+**
- Strategy: Balanced - moderate threads + fast completion

### Build Script Breakdown

**Phase 1: Dependencies (60+ threads)**
```bash
for i in $(seq 1 20); do
    (sleep 15 && echo "Worker $i done") &        # 20 worker processes
    (dd if=/dev/zero of=/dev/null ...) &         # 20 I/O processes
    (echo "Thread $i" | md5sum > /dev/null) &    # 20 CPU processes
done
# Total: 60 background processes
```

**Phase 2: Compilation (70+ threads)**
```bash
for i in $(seq 1 10); do
    (sleep 12 && echo "Compiler $i done") &      # 10 compiler processes
done
# Total: 60 + 10 = 70 processes
```

**Phase 3: Testing (80+ threads)**
```bash
for i in $(seq 1 10); do
    (sleep 10 && echo "Test $i done") &          # 10 test processes
done
# Total: 60 + 10 + 10 = 80 processes
```

### Process Types Created

1. **Worker processes (20)**: Long-running background tasks (15 seconds)
2. **I/O processes (20)**: CPU-intensive disk I/O simulation (`dd`)
3. **CPU processes (20)**: Computational work (`md5sum`)
4. **Compiler processes (10)**: Medium-duration tasks (12 seconds)
5. **Test processes (10)**: Short-duration tasks (10 seconds)

**Total**: 80 concurrent background processes per pod

## Files Modified

### Python: `/Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/python/create-deployment.py`

**Lines 878-913**: Updated build script
- Function renamed: `lightweight_build()` → `increased_thread_build()`
- Thread count: 5 processes → 80 processes
- Phase structure: 3 phases with increasing thread counts

**Line 913**: Updated function call
```python
# Old: lightweight_build
# New: increased_thread_build
```

**Lines 919-927**: Updated completion message
```bash
echo "BUILD COMPLETE (INCREASED THREAD MODE)"
echo "Threads/Processes: 80+ (increased for resource pressure)"
```

### Go: `/Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go/create-deployment.go`

**Lines 660-695**: Updated build script (identical to Python)
- Function renamed: `lightweight_build()` → `increased_thread_build()`
- Thread count: 5 processes → 80 processes
- Phase structure: 3 phases with increasing thread counts

**Line 695**: Updated function call
```go
// Old: lightweight_build
// New: increased_thread_build
```

**Lines 701-709**: Updated completion message
```bash
echo "BUILD COMPLETE (INCREASED THREAD MODE)"
echo "Threads/Processes: 80+ (increased for resource pressure)"
```

## Impact Analysis

### Per Namespace (20 build jobs × 10 parallelism)

**Before (Fast Pod Mode)**:
- Total pods: 20
- Max concurrent: 10 pods
- Processes per pod: 10-15
- Total concurrent processes: 100-150 (10 pods × 10-15 processes)
- Duration: ~15 seconds per pod

**After (Increased Thread Mode)**:
- Total pods: 20
- Max concurrent: 10 pods
- Processes per pod: **80+**
- Total concurrent processes: **800+** (10 pods × 80+ processes)
- Duration: ~15 seconds per pod (unchanged)

**Net impact**: 5.3x more concurrent processes while maintaining fast pod creation

### Cluster-Level Impact

With 10 namespaces running concurrently:

**Total concurrent load**:
- Pods: 100 (10 per namespace × 10 namespaces)
- Processes: **8,000+** (100 pods × 80 processes)
- Pod creation rate: Still high (many pods completing every 15 seconds)

**Resource pressure targets**:
1. **Per-pod resources**: 80 processes per pod stresses container runtime
2. **Node resources**: 800+ processes per namespace stresses kubelet/node
3. **Cluster resources**: 100 concurrent pods stresses scheduler/CNI
4. **Combined**: Both high pod rate AND high per-pod processes

## Expected Resource Exhaustion Pattern

### Dual-Vector Attack

**Vector 1: High Pod Creation Rate** (unchanged)
- 10 pods starting simultaneously per namespace
- Scheduler, CNI, kubelet overwhelmed by pod count

**Vector 2: High Per-Pod Process Count** (new)
- 80 processes per pod stresses:
  - Container runtime (containerd/CRI-O)
  - cgroup management
  - Process/thread limits
  - Memory allocation

### Expected Errors

**More likely with increased threads**:
- `resource temporarily unavailable` - Fork/exec failures due to process limits
- `cannot allocate memory` - Memory exhaustion per pod
- `too many open files` - File descriptor exhaustion
- `cgroup: fork rejected` - cgroup process limits hit
- `failed to create container` - Container runtime overload

**Still likely from pod creation rate**:
- `failed to create pod sandbox` - CNI timeout
- `context deadline exceeded` - Scheduler timeout

## Testing

### Quick Test (2 Namespaces)

**Python**:
```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/python
python3 create-deployment.py --total-namespaces 2
```

**Go (macOS)**:
```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go
go run create-deployment.go --total-namespaces 2
```

**Expected behavior**:
- Creates 40 build jobs (20 per namespace)
- Max 20 concurrent pods (10 per namespace)
- Each pod spawns 80+ processes
- Total concurrent processes: ~1,600 (20 pods × 80)

### Monitor Thread Count

**Check actual process count in a pod**:
```bash
# Get a running build pod
BUILD_POD=$(kubectl get pods -A -l type=build-job \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}')

NAMESPACE=$(kubectl get pods -A -l type=build-job \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.namespace}')

# Count processes
kubectl exec $BUILD_POD -n $NAMESPACE -- ps aux | wc -l
```

**Expected output**: 80-90 processes

**Watch process count over time**:
```bash
watch -n 1 'kubectl exec $BUILD_POD -n $NAMESPACE -- ps aux 2>/dev/null | wc -l'
```

**Expected progression**:
- t=0s: ~10 processes (container startup)
- t=1s: ~65 processes (Phase 1 complete: 60 workers)
- t=2s: ~75 processes (Phase 2 complete: 60 workers + 10 compilers)
- t=3s: ~85 processes (Phase 3 complete: 60 workers + 10 compilers + 10 tests)
- t=15s: ~10 processes (cleanup complete)

### Monitor Resource Usage

**Check CPU/memory per pod**:
```bash
kubectl top pods -A -l type=build-job
```

**Expected**:
- CPU: 200-500m (higher due to more processes)
- Memory: 100-200Mi (higher due to process overhead)

**Watch node-level impact**:
```bash
kubectl top nodes
```

**Expected with 10 concurrent pods per node**:
- Significant CPU increase (many processes doing I/O and computation)
- Memory increase from process overhead
- Possible node pressure warnings

## Comparison: Thread Count Evolution

### Version 1: Aggressive Mode (Original)
- **Processes**: 300+ per pod
- **Duration**: 30-40 seconds
- **Completions**: 3
- **Parallelism**: 3
- **Problem**: Too slow, only 3 concurrent pods

### Version 2: Fast Pod Mode
- **Processes**: 10-15 per pod
- **Duration**: ~15 seconds
- **Completions**: 20
- **Parallelism**: 10
- **Problem**: Not enough per-pod pressure

### Version 3: Increased Thread Mode (Current)
- **Processes**: 80+ per pod
- **Duration**: ~15 seconds
- **Completions**: 20
- **Parallelism**: 10
- **Advantage**: Balanced - high pod rate + moderate per-pod load

## Resource Requirements

### Per Namespace (Default: 20 completions × 10 parallelism)

**Build pod resources**:
- Request: 64Mi RAM, 50m CPU (per pod)
- Limit: 256Mi RAM, 500m CPU (per pod)

**Concurrent pods**: 10 pods

**Total resource usage**:
- Requests: 640Mi RAM, 500m CPU
- Limits: 2.5Gi RAM, 5 CPU cores

**Processes**: ~800 (10 pods × 80 processes)

### 10 Namespaces Total

**Concurrent pods**: 100 (10 per namespace)

**Total resource usage**:
- Requests: 6.4Gi RAM, 5 CPU cores
- Limits: 25Gi RAM, 50 CPU cores

**Total processes**: ~8,000 (100 pods × 80 processes)

**Should trigger resource exhaustion on**:
- Small clusters (< 3 nodes)
- Resource-constrained nodes
- Clusters with process/thread limits
- Clusters with cgroup restrictions

## Customization

### Further Increase Thread Count

Edit the build script to create more processes:

**Python/Go build script modification**:
```bash
# Change Phase 1 from 20 to 40 iterations (120 processes instead of 60)
for i in $(seq 1 40); do
    (sleep 15 && echo "Worker $i done") &
    (dd if=/dev/zero of=/dev/null bs=1M count=3 2>/dev/null) &
    (echo "Thread $i" | md5sum > /dev/null) &
done
# Total: 120 processes in Phase 1 alone
```

### Reduce Thread Count

To reduce per-pod overhead while maintaining high pod rate:

```bash
# Change Phase 1 from 20 to 10 iterations (30 processes instead of 60)
for i in $(seq 1 10); do
    (sleep 15 && echo "Worker $i done") &
    (dd if=/dev/zero of=/dev/null bs=1M count=3 2>/dev/null) &
    (echo "Thread $i" | md5sum > /dev/null) &
done
# Total: 30 processes in Phase 1
```

### Adjust via Environment Variables

You can still control parallelism and completions:

```bash
# Create more pods with fewer processes each
BUILD_PARALLELISM=20 BUILDS_PER_NS=50 python3 create-deployment.py --total-namespaces 3

# Creates: 150 total pods, max 60 concurrent, 80+ processes per pod
# Total concurrent processes: ~4,800
```

## Summary

**Change**: Increased thread count from 10-15 to 80+ per build pod

**Strategy**: Balanced approach combining:
- ✅ High pod creation rate (20 completions × 10 parallelism)
- ✅ Moderate thread count (80+ processes per pod)
- ✅ Fast pod completion (~15 seconds)

**Impact**:
- 5.3x more processes per pod (80 vs 15)
- Same fast pod turnover (~15 seconds)
- 5.3x more total concurrent processes (800+ vs 150 per namespace)

**Expected outcome**:
- Triggers both pod-level AND cluster-level resource exhaustion
- Should hit "resource temporarily unavailable" faster
- Creates realistic multi-vector cluster stress

## Next Steps

1. **Test with 2 namespaces** to verify thread count (~1,600 concurrent processes)
2. **Monitor pod process count** using `kubectl exec ... ps aux | wc -l`
3. **Watch for resource errors** in cluster events and pod logs
4. **Adjust thread count** if needed by editing build script
5. **Increase namespaces** gradually to find cluster breaking point
