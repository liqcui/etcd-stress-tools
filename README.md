# OpenShift etcd Stress Testing Tool

A high-performance for comprehensive Kubernetes resource creation to stress test etcd performance in OpenShift clusters.

## Features

- **Modular Design**: Separate functions for each resource type
- **Maximum Concurrency**: Optimized async operations with configurable limits
- **Comprehensive Resource Creation**: ConfigMaps, Secrets, Deployments, Network Policies, and more
- **Flexible Configuration**: Environment variables and command-line options
- **Optional Cleanup**: Automated resource cleanup after testing
- **Progress Monitoring**: Real-time progress reporting with colored output

## Resources Created

### Per Namespace (100 namespaces by default)
- **10 Small ConfigMaps** (5 key-value pairs each)
- **1 Large ConfigMap** (1MB size, respects 6GB total limit)
- **10 Small Secrets** (username, password, token)
- **10 Large Secrets** (TLS certificates, SSH keys, large tokens)
- **1 EgressFirewall** (10 rules with Allow/Deny policies)
- **2 NetworkPolicies** (deny-by-default + complex egress rules)
- **5 Deployments** (optional, with mounted ConfigMaps/Secrets)

### Cluster-Wide Resources
- **1 BaselineAdminNetworkPolicy (BANP)** (deny-all baseline)
- **33+ AdminNetworkPolicies (ANP)** (total_namespaces/3, with fake IPs)
- **500+ Images** (total_namespaces × 5, OpenShift Image resources)