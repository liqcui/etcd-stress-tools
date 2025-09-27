# FIO Benchmark Analyzer for OpenShift

A comprehensive storage performance analysis tool for OpenShift clusters that runs FIO benchmarks on master/control-plane nodes and provides intelligent analysis using LLM integration.

## Features

- **Automated Node Discovery**: Automatically discovers OpenShift master/control-plane nodes
- **Comprehensive Benchmark Suite**: 10 different FIO tests covering various workload patterns
- **Database Storage**: Stores results in DuckDB for historical analysis and comparison
- **LLM-Powered Analysis**: Real-time streaming analysis using LangChain/LangGraph
- **Comparison Tools**: Compare performance across clusters and analyze trends over time
- **Validation Against Baselines**: Automatic validation against etcd performance requirements
- **Multiple Output Formats**: JSON reports, HTML dashboards, and console summaries

## Architecture

The tool consists of four main modules:

1. **fio_benchmark_analyzer.py**: Main orchestration and test execution
2. **fio_benchmark_db.py**: DuckDB storage and retrieval
3. **fio_benchmark_comparison.py**: Cross-cluster and trend analysis
4. **fio_benchmark_utils.py**: Utility functions and validation logic

## Prerequisites

### System Requirements

- OpenShift CLI (`oc`) installed and configured
- Access to OpenShift cluster with cluster-admin or equivalent permissions
- Python 3.8 or higher
- Network access to `quay.io/openshift-psap-qe/fio` container image

### OpenShift Cluster Requirements

- Access to master/control-plane nodes
- Podman available on master nodes
- Permissions to run `oc debug node/...` commands
- Write access to `/var/lib/etcd/` on master nodes

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd fio-benchmark-analyzer
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Configure OpenShift CLI:
```bash
oc login <cluster-url>
```

4. (Optional) Configure LLM integration:
```bash
# Create .env file
cat > .env << EOF
OPENAI_API_KEY=your-api-key-here
BASE_URL=https://your-llm-endpoint
EOF
```

## Usage

### Basic Benchmark Run

Run all tests on the first master node:
```bash
python fio_benchmark_analyzer.py
```

### Target Specific Node

Run tests on a specific master node:
```bash
python fio_benchmark_analyzer.py --node master-0.example.com
```

### Run Specific Test

Run only one test type:
```bash
python fio_benchmark_analyzer.py --test fsync_write
```

### Enable LLM Analysis

Enable real-time streaming LLM analysis:
```bash
python fio_benchmark_analyzer.py --enable-llm
```

### Disable Database Storage

Run without saving to database:
```bash
python fio_benchmark_analyzer.py --no-db
```

### Disable File Output

Run without saving JSON/HTML reports:
```bash
python fio_benchmark_analyzer.py --no-save
```

## Available Tests

| Test Name | Description | Key Metrics |
|-----------|-------------|-------------|
| `fsync_write` | etcd fsync performance | sync P99 latency (<10ms) |
| `mixed_libaio_seq_read_1g_r70_w30` | 1GB mixed read-heavy | IOPS, bandwidth, latency |
| `mixed_libaio_seq_read_256m_r70_w30` | 256MB mixed read-heavy | IOPS, latency |
| `mixed_libaio_seq_write_256m_r30_w70` | 256MB mixed write-heavy | Write IOPS, bandwidth |
| `mixed_libaio_seq_write_1g_r30_w70` | 1GB mixed write-heavy | Write IOPS, bandwidth |
| `solo_libaio_seek_1g` | 1GB random read | Random read IOPS (>20k) |
| `solo_libaio_seek_256m` | 256MB random read | Random read IOPS (>25k) |
| `basic_fio_random_latency` | Low-latency random write | Write latency (<100μs) |
| `basic_fio_random_bandwidth` | High-throughput write | Bandwidth (>400 MB/s) |
| `basic_fio_random_iops` | Maximum IOPS test | Write IOPS (>60k) |

## Comparison and Analysis

### List All Clusters

View all clusters stored in the database:
```bash
python fio_benchmark_analyzer.py --list-clusters
```

### Compare Two Clusters

Compare performance between two clusters:
```bash
python fio_benchmark_analyzer.py --compare \
  --cluster1 prod-cluster-abc \
  --cluster2 staging-cluster-xyz
```

Compare specific test only:
```bash
python fio_benchmark_analyzer.py --compare \
  --cluster1 prod-cluster-abc \
  --cluster2 staging-cluster-xyz \
  --test fsync_write
```

### Trend Analysis

Analyze performance trends for a cluster over time:
```bash
python fio_benchmark_analyzer.py --trend prod-cluster-abc \
  --test fsync_write
```

## Output Files

Each run generates multiple output files:

- `fio_benchmark_report_YYYYMMDD_HHMMSS.json`: Complete JSON report
- `fio_benchmark_report_YYYYMMDD_HHMMSS.html`: Interactive HTML dashboard
- `fio_benchmarks.duckdb`: Database file (persistent across runs)

## Performance Baselines

The tool validates against etcd performance requirements:

### Critical Metrics

- **fsync P99 latency**: < 10ms (must pass for etcd health)
- **Random write latency P99**: < 100μs at iodepth=1
- **Random write IOPS**: > 15,000 at iodepth=1
- **System CPU usage**: < 30%
- **Disk utilization**: < 60% for low queue depth tests

### Warning Thresholds

- Within 20% of minimum thresholds (for throughput metrics)
- Within 50% of maximum thresholds (for latency metrics)

## Database Schema

The DuckDB database stores results with the following key fields:

- `uuid`: Unique identifier for each test result
- `run_uuid`: Groups all tests from a single benchmark run
- `cluster_name`: OpenShift cluster identifier
- `infrastructure_type`: Cloud platform (AWS, Azure, GCP, etc.)
- `node_name`: Specific master node tested
- `test_name`: FIO test type
- `timestamp`: Test execution time
- Performance metrics: IOPS, bandwidth, latency, CPU, disk utilization

## LLM Analysis Features

When `--enable-llm` is enabled:

- Real-time streaming analysis output
- Performance assessment against etcd requirements
- Bottleneck identification
- Optimization recommendations
- Concerning pattern detection
- Practical implications for cluster health

## Troubleshooting

### Permission Denied Errors

Ensure you have cluster-admin permissions:
```bash
oc auth can-i debug node
```

### Container Pull Errors

Verify network access to quay.io:
```bash
podman pull quay.io/openshift-psap-qe/fio
```

### Database Locked Errors

Ensure no other processes are using the database:
```bash
lsof fio_benchmarks.duckdb
```

### Test Timeout

Default timeout is 600s (10 minutes). Check node responsiveness:
```bash
oc debug node/<node-name> -- chroot /host uptime
```

## Advanced Configuration

### Custom Database Path

Specify a custom database location:
```bash
python fio_benchmark_analyzer.py --db-path /path/to/custom.duckdb
```

### Environment Variables

Configure in `.env` file:
```bash
OPENAI_API_KEY=sk-...
BASE_URL=https://api.openai.com/v1
```

## Performance Impact

The tool is designed to minimize impact on cluster operations:

- Tests run on one master node at a time
- Uses dedicated test files in `/var/lib/etcd/`
- Automatic cleanup after each test
- 5-second delay between consecutive tests
- Tests use isolated storage paths

## Demo
[![asciicast](https://asciinema.org/a/745573.svg)](https://asciinema.org/a/745573)

## Contributing

Contributions are welcome! Areas for improvement:

- Additional test configurations
- Support for worker node testing
- Grafana dashboard integration
- Prometheus metrics export
- Multi-node concurrent testing

## License

[Specify your license here]

## Support

For issues and questions:
- Open an issue in the repository
- Check existing documentation
- Review OpenShift storage best practices

## Version History

- **v1.0.0**: Initial release with core functionality
- **v1.1.0**: Added DuckDB storage and comparison features
- **v1.2.0**: Streaming LLM analysis and enhanced validation
- **v1.3.0**: Additional test configurations and solo random read tests

## References and Documentation

### Official Documentation
- [etcd Performance Documentation](https://etcd.io/docs/latest/op-guide/performance/) - Official etcd performance tuning guide
- [FIO Documentation](https://fio.readthedocs.io/) - Flexible I/O tester documentation
- [OpenShift Node Debugging](https://docs.openshift.com/container-platform/latest/support/troubleshooting/investigating-pod-issues.html#debugging-nodes) - Official guide for debugging OpenShift nodes
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/) - LangGraph framework documentation

### Related Tools and Projects
- [etcd-tools Repository](https://github.com/peterducai/etcd-tools) - Additional etcd monitoring and diagnostic tools

## Acknowledgments

- OpenShift Performance and Scale team for FIO container
- LangChain/LangGraph for LLM integration framework
- DuckDB for embedded analytics database
- Peter Ducai for etcd-tools reference implementation