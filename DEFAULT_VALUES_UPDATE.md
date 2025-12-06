# Default Values Update - Build Jobs Enabled, StatefulSets Disabled

## Overview

Updated default configuration values in both Python and Go implementations to better suit typical testing scenarios.

---

## Changes Made

### 1. Build Job Simulation - Now Enabled by Default

**Previous Default**: `BUILD_JOB_ENABLED=false` (disabled)
**New Default**: `BUILD_JOB_ENABLED=true` (enabled)

**Rationale**:
- Build jobs are the primary feature for simulating realistic CI/CD workloads
- They create the process overhead patterns observed in must-gather analysis
- Most users want to test build job impact on cluster resources
- Easier to disable if not needed than to remember to enable

**How to Disable**:
```bash
# Python
BUILD_JOB_ENABLED=false python3 create-deployment.py
# or
python3 create-deployment.py --no-build-job

# Go
BUILD_JOB_ENABLED=false ./create-deployment
# or
./create-deployment --no-build-job
```

### 2. StatefulSet OOM Simulation - Now Disabled by Default

**Previous Default**: `STATEFULSET_ENABLED=true` (enabled)
**New Default**: `STATEFULSET_ENABLED=false` (disabled)

**Rationale**:
- StatefulSet OOM simulation is a specialized test case
- Most users don't need OOM testing by default
- Reduces default resource usage
- Cleaner test output without OOM pod restarts
- Users can explicitly enable when needed

**How to Enable**:
```bash
# Python
STATEFULSET_ENABLED=true python3 create-deployment.py
# or
python3 create-deployment.py --statefulset-enabled

# Go
STATEFULSET_ENABLED=true ./create-deployment
# or
./create-deployment --statefulset-enabled
```

---

## Impact on Default Behavior

### Before These Changes

Running with no arguments would create:
```
Per namespace:
  ✅ 3 deployments (nginx pods)
  ✅ 1 StatefulSet with 3 replicas (OOM simulation)
  ❌ No build jobs (had to explicitly enable)
  ❌ No services (still disabled by default)

Result: Mix of basic deployments and OOM testing, but NO build simulation
```

### After These Changes

Running with no arguments now creates:
```
Per namespace:
  ✅ 3 deployments (nginx pods)
  ✅ 20 build jobs with realistic simulation
  ❌ No StatefulSets (can enable if needed)
  ❌ No services (still disabled by default)

Result: Realistic build job simulation is the default use case
```

---

## Files Modified

### Python Implementation

**File**: `python/create-deployment.py`

**Lines 67, 73** - Config defaults:
```python
# Before
self.statefulset_enabled = os.getenv('STATEFULSET_ENABLED', 'true').lower() == 'true'
self.build_job_enabled = os.getenv('BUILD_JOB_ENABLED', 'false').lower() == 'true'

# After
self.statefulset_enabled = os.getenv('STATEFULSET_ENABLED', 'false').lower() == 'true'
self.build_job_enabled = os.getenv('BUILD_JOB_ENABLED', 'true').lower() == 'true'
```

**Lines 1400, 1405** - Help text:
```python
# Before
STATEFULSET_ENABLED (default: true)
BUILD_JOB_ENABLED (default: false)

# After
STATEFULSET_ENABLED (default: false)
BUILD_JOB_ENABLED (default: true)
```

**Lines 1418-1419** - Updated resource values in help:
```python
# Before
- Resource requests: 256Mi RAM, 200m CPU (realistic for JEE builds)
- Resource limits: 768Mi RAM, 1000m CPU (allows burst during compilation)

# After
- Resource requests: 64Mi RAM, 50m CPU (reduced for resource-constrained testing)
- Resource limits: 256Mi RAM, 500m CPU (reduced for testing environments)
```

**Lines 1445-1457** - Updated examples:
```python
# Before
# StatefulSet with OOM simulation (enabled by default)
%(prog)s --statefulset-replicas 5 --statefulsets-per-ns 2

# Disable StatefulSet creation
%(prog)s --no-statefulset

# Build job simulation (disabled by default)
%(prog)s --build-job-enabled --builds-per-ns 10 --build-parallelism 5
BUILD_JOB_ENABLED=true BUILDS_PER_NS=10 %(prog)s

# After
# StatefulSet with OOM simulation (disabled by default)
%(prog)s --statefulset-enabled --statefulset-replicas 5 --statefulsets-per-ns 2
STATEFULSET_ENABLED=true %(prog)s

# Build job simulation (enabled by default)
%(prog)s --builds-per-ns 10 --build-parallelism 5

# Disable build job creation
%(prog)s --no-build-job
BUILD_JOB_ENABLED=false %(prog)s
```

### Go Implementation

**File**: `go/create-deployment.go`

**Lines 90, 93** - Config defaults:
```go
// Before
StatefulSetEnabled: getEnvBool("STATEFULSET_ENABLED", true),
BuildJobEnabled:    getEnvBool("BUILD_JOB_ENABLED", false),

// After
StatefulSetEnabled: getEnvBool("STATEFULSET_ENABLED", false),
BuildJobEnabled:    getEnvBool("BUILD_JOB_ENABLED", true),
```

---

## Usage Examples

### Quick Start (Default Behavior)

**Python**:
```bash
# This now creates build jobs by default
python3 create-deployment.py --total-namespaces 5

# What gets created per namespace:
# - 3 nginx deployments
# - 20 build jobs (realistic CI/CD simulation)
# - NO StatefulSets (disabled by default)
```

**Go**:
```bash
# This now creates build jobs by default
./create-deployment --total-namespaces 5

# What gets created per namespace:
# - 3 nginx deployments
# - 20 build jobs (realistic CI/CD simulation)
# - NO StatefulSets (disabled by default)
```

### Custom Configurations

**Only Build Jobs (No Deployments)**:
```bash
# Python
python3 create-deployment.py --deployments-per-ns 0 --total-namespaces 5

# Go
./create-deployment --deployments-per-ns 0 --total-namespaces 5

# Result: Only build jobs created (20 per namespace)
```

**Build Jobs + StatefulSets**:
```bash
# Python
STATEFULSET_ENABLED=true python3 create-deployment.py --total-namespaces 3

# Go
STATEFULSET_ENABLED=true ./create-deployment --total-namespaces 3

# Result: Build jobs + StatefulSets with OOM simulation
```

**Only Deployments (No Build Jobs)**:
```bash
# Python
BUILD_JOB_ENABLED=false python3 create-deployment.py --total-namespaces 10

# Go
BUILD_JOB_ENABLED=false ./create-deployment --total-namespaces 10

# Result: Only nginx deployments (like original behavior)
```

**Everything Enabled**:
```bash
# Python
STATEFULSET_ENABLED=true SERVICE_ENABLED=true python3 create-deployment.py

# Go
STATEFULSET_ENABLED=true SERVICE_ENABLED=true ./create-deployment

# Result: Deployments + Services + Build Jobs + StatefulSets
```

---

## Default Resource Usage Comparison

### Before (StatefulSets Enabled, Build Jobs Disabled)

For 10 namespaces with default settings:
```
Resources created per namespace:
  - 3 nginx deployments (3 pods)
  - 1 StatefulSet (3 pods, will OOM and restart)
  - 0 build jobs

Total pods: 60 (30 deployments + 30 StatefulSet pods)
Resource usage: ~2-4GB RAM, ~1-2 CPU cores
OOM restarts: Yes (StatefulSet pods continuously restarting)
```

### After (Build Jobs Enabled, StatefulSets Disabled)

For 10 namespaces with default settings:
```
Resources created per namespace:
  - 3 nginx deployments (3 pods)
  - 20 build jobs (max 3 concurrent per job)
  - 0 StatefulSets

Total pods: 30 deployments + up to 30 concurrent build pods
Resource usage: ~1.5-5GB RAM, ~1-15 CPU cores (bursty)
OOM restarts: No (clean pod lifecycle)
```

---

## Environment Variable Summary

| Variable | Old Default | New Default | Override |
|----------|-------------|-------------|----------|
| `STATEFULSET_ENABLED` | `true` | `false` | Set to `true` to enable |
| `BUILD_JOB_ENABLED` | `false` | `true` | Set to `false` to disable |
| `SERVICE_ENABLED` | `false` | `false` | (unchanged) |
| `CURL_TEST_ENABLED` | `false` | `false` | (unchanged) |
| `CLEANUP_ON_COMPLETION` | `false` | `false` | (unchanged) |

All other defaults remain unchanged.

---

## CLI Arguments Summary

| Argument | Purpose | Default Behavior |
|----------|---------|------------------|
| `--statefulset-enabled` | Enable StatefulSets | Creates OOM simulation pods |
| `--no-statefulset` | Disable StatefulSets | Skip StatefulSet creation |
| `--build-job-enabled` | Enable build jobs | Creates 20 build jobs/namespace |
| `--no-build-job` | Disable build jobs | Skip build job creation |
| `--service-enabled` | Enable services | Creates ClusterIP services |
| `--no-service` | Disable services | Skip service creation |

**Note**: `--build-job-enabled` is now redundant (builds are on by default), but kept for backward compatibility.

---

## Migration Guide

### If You Were Using Default Settings

**Before**:
```bash
# Old behavior: No build jobs, StatefulSets with OOM
python3 create-deployment.py --total-namespaces 10
```

**To Get Same Behavior**:
```bash
# New equivalent: Explicitly disable builds, enable StatefulSets
BUILD_JOB_ENABLED=false STATEFULSET_ENABLED=true \
  python3 create-deployment.py --total-namespaces 10

# or
python3 create-deployment.py --total-namespaces 10 \
  --no-build-job --statefulset-enabled
```

### If You Were Explicitly Enabling Build Jobs

**Before**:
```bash
# Old: Had to explicitly enable
BUILD_JOB_ENABLED=true python3 create-deployment.py
```

**Now**:
```bash
# New: Just run it (builds are default)
python3 create-deployment.py
```

---

## Testing Recommendations

### Phase 1: Verify Default Behavior

```bash
# Test with minimal resources
python3 create-deployment.py --total-namespaces 2
# or
./create-deployment --total-namespaces 2

# Expected: 2 namespaces × (3 deployments + 20 build jobs)
```

### Phase 2: Verify Build Jobs Work

```bash
# Monitor build job creation
kubectl get jobs -A -l type=build-job -w

# Expected: 40 jobs total (20 per namespace × 2 namespaces)
```

### Phase 3: Verify StatefulSets Disabled

```bash
# Check for StatefulSets
kubectl get statefulsets -A -l deployment-test=true

# Expected: No resources found
```

### Phase 4: Enable StatefulSets if Needed

```bash
# Run with StatefulSets
STATEFULSET_ENABLED=true python3 create-deployment.py --total-namespaces 1

# Verify OOM behavior
kubectl get pods -A -l app=oom-simulator -w

# Expected: Pods will OOMKill and restart
```

---

## Rationale for Changes

### Why Enable Build Jobs by Default?

1. **Primary Use Case**: Build job simulation is the main feature enhancement
2. **Must-Gather Driven**: Based on real-world patterns from must-gather analysis
3. **Resource Testing**: Helps test cluster behavior under realistic CI/CD load
4. **Process Overhead**: Tests the 60-85 process creation pattern that caused issues
5. **CNI Testing**: Exercises CNI operations similar to real workloads

### Why Disable StatefulSets by Default?

1. **Specialized Test**: OOM simulation is a specific test case, not general-purpose
2. **Noisy Output**: OOM restarts create log noise and confusing pod states
3. **Resource Usage**: StatefulSet pods consume resources continuously
4. **Cleaner Defaults**: Most users want clean, successful pod lifecycles
5. **Explicit Intent**: Users who need OOM testing should explicitly request it

### Overall Philosophy

**Before**: Conservative defaults (minimal resource usage)
**After**: Realistic defaults (simulate actual workloads)

The new defaults better represent real-world cluster usage patterns while still allowing full customization.

---

## Backward Compatibility

### Scripts/Automation Impact

**Potential Impact**: Scripts that rely on default behavior will now create build jobs instead of StatefulSets.

**Mitigation**: Update scripts to explicitly set desired values:

```bash
# If you want old behavior
BUILD_JOB_ENABLED=false STATEFULSET_ENABLED=true ./create-deployment

# If you want new behavior (redundant but explicit)
BUILD_JOB_ENABLED=true STATEFULSET_ENABLED=false ./create-deployment
```

### Upgrade Path

No code changes required - only default values changed.

**To maintain old behavior**:
- Set `BUILD_JOB_ENABLED=false`
- Set `STATEFULSET_ENABLED=true`

**To use new behavior**:
- No changes needed (new defaults)

---

## Summary

✅ **Build jobs now enabled by default** (`BUILD_JOB_ENABLED=true`)
✅ **StatefulSets now disabled by default** (`STATEFULSET_ENABLED=false`)
✅ **Both Python and Go implementations updated**
✅ **Help text updated to reflect new defaults**
✅ **Examples updated in documentation**
✅ **Backward compatibility maintained via environment variables**

**Result**: Default behavior now focuses on realistic CI/CD build simulation while allowing specialized tests (like OOM) to be explicitly enabled.

**Files Updated**:
- `python/create-deployment.py` (config, help text, examples)
- `go/create-deployment.go` (config defaults)
- `DEFAULT_VALUES_UPDATE.md` (this file)

**Testing Status**: ✅ Both implementations compile and run successfully
