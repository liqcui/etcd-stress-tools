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
- **5 Deployments** (optional) - With mounted ConfigMaps and Secrets
- **2 Network Policies** - Complex ingress/egress rules
- **1 EgressFirewall** - OVN-Kubernetes egress rules

### Global Resources
- **BaselineAdminNetworkPolicy (BANP)** - Cluster-wide baseline policies
- **AdminNetworkPolicy (ANP)** objects - Advanced network policies
- **Images** - OpenShift Image objects (5 per namespace)

### Performance Features
- **Parallel Processing** - Configurable concurrent operations
- **Retry Logic** - Exponential backoff for failed operations
- **Resource Limits** - Configurable size limits to prevent cluster overload
- **Progress Monitoring** - Real-time status updates
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
| `NAMESPACE_PARALLEL` | `10` | Parallel namespace processing |
| `NAMESPACE_PREFIX` | `stress-test` | Prefix for namespace names |
| `TOTAL_LARGE_CONFIGMAP_LIMIT_GB` | `6` | Total size limit for large ConfigMaps |
| `CREATE_DEPLOYMENTS` | `true` | Enable deployment creation |
| `CREATE_EGRESS_FIREWALL` | `true` | Enable EgressFirewall creation |
| `CREATE_NETWORK_POLICIES` | `true` | Enable NetworkPolicy creation |
| `CREATE_ANP_BANP` | `true` | Enable ANP/BANP creation |
| `CREATE_IMAGES` | `true` | Enable Image creation |
| `MAX_CONCURRENT_OPERATIONS` | `50` | Maximum concurrent operations |
| `NAMESPACE_READY_TIMEOUT` | `60` | Namespace readiness timeout (seconds) |
| `RESOURCE_RETRY_COUNT` | `3` | Number of retries for failed operations |
| `RESOURCE_RETRY_DELAY` | `1.0` | Delay between retries (seconds) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `CLEANUP_ON_COMPLETION` | `false` | Enable cleanup after completion |

### Command Line Flags

```bash
./etcd-stress-tools --help
```

Key flags:
- `--total-namespaces`: Override total namespace count
- `--namespace-parallel`: Concurrent namespace processing
- `--large-limit`: ConfigMap size limit in GB
- `--enable-deployments`: Force enable deployments
- `--no-egress-firewall`: Disable EgressFirewall creation
- `--no-network-policies`: Disable NetworkPolicy creation
- `--no-anp-banp`: Disable ANP/BANP creation
- `--no-images`: Disable Image creation
- `--cleanup`: Enable cleanup after completion
- `--max-concurrent`: Maximum concurrent operations

## Usage Examples

### Basic Usage

```bash
# Run with default settings (100 namespaces)
./etcd-stress-tools

# Custom namespace count with parallel processing
./etcd-stress-tools --total-namespaces 50 --namespace-parallel 5

# Large scale test with cleanup
./etcd-stress-tools --total-namespaces 200 --large-limit 10 --cleanup

# Minimal test (no optional resources)
./etcd-stress-tools --total-namespaces 20 --no-images --no-egress-firewall
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
  resources: ["namespaces", "configmaps", "secrets"]
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

### Total Cluster Impact (100 namespaces)
- **Total Objects**: ~4,000 Kubernetes objects
- **Storage**: ~300-400MB of etcd data
- **Images**: 500 Image objects (if enabled)
- **Global Policies**: 1 BANP + ~33 ANP objects

## Monitoring and Troubleshooting

### Logs and Output

The tool provides colored, structured logging:
- **Green**: Successful operations and info messages
- **Yellow**: Warnings and retries
- **Red**: Errors and failures

### Common Issues

1. **Namespace Creation Timeout**
   ```
   Increase NAMESPACE_READY_TIMEOUT or reduce NAMESPACE_PARALLEL
   ```

2. **Resource Creation Failures**
   ```
   Check RBAC permissions and cluster resource quotas
   ```

3. **Out of Memory/Disk Space**
   ```
   Reduce TOTAL_NAMESPACES or disable large ConfigMaps
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
# Delete all stress test namespaces
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
```

### For Resource-Constrained Environments
```bash
export NAMESPACE_PARALLEL=5
export MAX_CONCURRENT_OPERATIONS=20
export CREATE_DEPLOYMENTS=false
export CREATE_IMAGES=false
```

### etcd Optimization
Consider these etcd settings for better performance during testing:
- Increase `--quota-backend-bytes`
- Tune `--heartbeat-interval` and `--election-timeout`
- Monitor disk I/O and consider faster storage
### Demo
[![asciicast](https://asciinema.org/a/745465.svg)](https://asciinema.org/a/745465)

### Contributing
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

## Support

For issues and questions:
1. Check the [troubleshooting section](#monitoring-and-troubleshooting)
2. Review logs for specific error messages
3. Verify RBAC permissions
4. Open an issue with detailed reproduction steps

