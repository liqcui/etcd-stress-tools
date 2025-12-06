# create-deployment - Go Implementation

Kubernetes deployment testing tool with realistic CI/CD build job simulation.

## ⚠️ macOS Users: Important Note

Due to macOS code signing issues with Go 1.22+ binaries, **use `go run` instead of the compiled binary**:

```bash
# ✅ This works on macOS
go run create-deployment.go --total-namespaces 5

# ✅ Or use the wrapper script
./run-create-deployment.sh --total-namespaces 5

# ❌ This will fail with "missing LC_UUID" error
./create-deployment --total-namespaces 5
```

See [MACOS_BINARY_FIX.md](MACOS_BINARY_FIX.md) for detailed explanation and solutions.

## Quick Start

### On macOS

```bash
cd /Users/liqcui/goproject/github.com/liqcui/ai-learning/etcd-stress-tools/go

# Option 1: Use go run (recommended)
go run create-deployment.go --total-namespaces 5

# Option 2: Use wrapper script
./run-create-deployment.sh --total-namespaces 5

# With environment variables
BUILD_PARALLELISM=5 go run create-deployment.go --total-namespaces 3
```

### On Linux

```bash
# Build once
go build -o create-deployment create-deployment.go

# Run normally
./create-deployment --total-namespaces 5
```

## Default Behavior

Running with **no flags** creates:

**Per namespace**:
- ✅ 3 nginx deployments (basic pods)
- ✅ 20 build jobs (realistic CI/CD simulation)
- ❌ No StatefulSets (disabled by default)
- ❌ No Services (disabled by default)

Example:
```bash
go run create-deployment.go --total-namespaces 5
```

Creates:
- 5 namespaces (`deploy-test-1` through `deploy-test-5`)
- 15 nginx deployments (3 per namespace)
- 100 build jobs (20 per namespace)

## Build Job Features

Each build job simulates a realistic CI/CD build pipeline:

- **7 realistic phases**: setup → dependencies → compile → test → package → publish
- **Process creation**: 60-85 background processes per build pod (matches must-gather observations)
- **CPU-intensive work**: Simulates actual compilation with background workers
- **Staggered creation**: 1-second delay between batches to prevent simultaneous starts
- **Resources**: 64Mi/50m request, 256Mi/500m limit (suitable for testing environments)
- **Auto-cleanup**: Pods removed 5 minutes after completion (TTL)
- **Realistic naming**: `build-{service}-{component}-{version}-s-1`

### Build Job Configuration

- **Jobs per namespace**: 20 unique jobs
- **Completions per job**: 10 (default)
- **Parallelism**: 3 concurrent build pods (default)
- **Duration**: 1-2 minutes per build (randomized)
- **Timeout**: 15 minutes (900 seconds)

## Configuration

All settings via environment variables or CLI flags:

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TOTAL_NAMESPACES` | `100` | Number of namespaces to create |
| `NAMESPACE_PARALLEL` | `10` | Parallel namespace processing |
| `NAMESPACE_PREFIX` | `deploy-test` | Prefix for namespace names |
| `DEPLOYMENTS_PER_NS` | `3` | Deployments per namespace |
| `MAX_CONCURRENT_OPERATIONS` | `50` | Max concurrent operations |

### Build Job Settings (Enabled by Default)

| Variable | Default | Description |
|----------|---------|-------------|
| `BUILD_JOB_ENABLED` | `true` | **Enable build job creation** |
| `BUILDS_PER_NS` | `10` | Build completions per job |
| `BUILD_PARALLELISM` | `3` | Concurrent build pods |
| `BUILD_TIMEOUT` | `900` | Job timeout (seconds) |

### StatefulSet Settings (Disabled by Default)

| Variable | Default | Description |
|----------|---------|-------------|
| `STATEFULSET_ENABLED` | `false` | **Enable StatefulSet OOM simulation** |
| `STATEFULSET_REPLICAS` | `3` | Replicas per StatefulSet |
| `STATEFULSETS_PER_NS` | `1` | StatefulSets per namespace |

### Other Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_ENABLED` | `false` | Create ClusterIP services |
| `CLEANUP_ON_COMPLETION` | `false` | Cleanup after completion |
| `LOG_LEVEL` | `INFO` | Logging level |

## Examples

### 1. Basic Test (2 Namespaces)

```bash
go run create-deployment.go --total-namespaces 2
```

Creates:
- 2 namespaces
- 6 deployments (3 per namespace)
- 40 build jobs (20 per namespace)

### 2. Custom Build Parallelism

```bash
BUILD_PARALLELISM=5 go run create-deployment.go --total-namespaces 5
```

Creates more concurrent build pods (5 instead of 3).

### 3. Disable Build Jobs (Old Behavior)

```bash
BUILD_JOB_ENABLED=false go run create-deployment.go --total-namespaces 10
```

Creates only deployments, no build jobs.

### 4. Enable StatefulSets

```bash
STATEFULSET_ENABLED=true go run create-deployment.go --total-namespaces 3
```

Adds StatefulSets with OOM simulation to each namespace.

### 5. Everything Enabled

```bash
STATEFULSET_ENABLED=true SERVICE_ENABLED=true \
  go run create-deployment.go --total-namespaces 5
```

Creates deployments, services, build jobs, and StatefulSets.

### 6. High Load Test

```bash
BUILD_PARALLELISM=8 BUILDS_PER_NS=20 \
  go run create-deployment.go --total-namespaces 3
```

Higher parallelism for stress testing.

### 7. With Cleanup

```bash
go run create-deployment.go --total-namespaces 5 --cleanup
```

Automatically deletes all resources after completion.

## CLI Arguments

```bash
--total-namespaces N          # Number of namespaces (default: 100)
--namespace-parallel N        # Parallel processing (default: 10)
--namespace-prefix PREFIX     # Namespace prefix (default: deploy-test)
--deployments-per-ns N        # Deployments per namespace (default: 3)

--build-job-enabled           # Enable build jobs (redundant, on by default)
--no-build-job                # Disable build jobs
--builds-per-ns N             # Build completions (default: 10)
--build-parallelism N         # Concurrent builds (default: 3)
--build-timeout N             # Timeout in seconds (default: 900)

--statefulset-enabled         # Enable StatefulSets
--no-statefulset              # Disable StatefulSets (redundant, off by default)
--statefulset-replicas N      # Replicas per StatefulSet (default: 3)
--statefulsets-per-ns N       # StatefulSets per namespace (default: 1)

--service-enabled             # Create services
--no-service                  # Don't create services (default)

--cleanup                     # Cleanup after completion
--log-level LEVEL             # Set logging level
--help                        # Show help message
```

## Monitoring

### Watch Build Jobs

```bash
# Watch build job creation
kubectl get jobs -A -l type=build-job -w

# Count build jobs
kubectl get jobs -A -l type=build-job --no-headers | wc -l
```

### Watch Build Pods

```bash
# Watch build pods
kubectl get pods -A -l type=build-job -w

# Count running build pods
kubectl get pods -A -l type=build-job --field-selector=status.phase=Running --no-headers | wc -l
```

### View Build Logs

```bash
# Get a running build pod
BUILD_POD=$(kubectl get pods -A -l type=build-job \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}' -n deploy-test-1)

# View logs
kubectl logs -f $BUILD_POD -n deploy-test-1
```

### Check Process Count

```bash
# Verify 60-85 processes per build pod
kubectl exec $BUILD_POD -n deploy-test-1 -- ps aux | wc -l
```

Expected: 60-85 processes during compilation phase.

### Monitor Resource Usage

```bash
# Check actual CPU/memory usage
kubectl top pods -A -l type=build-job

# Expected during compilation:
# CPU: 200-450m (close to 500m limit is normal)
# MEM: 80-200Mi (close to 256Mi limit is normal)
```

## Cleanup

### Delete All Test Resources

```bash
kubectl delete ns -l deployment-test=true
```

### Delete Build Jobs Only

```bash
kubectl delete jobs -A -l type=build-job
```

### Delete Specific Namespace

```bash
kubectl delete ns deploy-test-1
```

## Resource Estimates

### Per Namespace (Default Settings)

- **Deployments**: 3 pods (~300Mi RAM, ~300m CPU)
- **Build Jobs**: 20 jobs, max 3 concurrent pods (~200-800Mi RAM, ~150-1500m CPU bursty)
- **Total**: ~500Mi-1GB RAM, ~450m-1.8 CPU per namespace

### 10 Namespaces Total

- **Deployments**: 30 pods
- **Build Jobs**: 200 jobs, max 30 concurrent pods
- **Total Cluster**: ~3-10GB RAM, ~2-16 CPU cores (bursty during builds)

## Troubleshooting

### Build Pods Not Starting

**Symptoms**: Jobs created but no pods appear.

**Diagnosis**:
```bash
kubectl get jobs -A -l type=build-job
kubectl describe job <job-name> -n <namespace>
```

**Common Causes**:
- Node resource constraints
- Resource quotas
- Image pull failures

**Solutions**:
- Check node capacity: `kubectl describe nodes`
- Reduce `BUILD_PARALLELISM` or `TOTAL_NAMESPACES`
- Check resource quotas: `kubectl get resourcequotas -A`

### Build Pods Failing

**Symptoms**: Pods in Failed/Error state.

**Diagnosis**:
```bash
kubectl logs <build-pod> -n <namespace>
kubectl describe pod <build-pod> -n <namespace>
```

**Common Causes**:
- OOMKilled (insufficient memory)
- Timeout (active deadline exceeded)
- Security context restrictions

**Solutions**:
- Check for OOMKilled in pod status
- Increase `BUILD_TIMEOUT`
- Review security policies

### Too Much Cluster Load

**Symptoms**: Cluster unresponsive, many pending pods.

**Immediate Actions**:
```bash
# Delete all build jobs
kubectl delete jobs -A -l type=build-job

# Wait for pods to terminate
kubectl get pods -A -l type=build-job -w
```

**Prevention**:
- Reduce `BUILD_PARALLELISM` (try 2-3)
- Reduce `TOTAL_NAMESPACES` (try 2-5)
- Set `BUILD_JOB_ENABLED=false` for testing without builds

## Files

- `create-deployment.go` - Main Go implementation
- `run-create-deployment.sh` - Wrapper script for macOS (avoids dyld issues)
- `MACOS_BINARY_FIX.md` - macOS troubleshooting guide
- `GO_CONVERSION_SUMMARY.md` - Python to Go conversion details
- `CREATE_DEPLOYMENT_README.md` - This file

## Python Alternative

Identical functionality available in Python:

```bash
cd ../python
python3 create-deployment.py --total-namespaces 5
```

Both implementations have:
- Same default values
- Same configuration options
- Same build job simulation
- Same resource creation patterns

## Documentation

See parent directory for comprehensive documentation:

- `../RESOURCE_REDUCTION.md` - Resource configuration details
- `../DEFAULT_VALUES_UPDATE.md` - Default value changes
- `../QUICK_REFERENCE.txt` - Quick reference card
- `../python/BUILD_JOB_ENHANCEMENT.md` - Build job architecture (applies to both)
- `../python/BUILD_JOB_QUICK_START.md` - Build job quick start guide

## Support

For issues:
1. Check [MACOS_BINARY_FIX.md](MACOS_BINARY_FIX.md) for binary execution problems
2. Review [Troubleshooting](#troubleshooting) section above
3. Check parent directory documentation
4. Verify Kubernetes cluster has sufficient resources

## Summary

**Recommended Usage on macOS**:
```bash
go run create-deployment.go --total-namespaces 5
```

**Default Behavior**: Creates deployments + build jobs (realistic CI/CD simulation)

**Key Features**:
- 20 build jobs per namespace with realistic 7-phase pipeline
- 60-85 processes per build pod
- Reduced resources for testing environments (64Mi/50m → 256Mi/500m)
- Automatic cleanup with TTL (5 minutes)
