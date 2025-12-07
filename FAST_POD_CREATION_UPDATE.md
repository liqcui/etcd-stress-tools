# Fast Pod Creation Update - Summary

## Overview

Updated both Python and Go implementations to optimize for **rapid pod creation** rather than per-pod process count. This triggers "resource temporarily unavailable" errors faster by overwhelming cluster scheduling, CNI, and kubelet resources.

## Strategy Change

### Previous Approach (Aggressive Mode)
- **Strategy**: Few pods with many processes per pod
- **Per-pod processes**: 300+ processes
- **Pod duration**: 30-40 seconds
- **Default completions**: 3 (old: 10)
- **Default parallelism**: 3
- **Bottleneck**: Limited by per-pod resource limits

### New Approach (Fast Pod Mode)  
- **Strategy**: Many pods with few processes per pod
- **Per-pod processes**: 10-15 processes (95% reduction)
- **Pod duration**: ~15 seconds (50% faster)
- **Default completions**: 20 (6.7x increase)
- **Default parallelism**: 10 (3.3x increase)
- **Bottleneck**: Cluster-wide resources (scheduling, CNI, kubelet)

## Configuration Changes

### Default Values Updated

| Setting | Old Value | New Value | Change |
|---------|-----------|-----------|--------|
| `BUILDS_PER_NS` | 3 | **20** | +566% |
| `BUILD_PARALLELISM` | 3 | **10** | +233% |
| `BUILD_TIMEOUT` | 900s | **300s** | -67% |
| Pod duration | 30-40s | **~15s** | -50% |
| Processes/pod | 300+ | **10-15** | -95% |

### Impact Per Namespace

**Old configuration** (3 completions × 3 parallel):
- Creates 3 build pods total per job
- Each pod: 300+ processes, 30-40 seconds
- Max concurrent: 3 pods
- Total time: ~40 seconds for all 3

**New configuration** (20 completions × 10 parallel):
- Creates 20 build pods total per job  
- Each pod: 10-15 processes, ~15 seconds
- Max concurrent: 10 pods
- Total time: ~30 seconds for all 20

**Result**: Creates 6.7x more pods, 3.3x faster concurrency, completes in 25% less time

## Build Script Changes

### Python: `/Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/python/create-deployment.py`

**Lines 863-916**: Replaced aggressive build script with lightweight version

**Key changes**:
```bash
# Old: Created 300+ processes over 3 phases
aggressive_process_spawn 30  # 150 processes
aggressive_process_spawn 20  # 100 processes
aggressive_process_spawn 15  # 75 processes
sleep 30  # Maintain pressure

# New: Creates 10-15 processes, completes quickly
for i in $(seq 1 5); do
    (sleep 15 && echo "Worker $i done") &
done
# 3 quick phases, total ~15 seconds
```

**Lines 71-77**: Updated default configuration
```python
self.builds_per_ns = int(os.getenv('BUILDS_PER_NS', '20'))  # Was: 3
self.build_parallelism = int(os.getenv('BUILD_PARALLELISM', '10'))  # Was: 3
self.build_timeout = int(os.getenv('BUILD_TIMEOUT', '300'))  # Was: 900
```

**Lines 1194-1195**: Removed staggered creation delay
```python
# Old: await asyncio.sleep(1)  # 1 second delay between batches
# New: NO DELAY - Create all build jobs as fast as possible
```

### Go: `/Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go/create-deployment.go`

**Lines 646-698**: Replaced aggressive build script with lightweight version (identical to Python)

**Lines 94-96**: Updated default configuration
```go
BuildsPerNS:      getEnvInt("BUILDS_PER_NS", 20),      // Was: 3
BuildParallelism: getEnvInt("BUILD_PARALLELISM", 10),  // Was: 3
BuildTimeout:     getEnvInt("BUILD_TIMEOUT", 300),     // Was: 900
```

**Lines 917-918**: Removed staggered creation delay
```go
// Old: time.Sleep(1 * time.Second)
// New: NO DELAY - Create all build jobs as fast as possible
```

## Expected Resource Exhaustion Pattern

### Cluster-Level Bottlenecks (Now Primary)

With 10 parallel pods starting every ~15 seconds:

1. **Pod Scheduling Pressure**
   - 10 pods starting simultaneously per namespace
   - kube-scheduler must process 10+ pod placement decisions
   - Node resource calculation overhead multiplied

2. **CNI Plugin Exhaustion**
   - 10 pods requesting network setup simultaneously
   - CNI plugin process/thread limits hit faster
   - IP allocation and network namespace creation backlog

3. **Kubelet Overload**
   - Kubelet managing 10+ pod startups concurrently
   - Container runtime calls multiplied
   - Pod lifecycle events flooding

4. **etcd Write Pressure**
   - 10x more pod status updates
   - Faster event creation rate
   - Watch stream updates intensified

### Expected Errors

**Trigger faster with new approach**:
- `resource temporarily unavailable` - Fork/exec failures in CNI, kubelet
- `failed to create pod sandbox` - CNI timeout or failure
- `context deadline exceeded` - Scheduler or kubelet timeout
- `too many open files` - File descriptor exhaustion in cluster components

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

**Expected behavior per namespace**:
- Creates 20 build jobs immediately (no stagger delay)
- Each job runs up to 10 pods concurrently
- Each pod completes in ~15 seconds
- Total: 20 pods created in ~30 seconds per namespace

### Monitoring

**Watch pod creation rate**:
```bash
# Count running build pods every 2 seconds
watch -n 2 'kubectl get pods -A -l type=build-job --field-selector=status.phase=Running --no-headers | wc -l'
```

**Expected progression** (2 namespaces):
- t=0s: 0 pods
- t=5s: 20 pods (10 per namespace, hitting parallelism limit)
- t=15s: 20 pods (first batch completing, next batch starting)
- t=30s: 10-20 pods (final batches)
- t=45s: 0 pods (all complete)

**Check for resource exhaustion**:
```bash
# Look for CNI/scheduling errors
kubectl get events -A --sort-by='.lastTimestamp' | grep -E 'Failed|Error|resource temporarily'

# Watch kubelet logs for fork/exec failures
# (on cluster node)
journalctl -u kubelet -f | grep -E 'fork/exec|resource temporarily|failed to create'
```

### Stress Test (10 Namespaces)

```bash
# Python
python3 create-deployment.py --total-namespaces 10

# Go
go run create-deployment.go --total-namespaces 10
```

**Expected**:
- Creates 200 build jobs total (20 per namespace)
- Max concurrent: 100 pods (10 per namespace × 10 namespaces)
- Should trigger resource exhaustion within 30-60 seconds
- Much faster than previous 300+ process-per-pod approach

## Comparison: Old vs New

### Example: 5 Namespaces

**Old configuration** (3 completions, 3 parallel, 30-40s duration):
- Total pods created: 15 (3 × 5 namespaces)
- Max concurrent: 15 pods
- Processes per pod: 300+
- Total processes: ~4,500
- Time to completion: ~40 seconds
- **Bottleneck**: Per-pod resource limits prevented more pods

**New configuration** (20 completions, 10 parallel, ~15s duration):
- Total pods created: 100 (20 × 5 namespaces)
- Max concurrent: 50 pods
- Processes per pod: 10-15
- Total processes: ~1,000-1,500
- Time to completion: ~30 seconds
- **Bottleneck**: Cluster scheduling, CNI, kubelet capacity

### Why This Is More Effective

1. **Overwhelms cluster control plane faster**
   - 50 concurrent pods vs 15 concurrent pods
   - Scheduler processes 3.3x more placement decisions
   - kube-apiserver handles 3.3x more pod status updates

2. **Hits CNI limits faster**
   - CNI plugins have process/thread limits
   - 10 simultaneous network setups per namespace
   - No stagger delay = immediate pressure

3. **Exhausts kubelet resources faster**
   - Each pod startup requires kubelet operations
   - 6.7x more pods = 6.7x more kubelet work
   - Faster pod turnover = sustained pressure

4. **Creates more realistic cluster stress**
   - Real clusters struggle with pod scheduling rate
   - Real CNI plugins have concurrency limits
   - This simulates actual cluster failure modes

## Customization

### Increase Pod Creation Speed Even More

```bash
# Create 50 pods per job with 20 parallel
BUILD_PARALLELISM=20 BUILDS_PER_NS=50 python3 create-deployment.py --total-namespaces 3

# Expected: 150 total pods, 60 concurrent
```

### Reduce Per-Pod Duration Further

Edit the build script to remove sleep delays:

```bash
# Current: ~15 seconds (5 workers × 3 seconds phases)
# Faster: ~5 seconds (remove sleep, immediate completion)

lightweight_build() {
    echo "Quick build"
    for i in $(seq 1 5); do
        (sleep 5 && echo "Worker $i done") &  # Changed from 15 to 5
    done
    # Remove phase sleep delays for even faster completion
}
```

### Test Without Build Jobs

To compare with old behavior:

```bash
# Disable build jobs, only create deployments
BUILD_JOB_ENABLED=false python3 create-deployment.py --total-namespaces 10
```

## Files Modified

### Python Implementation
- **File**: `/Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/python/create-deployment.py`
- **Lines changed**:
  - 71-77: Default configuration
  - 863-916: Build script (lightweight version)
  - 1194-1195: Removed stagger delay

### Go Implementation
- **File**: `/Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go/create-deployment.go`
- **Lines changed**:
  - 94-96: Default configuration
  - 646-698: Build script (lightweight version)
  - 917-918: Removed stagger delay

## Summary

**Goal**: Trigger "resource temporarily unavailable" errors ASAP

**Solution**: Create many pods quickly instead of few pods with many processes

**Results**:
- ✅ 6.7x more pods created (20 vs 3)
- ✅ 3.3x higher concurrency (10 vs 3)
- ✅ 50% faster per-pod completion (15s vs 30-40s)
- ✅ 95% fewer processes per pod (10-15 vs 300+)
- ✅ No stagger delay (immediate creation)
- ✅ Faster overall completion (30s vs 40s)

**Cluster impact**:
- Overwhelms scheduler, CNI, and kubelet faster
- Triggers realistic cluster failure modes
- Should hit "resource temporarily unavailable" within 30-60 seconds with moderate namespace count

## Next Steps

1. **Test with 2 namespaces** to verify pod creation rate
2. **Monitor cluster events** for resource exhaustion errors
3. **Adjust parallelism** if needed (`BUILD_PARALLELISM` environment variable)
4. **Increase namespaces** gradually to find breaking point
