# Build Job Resource Reduction for Testing Environments

## Overview

Reduced CPU and memory resource requests/limits for build jobs in both Python and Go implementations to accommodate resource-constrained testing environments.

---

## Changes Made

### Resource Configuration Update

**Previous Configuration (Production-grade)**:
```yaml
resources:
  requests:
    memory: 256Mi
    cpu: 200m
  limits:
    memory: 768Mi
    cpu: 1000m
```

**New Configuration (Testing-optimized)**:
```yaml
resources:
  requests:
    memory: 64Mi
    cpu: 50m
  limits:
    memory: 256Mi
    cpu: 500m
```

### Reduction Summary

| Resource | Previous Request | New Request | Reduction | Previous Limit | New Limit | Reduction |
|----------|------------------|-------------|-----------|----------------|-----------|-----------|
| Memory   | 256Mi            | 64Mi        | **-75%**  | 768Mi          | 256Mi     | **-67%** |
| CPU      | 200m             | 50m         | **-75%**  | 1000m          | 500m      | **-50%** |

---

## Impact Analysis

### Resource Usage Per Build Pod

**Previous (Production)**:
- Memory: 256Mi request, 768Mi limit
- CPU: 200m (0.2 cores) request, 1000m (1 core) limit

**New (Testing)**:
- Memory: 64Mi request, 256Mi limit
- CPU: 50m (0.05 cores) request, 500m (0.5 cores) limit

### Cluster Resource Usage Examples

#### Scenario 1: Safe Configuration (10 namespaces, 3 parallel builds)

**Total concurrent build pods**: 30

| Metric | Previous | New | Savings |
|--------|----------|-----|---------|
| Memory Request | 7.5GB | 1.9GB | **-75% (5.6GB)** |
| Memory Limit | 22.5GB | 7.5GB | **-67% (15GB)** |
| CPU Request | 6 cores | 1.5 cores | **-75% (4.5 cores)** |
| CPU Limit | 30 cores | 15 cores | **-50% (15 cores)** |

#### Scenario 2: Moderate Load (5 namespaces, 5 parallel builds)

**Total concurrent build pods**: 25

| Metric | Previous | New | Savings |
|--------|----------|-----|---------|
| Memory Request | 6.25GB | 1.6GB | **-75% (4.65GB)** |
| Memory Limit | 18.75GB | 6.25GB | **-67% (12.5GB)** |
| CPU Request | 5 cores | 1.25 cores | **-75% (3.75 cores)** |
| CPU Limit | 25 cores | 12.5 cores | **-50% (12.5 cores)** |

#### Scenario 3: High Load (3 namespaces, 8 parallel builds)

**Total concurrent build pods**: 24

| Metric | Previous | New | Savings |
|--------|----------|-----|---------|
| Memory Request | 6GB | 1.5GB | **-75% (4.5GB)** |
| Memory Limit | 18GB | 6GB | **-67% (12GB)** |
| CPU Request | 4.8 cores | 1.2 cores | **-75% (3.6 cores)** |
| CPU Limit | 24 cores | 12 cores | **-50% (12 cores)** |

---

## Files Modified

### Python Implementation
- **File**: `etcd-stress-tools/python/create-deployment.py`
- **Lines**: 1049-1056
- **Change**: Updated `client.V1ResourceRequirements`

### Go Implementation
- **File**: `etcd-stress-tools/go/create-deployment.go`
- **Lines**: 795-804
- **Change**: Updated `corev1.ResourceRequirements`

### Documentation Updates
1. `python/BUILD_JOB_ENHANCEMENT.md` - Updated resource configuration section
2. `python/BUILD_JOB_CHANGES_SUMMARY.md` - Updated resource changes
3. `python/BUILD_JOB_QUICK_START.md` - Updated resource usage section
4. `go/GO_CONVERSION_SUMMARY.md` - Updated build job configuration table

---

## Testing Recommendations

### Minimum Cluster Requirements (New Configuration)

For safe testing with **10 namespaces** × **3 parallel builds**:

**Minimum per worker node**:
- **Memory**: 2GB allocatable (for 30 concurrent build pods)
- **CPU**: 2 cores allocatable (for 30 concurrent build pods)

**Recommended cluster**:
- **3 worker nodes** with 4GB RAM, 2 CPU each
- **Total allocatable**: 12GB RAM, 6 CPU
- **Headroom**: Sufficient for system pods + build workload

### Testing Strategy

#### Phase 1: Minimal Test (Verify Functionality)
```bash
BUILD_JOB_ENABLED=true \
BUILD_PARALLELISM=2 \
BUILDS_PER_NS=5 \
TOTAL_NAMESPACES=2 \
./create-deployment --total-namespaces 2
```

**Resources needed**:
- 4 concurrent build pods max
- Memory: 256Mi-1GB
- CPU: 0.2-2 cores

#### Phase 2: Light Load (Safe Testing)
```bash
BUILD_JOB_ENABLED=true \
BUILD_PARALLELISM=3 \
BUILDS_PER_NS=10 \
TOTAL_NAMESPACES=5 \
./create-deployment --total-namespaces 5
```

**Resources needed**:
- 15 concurrent build pods max
- Memory: 960Mi-3.75GB
- CPU: 0.75-7.5 cores

#### Phase 3: Moderate Load (Stress Testing)
```bash
BUILD_JOB_ENABLED=true \
BUILD_PARALLELISM=5 \
BUILDS_PER_NS=15 \
TOTAL_NAMESPACES=5 \
./create-deployment --total-namespaces 5
```

**Resources needed**:
- 25 concurrent build pods max
- Memory: 1.6-6.25GB
- CPU: 1.25-12.5 cores

---

## Important Notes

### Process Creation Unchanged

The resource reduction **does NOT affect** the process creation simulation:
- ✅ Still creates 60-85 processes per build pod
- ✅ Still simulates realistic compilation workload
- ✅ Still creates CPU-intensive background workers
- ✅ Still tests CNI operation patterns

**Why it still works**:
- Process creation is driven by bash script execution, not resource limits
- CPU/memory limits affect scheduling and OOM behavior, not process creation
- The build script's `cpu_intensive_work()` function still spawns background processes
- Each build pod still creates realistic process overhead

### What Changed vs. What Stayed the Same

**Changed** (Resource Pressure):
- ✅ Lower CPU/memory requests → easier to schedule on small clusters
- ✅ Lower limits → less resource contention
- ✅ Cluster can handle more concurrent builds
- ✅ Reduced risk of node resource exhaustion

**Unchanged** (Simulation Accuracy):
- ✅ Same 7-phase build pipeline
- ✅ Same process creation (60-85 per pod)
- ✅ Same build duration (1-2 minutes)
- ✅ Same job naming pattern
- ✅ Same staggered creation
- ✅ Same CNI operation patterns

### When to Increase Resources

Consider increasing resources back to production values if:

1. **Testing production scenarios**: Need to match real-world resource patterns
2. **Large cluster available**: Have spare capacity for higher resource requests
3. **Stress testing resource limits**: Specifically testing OOM/CPU throttling behavior
4. **CI/CD pipeline validation**: Matching real build resource consumption

**How to increase**:

Edit the resource requirements in the code:

**Python** (`create-deployment.py`, line ~1053):
```python
resources=client.V1ResourceRequirements(
    requests={"memory": "256Mi", "cpu": "200m"},  # Production values
    limits={"memory": "768Mi", "cpu": "1000m"}
),
```

**Go** (`create-deployment.go`, line ~795):
```go
Resources: corev1.ResourceRequirements{
    Requests: corev1.ResourceList{
        corev1.ResourceMemory: resource.MustParse("256Mi"),  // Production values
        corev1.ResourceCPU:    resource.MustParse("200m"),
    },
    Limits: corev1.ResourceList{
        corev1.ResourceMemory: resource.MustParse("768Mi"),
        corev1.ResourceCPU:    resource.MustParse("1000m"),
    },
},
```

---

## Validation

### Verify Reduced Resources

**Check build pod resource requests**:
```bash
kubectl get pods -A -l type=build-job -o json | \
  jq '.items[0].spec.containers[0].resources'
```

**Expected output**:
```json
{
  "limits": {
    "cpu": "500m",
    "memory": "256Mi"
  },
  "requests": {
    "cpu": "50m",
    "memory": "64Mi"
  }
}
```

### Verify Process Creation Still Works

**Check process count in running build pod**:
```bash
# Get a running build pod
BUILD_POD=$(kubectl get pods -A -l type=build-job --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' -n deploy-test-1)

# Count processes
kubectl exec $BUILD_POD -n deploy-test-1 -- ps aux | wc -l
```

**Expected**: 60-85 processes (unchanged from previous configuration)

### Monitor Resource Usage

**Check actual resource consumption**:
```bash
kubectl top pods -A -l type=build-job
```

**Expected during compilation phase**:
```
NAMESPACE       NAME                                    CPU(cores)   MEMORY(bytes)
deploy-test-1   build-api-authentication-3-12-5-s-1...  200-450m     80-200Mi
deploy-test-2   build-worker-messaging-2-34-2-s-1...    180-400m     90-180Mi
```

**Note**: Actual usage may spike close to limits during compilation phase, which is expected.

---

## Summary

✅ **Reduced memory requests by 75%** (256Mi → 64Mi)
✅ **Reduced memory limits by 67%** (768Mi → 256Mi)
✅ **Reduced CPU requests by 75%** (200m → 50m)
✅ **Reduced CPU limits by 50%** (1000m → 500m)

✅ **Process creation unchanged** (still 60-85 per pod)
✅ **Build simulation unchanged** (still realistic 7-phase pipeline)
✅ **CNI operation patterns unchanged** (still creates network stress)

**Result**: Build job simulation can now run on resource-constrained testing clusters while maintaining realistic process overhead and CNI operation patterns.

**Suitable for**:
- Small development clusters (3 nodes, 4GB RAM, 2 CPU each)
- CI/CD testing environments with limited resources
- Local testing with minikube/kind/k3s
- Resource-constrained cloud instances

**Can be increased** for production testing when larger clusters are available.
