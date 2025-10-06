# etcd Stress Testing Tool

A comprehensive Go-based tool for stress testing etcd in OpenShift/Kubernetes clusters through intensive resource creation and management.

## Overview

This tool creates thousands of Kubernetes resources across multiple namespaces to simulate heavy etcd load and test cluster performance under stress. It's designed to help identify etcd performance bottlenecks, test cluster scaling limits, and validate etcd backup/recovery procedures.

## Features

### Resource Creation Per Namespace
- **10 Small ConfigMaps** - Basic configuration data
- **3 Large ConfigMaps** - 1MB each (configurable total limit)
- **10 Small Secrets** - Basic authentication data  
- **10 Large Secrets** - TLS certificates and large tokens
- **5 Deployments** (optional) - With mounted ConfigMaps and Secrets, validated for pod readiness
- **2 Network Policies** - Complex ingress/egress rules
- **1 EgressFirewall** - OVN-Kubernetes egress rules

### Global Resources
- **BaselineAdminNetworkPolicy (BANP)** - Cluster-wide baseline policies
- **AdminNetworkPolicy (ANP)** objects - Advanced network policies
- **Images** - OpenShift Image objects (5 per namespace)

### Performance Features
- **Parallel Processing** - Configurable concurrent operations
- **Retry Logic** - Exponential backoff for failed operations
- **Pod Readiness Validation** - Ensures deployment pods are running and ready before proceeding (120s timeout per deployment)
- **Resource Limits** - Configurable size limits to prevent cluster overload
- **Progress Monitoring** - Real-time status updates with pod status tracking
- **List-Only Mode** - Query and display existing resources without creating new ones
- **Cleanup Support** - Optional resource cleanup after testing

## Prerequisites

- Go 1.21 or later
- Kubernetes/OpenShift cluster access
- Appropriate RBAC permissions (see [Permissions](#permissions))

## Installation

### From Source

```bash
# Clone the repository
git clone <repository-url>
cd etcd-stress-tools

# Install dependencies
go mod tidy

# Build the binary
go build -o etcd-stress-tools etcd-stress-tools.go

# Make executable
chmod +x etcd-stress-tools
```

### Container Build

```bash
# Build container image
docker build -t etcd-stress-tools:latest .

# Or using podman
podman build -t etcd-stress-tools:latest .
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TOTAL_NAMESPACES` | `100` | Number of namespaces to create |
| `NAMESPACE_PARALLEL` | `5` | Parallel namespace processing |
| `NAMESPACE_PREFIX` | `stress-test` | Prefix for namespace names |
| `TOTAL_LARGE_CONFIGMAP_LIMIT_GB` | `6.0` | Total size limit for large ConfigMaps |
| `CREATE_DEPLOYMENTS` | `true` | Enable deployment creation |
| `CREATE_EGRESS_FIREWALL` | `true` | Enable EgressFirewall creation |
| `CREATE_NETWORK_POLICIES` | `true` | Enable NetworkPolicy creation |
| `CREATE_ANP_BANP` | `true` | Enable ANP/BANP creation |
| `CREATE_IMAGES` | `true` | Enable Image creation |
| `MAX_CONCURRENT_OPERATIONS` | `20` | Maximum concurrent operations |
| `NAMESPACE_READY_TIMEOUT` | `60` | Namespace readiness timeout (seconds) |
| `RESOURCE_RETRY_COUNT` | `5` | Number of retries for failed operations |
| `RESOURCE_RETRY_DELAY` | `2.0` | Base delay between retries (seconds) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `CLEANUP_ON_COMPLETION` | `false` | Enable cleanup after completion |

### Command Line Flags

```bash
./etcd-stress-tools --help
```

Key flags:
- `--total-namespaces`: Override total namespace count
- `--namespace-parallel`: Concurrent namespace processing
- `--namespace-prefix`: Custom prefix for namespace names
- `--large-limit`: ConfigMap size limit in GB
- `--enable-deployments`: Force enable deployments
- `--no-egress-firewall`: Disable EgressFirewall creation
- `--no-network-policies`: Disable NetworkPolicy creation
- `--no-anp-banp`: Disable ANP/BANP creation
- `--no-images`: Disable Image creation
- `--max-concurrent`: Maximum concurrent operations
- `--namespace-timeout`: Namespace readiness timeout in seconds
- `--retry-count`: Number of retries for failed operations
- `--retry-delay`: Delay between retries in seconds
- `--cleanup`: Enable cleanup after completion
- `--list-only`: Only list existing resources without creating new ones
- `--log-level`: Set logging level
- `--help`: Show help message

## Usage Examples

### Basic Usage

```bash
# Run with default settings (100 namespaces)
./etcd-stress-tools

# Custom namespace count with parallel processing
./etcd-stress-tools --total-namespaces 50 --namespace-parallel 10

# Large scale test with cleanup
./etcd-stress-tools --total-namespaces 200 --large-limit 10 --cleanup

# Minimal test (no optional resources)
./etcd-stress-tools --total-namespaces 20 --no-images --no-egress-firewall
```

### List Existing Resources

```bash
# List all stress-test resources without creating new ones
./etcd-stress-tools --list-only

# This will display:
# - Total pods, ConfigMaps, Secrets across namespaces
# - Breakdown of small vs large resources
# - NetworkPolicy counts
# - Image counts
# - ANP/BANP counts
```

### Environment Variable Configuration

```bash
# Set environment variables
export TOTAL_NAMESPACES=150
export NAMESPACE_PARALLEL=15
export CREATE_DEPLOYMENTS=false
export CLEANUP_ON_COMPLETION=true

# Run the tool
./etcd-stress-tools
```

### Container Usage

```bash
# Run in container with kubeconfig mounted
docker run -v ~/.kube/config:/root/.kube/config:ro \
  etcd-stress-tools:latest --total-namespaces 100 --cleanup

# Run in OpenShift with service account
oc run etcd-stress --image=etcd-stress-tools:latest \
  --serviceaccount=etcd-stress-sa \
  --restart=Never \
  -- --total-namespaces 50 --cleanup
```

## Permissions

The tool requires extensive RBAC permissions to create cluster resources:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: etcd-stress-tools
rules:
# Core resources
- apiGroups: [""]
  resources: ["namespaces", "configmaps", "secrets", "pods"]
  verbs: ["get", "list", "create", "delete"]
# Apps
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["get", "list", "create", "delete"]
# Networking
- apiGroups: ["networking.k8s.io"]
  resources: ["networkpolicies"]
  verbs: ["get", "list", "create", "delete"]
# OpenShift specific
- apiGroups: ["k8s.ovn.org"]
  resources: ["egressfirewalls"]
  verbs: ["get", "list", "create", "delete"]
- apiGroups: ["policy.networking.k8s.io"]
  resources: ["adminnetworkpolicies", "baselineadminnetworkpolicies"]
  verbs: ["get", "list", "create", "delete"]
- apiGroups: ["image.openshift.io"]
  resources: ["images"]
  verbs: ["get", "list", "create", "delete"]
```

## Resource Estimates

### Per Namespace Resources
- **Objects Created**: ~40 objects per namespace
- **Storage Impact**: ~3-4MB per namespace (with large ConfigMaps/Secrets)
- **Network Policies**: 2 per namespace + global ANP/BANP
- **Deployments**: 5 per namespace (optional), each with 1 pod replica

### Total Cluster Impact (100 namespaces)
- **Total Objects**: ~4,000 Kubernetes objects
- **Pods**: 500 pods (if deployments enabled)
- **Storage**: ~300-400MB of etcd data
- **Images**: 500 Image objects (if enabled)
- **Global Policies**: 1 BANP + ~33 ANP objects

### Deployment Pod Validation
- Each deployment waits up to **120 seconds** for pods to become ready
- Validates pod phase is `Running`
- Checks pod `Ready` condition is `True`
- Ensures all replicas are updated and ready

## Monitoring and Troubleshooting

### Logs and Output

The tool provides colored, structured logging:
- **Green**: Successful operations and info messages
- **Yellow**: Warnings and retries
- **Red**: Errors and failures
- **Cyan**: Section headers and summaries

### Pod Readiness Tracking

When deployments are enabled, the tool monitors:
```
[DEPLOYMENT] 2025-01-15 10:30:45 - Deployment stress-deployment-1 in namespace stress-test-1 is ready with 1 running pods
```

### Common Issues

1. **Namespace Creation Timeout**
   ```
   Solution: Increase NAMESPACE_READY_TIMEOUT or reduce NAMESPACE_PARALLEL
   ```

2. **Pod Not Ready Timeout**
   ```
   Error: "timeout waiting for deployment X pods to be ready in namespace Y"
   Solution: Check pod logs, increase deployment timeout, or verify cluster has sufficient resources
   ```

3. **Resource Creation Failures**
   ```
   Solution: Check RBAC permissions and cluster resource quotas
   ```

4. **Out of Memory/Disk Space**
   ```
   Solution: Reduce TOTAL_NAMESPACES or disable large ConfigMaps
   ```

5. **Connection Errors**
   ```
   The tool automatically retries on connection errors with exponential backoff
   Check: "connection reset by peer", "timeout", "EOF", "http2" errors in logs
   ```

### Monitoring etcd Performance

While running the tool, monitor:
```bash
# etcd metrics
curl -s http://localhost:2379/metrics | grep etcd_

# Kubernetes API server metrics
kubectl top nodes
kubectl get events --sort-by=.metadata.creationTimestamp

# Resource usage
kubectl get all --all-namespaces | wc -l

# Pod status
kubectl get pods --all-namespaces -l stress-test=true
```

## Cleanup

### Automatic Cleanup
```bash
./etcd-stress-tools --cleanup
# or
export CLEANUP_ON_COMPLETION=true
./etcd-stress-tools
```

### Manual Cleanup
```bash
# Delete all stress test namespaces (includes all resources within)
kubectl delete namespaces -l stress-test=true

# Delete global network policies
kubectl delete adminnetworkpolicies -l stress-test=true
kubectl delete baselineadminnetworkpolicies default

# Delete images
oc delete images -l stress-test=true
```

## Performance Tuning

### For Large Clusters
```bash
export NAMESPACE_PARALLEL=20
export MAX_CONCURRENT_OPERATIONS=100
export TOTAL_NAMESPACES=500
export RESOURCE_RETRY_COUNT=3
```

### For Resource-Constrained Environments
```bash
export NAMESPACE_PARALLEL=5
export MAX_CONCURRENT_OPERATIONS=20
export CREATE_DEPLOYMENTS=false
export CREATE_IMAGES=false
export TOTAL_NAMESPACES=50
```

### Optimizing Deployment Creation
- **Reduce parallel operations** if pods fail to schedule due to resource constraints
- **Monitor node resources** to ensure sufficient CPU/memory for pods
- **Consider disabling deployments** for pure etcd stress testing without pod overhead
- Deployment pod readiness timeout is fixed at 120s per deployment

### etcd Optimization
Consider these etcd settings for better performance during testing:
- Increase `--quota-backend-bytes` (default 2GB, consider 8GB+ for large tests)
- Tune `--heartbeat-interval` and `--election-timeout`
- Monitor disk I/O and consider faster storage (NVMe recommended)
- Enable etcd metrics and monitor:
  - `etcd_disk_backend_commit_duration_seconds`
  - `etcd_disk_wal_fsync_duration_seconds`
  - `etcd_server_has_leader`

## Demo
[![asciicast](https://asciinema.org/a/745465.svg)](https://asciinema.org/a/745465)

## Contributing
1. Fork the repository
2. Create a feature branch
3. Make changes and add tests
4. Submit a pull request

## License

[MIT License](LICENSE)

## Security Considerations

- This tool creates many cluster resources and should only be used in test environments
- Ensure proper RBAC is configured to limit access
- Monitor cluster resources during testing to prevent overload
- Always clean up resources after testing to prevent resource exhaustion
- The tool validates pod readiness but does not validate application functionality
- Large-scale testing can impact cluster performance for other workloads

## Support

For issues and questions:
1. Check the [troubleshooting section](#monitoring-and-troubleshooting)
2. Review logs for specific error messages
3. Verify RBAC permissions (especially for pods list/get)
4. Check pod status if deployment creation fails
5. Open an issue with detailed reproduction steps and logs

## Changelog

### Latest Updates
- **Pod Readiness Validation**: Deployments now wait for pods to be running and ready
- **Enhanced Monitoring**: Real-time pod status tracking during deployment creation
- **Improved Error Handling**: Better retry logic for connection errors and transient failures
- **List-Only Mode**: Query existing resources without creating new ones