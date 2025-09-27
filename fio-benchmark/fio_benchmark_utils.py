#!/usr/bin/env python3
"""
FIO Benchmark Utilities
Common functions and utilities for FIO benchmark analysis
"""

import re
import json
import subprocess
import logging
from typing import Dict, List, Optional, Union, Any
from dataclasses import dataclass
from datetime import datetime
import yaml
import os
import shutil
import glob


class fioAnalyzerUtils:
    """Common utilities for FIO benchmark analysis"""
    
    def __init__(self):
        self.logger = self._setup_logging()
        
    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger(f"{__name__}.utils")
        
    def execute_oc_command(self, cmd: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Execute OpenShift CLI command with error handling"""
        try:
            self.logger.debug(f"Executing: {' '.join(cmd)}")
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=timeout,
                check=False
            )
            
            if result.returncode != 0:
                self.logger.error(f"Command failed: {result.stderr}")
                
            return result
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"Command timeout after {timeout}s: {' '.join(cmd)}")
            raise
        except Exception as e:
            self.logger.error(f"Error executing command: {e}")
            raise
            
    def get_nodes_by_label(self, label_selector: str, 
                          output_format: str = "jsonpath={.items[*].metadata.name}") -> List[str]:
        """Get nodes by label selector"""
        cmd = ["oc", "get", "nodes", "-l", label_selector, "-o", output_format]
        result = self.execute_oc_command(cmd)
        
        if result.returncode != 0:
            raise Exception(f"Failed to get nodes with label {label_selector}: {result.stderr}")
            
        nodes = result.stdout.strip().split() if result.stdout.strip() else []
        self.logger.info(f"Found {len(nodes)} nodes with label '{label_selector}': {nodes}")
        return nodes
        
    def get_node_info(self, node_name: str) -> Dict[str, Any]:
        """Get detailed information about a specific node"""
        cmd = ["oc", "get", "node", node_name, "-o", "json"]
        result = self.execute_oc_command(cmd)
        
        if result.returncode != 0:
            raise Exception(f"Failed to get node info for {node_name}: {result.stderr}")
            
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse node info JSON: {e}")
            
    def check_node_ready(self, node_name: str) -> bool:
        """Check if node is in Ready state"""
        try:
            node_info = self.get_node_info(node_name)
            conditions = node_info.get("status", {}).get("conditions", [])
            
            for condition in conditions:
                if condition.get("type") == "Ready":
                    return condition.get("status") == "True"
                    
            return False
        except Exception as e:
            self.logger.error(f"Error checking node {node_name} readiness: {e}")
            return False
            
    def parse_fio_json_output(self, json_output: str) -> Dict[str, Any]:
        """Parse FIO JSON output format"""
        try:
            return json.loads(json_output)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse FIO JSON output: {e}")
            raise
            
    def parse_fio_text_output(self, text_output: str) -> Dict[str, Any]:
        """Parse FIO text output format and extract key metrics - Enhanced for mixed block sizes"""
        metrics = {}
        
        # Extract IOPS - Enhanced to handle 'k' suffix
        read_iops_match = re.search(r'read: IOPS=(\d+)', text_output, re.MULTILINE)
        write_iops_match = re.search(r'write: IOPS=([0-9.]+)k?', text_output, re.MULTILINE)
        
        if read_iops_match:
            metrics['iops_read'] = float(read_iops_match.group(1))
            
        if write_iops_match:
            value_str = write_iops_match.group(1)
            # Check if original has 'k' suffix
            if 'k' in write_iops_match.group(0):
                metrics['iops_write'] = float(value_str) * 1000
            else:
                metrics['iops_write'] = float(value_str)
            
        # Extract Bandwidth (MiB/s)
        read_bw_match = re.search(r'read:.*?BW=([0-9.]+)MiB/s', text_output, re.MULTILINE)
        write_bw_match = re.search(r'write:.*?BW=([0-9.]+)MiB/s', text_output, re.MULTILINE)
        
        if read_bw_match:
            metrics['bw_read_mbs'] = float(read_bw_match.group(1))
        if write_bw_match:
            metrics['bw_write_mbs'] = float(write_bw_match.group(1))
            
        # Extract CPU usage
        cpu_match = re.search(r'cpu\s*:\s*usr=([0-9.]+)%.*?sys=([0-9.]+)%', text_output, re.MULTILINE)
        if cpu_match:
            metrics['cpu_usr_pct'] = float(cpu_match.group(1))
            metrics['cpu_sys_pct'] = float(cpu_match.group(2))
            
        # Extract disk utilization - Enhanced to handle aggrutil
        disk_util_match = re.search(r'util=([0-9.]+)%', text_output, re.MULTILINE)
        if not disk_util_match:
            disk_util_match = re.search(r'aggrutil=([0-9.]+)%', text_output, re.MULTILINE)
        if disk_util_match:
            metrics['disk_util_pct'] = float(disk_util_match.group(1))
            
        # Extract runtime
        runtime_match = re.search(r'run=(\d+)-\d+msec', text_output, re.MULTILINE)
        if runtime_match:
            metrics['runtime_ms'] = int(runtime_match.group(1))
            
        return metrics
            
    def parse_size_string(self, size_str: str) -> int:
        """Parse size string (e.g., '32m', '1G') to bytes"""
        if not size_str:
            return 0
            
        size_str = size_str.strip().upper()
        multipliers = {
            'B': 1,
            'K': 1024, 'KB': 1024,
            'M': 1024**2, 'MB': 1024**2, 'MIB': 1024**2,
            'G': 1024**3, 'GB': 1024**3, 'GIB': 1024**3,
            'T': 1024**4, 'TB': 1024**4, 'TIB': 1024**4
        }
        
        # Extract number and unit
        match = re.match(r'^(\d+(?:\.\d+)?)\s*([A-Z]*)$', size_str)
        if not match:
            raise ValueError(f"Invalid size format: {size_str}")
            
        number, unit = match.groups()
        number = float(number)
        unit = unit or 'B'
        
        if unit not in multipliers:
            raise ValueError(f"Unknown size unit: {unit}")
            
        return int(number * multipliers[unit])
        
    def format_bytes(self, bytes_val: Union[int, float], decimal_places: int = 2) -> str:
        """Format bytes to human readable string"""
        if bytes_val == 0:
            return "0 B"
            
        units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
        bytes_val = float(bytes_val)
        
        for unit in units:
            if bytes_val < 1024.0:
                if unit == 'B':
                    return f"{int(bytes_val)} {unit}"
                else:
                    return f"{bytes_val:.{decimal_places}f} {unit}"
            bytes_val /= 1024.0
            
        return f"{bytes_val:.{decimal_places}f} {units[-1]}"
        
    def format_duration(self, milliseconds: int) -> str:
        """Format duration in milliseconds to human readable string"""
        if milliseconds < 1000:
            return f"{milliseconds}ms"
        elif milliseconds < 60000:
            return f"{milliseconds/1000:.1f}s"
        elif milliseconds < 3600000:
            minutes = milliseconds // 60000
            seconds = (milliseconds % 60000) // 1000
            return f"{minutes}m{seconds}s"
        else:
            hours = milliseconds // 3600000
            minutes = (milliseconds % 3600000) // 60000
            return f"{hours}h{minutes}m"
            
    def extract_numeric_metric(self, text: str, pattern: str, 
                             group_index: int = 1) -> Optional[float]:
        """Extract numeric metric from text using regex"""
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if match:
            try:
                return float(match.group(group_index))
            except (ValueError, IndexError):
                return None
        return None
        
    def parse_percentile_data(self, text: str, metric_type: str = "clat") -> Dict[str, float]:
        """Parse FIO percentile data from output"""
        percentiles = {}
        
        # Pattern to match percentile lines
        pattern = rf'{metric_type} percentiles.*?\n((?:\s*\|.*?\n)*)'
        match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
        
        if not match:
            return percentiles
            
        percentile_text = match.group(1)
        
        # Extract individual percentiles
        perc_pattern = r'(\d+\.\d+)th=\[\s*(\d+)\]'
        for match in re.finditer(perc_pattern, percentile_text):
            percentile = float(match.group(1))
            value = float(match.group(2))
            percentiles[f"p{percentile}"] = value
            
        return percentiles
        
    def calculate_iops_from_bandwidth(self, bandwidth_mbs: float, block_size: int) -> float:
        """Calculate IOPS from bandwidth and block size"""
        if bandwidth_mbs <= 0 or block_size <= 0:
            return 0.0
        
        bandwidth_bytes = bandwidth_mbs * 1024 * 1024
        return bandwidth_bytes / block_size
        
    def calculate_bandwidth_from_iops(self, iops: float, block_size: int) -> float:
        """Calculate bandwidth (MB/s) from IOPS and block size"""
        if iops <= 0 or block_size <= 0:
            return 0.0
            
        bandwidth_bytes = iops * block_size
        return bandwidth_bytes / (1024 * 1024)
        
    def validate_fio_command(self, command: str) -> bool:
        """Validate FIO command syntax (basic validation)"""
        required_params = ['--name=', '--size=']
        
        for param in required_params:
            if param not in command:
                self.logger.warning(f"FIO command missing required parameter: {param}")
                return False
                
        return True
        
    def create_fio_command(self, test_config: Dict[str, Any]) -> str:
        """Create FIO command from configuration dictionary"""
        base_cmd = test_config.get('base_command', 'fio')
        params = []
        
        # Required parameters
        required = ['name', 'size']
        for param in required:
            if param not in test_config:
                raise ValueError(f"Missing required parameter: {param}")
            params.append(f"--{param}={test_config[param]}")
            
        # Optional parameters
        optional_params = [
            'ioengine', 'rw', 'bs', 'iodepth', 'runtime', 'directory',
            'filename', 'direct', 'sync', 'fdatasync', 'ramp_time',
            'readwrite', 'rwmixread', 'rwmixwrite', 'percentage_random'
        ]
        
        for param in optional_params:
            if param in test_config:
                value = test_config[param]
                if isinstance(value, bool):
                    if value:
                        params.append(f"--{param}")
                else:
                    params.append(f"--{param}={value}")
                    
        return f"{base_cmd} {' '.join(params)}"
        
    def save_results_to_file(self, data: Any, filename: str, 
                           format_type: str = "json") -> None:
        """Save data to file in specified format"""
        try:
            if format_type.lower() == "json":
                with open(filename, 'w') as f:
                    json.dump(data, f, indent=2, default=str)
            elif format_type.lower() == "yaml":
                with open(filename, 'w') as f:
                    yaml.dump(data, f, default_flow_style=False)
            else:
                raise ValueError(f"Unsupported format: {format_type}")
                
            self.logger.info(f"Data saved to {filename}")
            
        except Exception as e:
            self.logger.error(f"Failed to save data to {filename}: {e}")
            raise
            
    def load_results_from_file(self, filename: str) -> Any:
        """Load data from file (auto-detect format)"""
        try:
            with open(filename, 'r') as f:
                if filename.endswith('.json'):
                    return json.load(f)
                elif filename.endswith(('.yaml', '.yml')):
                    return yaml.safe_load(f)
                else:
                    # Try JSON first, then YAML
                    content = f.read()
                    f.seek(0)
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        return yaml.safe_load(content)
                        
        except Exception as e:
            self.logger.error(f"Failed to load data from {filename}: {e}")
            raise
            
    def generate_test_report_html(self, results: List[Dict], 
                                output_file: str = "fio_report.html") -> None:
        """Generate HTML report from test results"""
        html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>FIO Benchmark Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background-color: #f0f0f0; padding: 20px; border-radius: 5px; }}
        .test-result {{ margin: 20px 0; border: 1px solid #ddd; padding: 15px; border-radius: 5px; }}
        .success {{ border-left: 5px solid #4CAF50; }}
        .failure {{ border-left: 5px solid #f44336; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
        .metric {{ font-weight: bold; }}
        .timestamp {{ color: #666; font-size: 0.9em; }}
        .runuuid {{ color: #333; font-size: 0.9em; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>FIO Benchmark Report</h1>
        <p class="timestamp">Generated: {timestamp}</p>
        <p class="runuuid">Run UUID: {run_uuid}</p>
        <p>Total Tests: {total_tests} | Successful: {successful} | Failed: {failed}</p>
    </div>
    
    {test_results}
</body>
</html>
"""

        test_results_html = []
        successful_tests = 0
        failed_tests = 0
        
        for result in results:
            if result.get('error'):
                failed_tests += 1
                status_class = "failure"
                status_text = f"FAILED: {result['error']}"
            else:
                successful_tests += 1
                status_class = "success"
                status_text = "SUCCESS"
                
            # Build metrics table
            metrics_html = "<table><tr><th>Metric</th><th>Value</th><th>Unit</th></tr>"
            
            metrics = [
                ("Test Name", result.get('test_name', 'N/A'), ""),
                ("Node", result.get('node_name', 'N/A'), ""),
                ("IOPS Read", result.get('iops_read', 'N/A'), "ops/sec"),
                ("IOPS Write", result.get('iops_write', 'N/A'), "ops/sec"),
                ("Bandwidth Read", result.get('bw_read_mbs', 'N/A'), "MB/s"),
                ("Bandwidth Write", result.get('bw_write_mbs', 'N/A'), "MB/s"),
                ("Completion Latency Avg", result.get('lat_clat_avg_us', 'N/A'), "μs"),
                ("Completion Latency P99", result.get('lat_clat_p99_us', 'N/A'), "μs"),
                ("Sync Latency Avg", result.get('sync_avg_us', 'N/A'), "μs"),
                ("CPU System", result.get('cpu_sys_pct', 'N/A'), "%"),
                ("Disk Utilization", result.get('disk_util_pct', 'N/A'), "%"),
                ("Runtime", result.get('runtime_ms', 'N/A'), "ms"),
            ]
            
            for metric, value, unit in metrics:
                if value is not None and value != 'N/A':
                    if isinstance(value, float):
                        value = f"{value:.2f}"
                    metrics_html += f"<tr><td class='metric'>{metric}</td><td>{value}</td><td>{unit}</td></tr>"
                    
            metrics_html += "</table>"
            
            test_html = f"""
            <div class="test-result {status_class}">
                <h3>{result.get('test_name', 'Unknown Test')} - {status_text}</h3>
                <p class="timestamp">Timestamp: {result.get('timestamp', 'N/A')}</p>
                {metrics_html}
            </div>
            """
            test_results_html.append(test_html)
            
        # Generate final HTML
        run_uuid = None
        if results and isinstance(results[0], dict):
            run_uuid = results[0].get('run_uuid')
        run_uuid = run_uuid or "N/A"
        final_html = html_template.format(
            timestamp=datetime.now().isoformat(),
            total_tests=len(results),
            successful=successful_tests,
            failed=failed_tests,
            test_results="\n".join(test_results_html),
            run_uuid=run_uuid
        )
        
        with open(output_file, 'w') as f:
            f.write(final_html)
            
        self.logger.info(f"HTML report generated: {output_file}")
        
    def compare_results(self, baseline_results: List[Dict], 
                       current_results: List[Dict]) -> Dict[str, Any]:
        """Compare current results with baseline results"""
        comparison = {
            "timestamp": datetime.now().isoformat(),
            "comparisons": [],
            "summary": {
                "improved": 0,
                "degraded": 0,
                "similar": 0
            }
        }
        
        # Group results by test name
        baseline_by_test = {r['test_name']: r for r in baseline_results if not r.get('error')}
        current_by_test = {r['test_name']: r for r in current_results if not r.get('error')}
        
        for test_name in set(baseline_by_test.keys()) & set(current_by_test.keys()):
            baseline = baseline_by_test[test_name]
            current = current_by_test[test_name]
            
            test_comparison = {
                "test_name": test_name,
                "metrics": {}
            }
            
            # Compare key metrics
            key_metrics = [
                ("iops_read", "higher_better"),
                ("iops_write", "higher_better"), 
                ("bw_read_mbs", "higher_better"),
                ("bw_write_mbs", "higher_better"),
                ("lat_clat_avg_us", "lower_better"),
                ("lat_clat_p99_us", "lower_better"),
                ("sync_avg_us", "lower_better"),
                ("cpu_sys_pct", "lower_better"),
                ("disk_util_pct", "lower_better")
            ]
            
            for metric, direction in key_metrics:
                baseline_val = baseline.get(metric)
                current_val = current.get(metric)
                
                if baseline_val is not None and current_val is not None:
                    change_pct = ((current_val - baseline_val) / baseline_val) * 100
                    
                    if direction == "higher_better":
                        if change_pct > 5:
                            status = "improved"
                        elif change_pct < -5:
                            status = "degraded"
                        else:
                            status = "similar"
                    else:  # lower_better
                        if change_pct < -5:
                            status = "improved"
                        elif change_pct > 5:
                            status = "degraded"
                        else:
                            status = "similar"
                            
                    test_comparison["metrics"][metric] = {
                        "baseline": baseline_val,
                        "current": current_val,
                        "change_pct": change_pct,
                        "status": status
                    }
                    
                    comparison["summary"][status] += 1
                    
            comparison["comparisons"].append(test_comparison)
            
        return comparison
        
    def get_etcd_performance_baselines(self) -> Dict[str, Dict[str, float]]:
        """Get recommended etcd performance baselines - Updated with basic_fio_random_latency"""
        return {
            "fsync_write": {
                "sync_p99_us_max": 10000,
                "sync_p9999_us_max": 50000,
                "iops_write_min": 500,
                "description": "etcd fsync performance requirements"
            },
            "mixed_libaio_seq_read_1g_r70_w30": {
                "lat_clat_p99_us_max": 1000,
                "iops_read_min": 8000,
                "bw_read_mbs_min": 30,
                "description": "etcd general I/O performance"
            },
            "mixed_libaio_seq_read_256m_r70_w30": {
                "lat_clat_p99_us_max": 2000,
                "iops_read_min": 5000,
                "iops_write_min": 2000,
                "bw_read_mbs_min": 20,
                "bw_write_mbs_min": 8,
                "cpu_sys_pct_max": 20,
                "description": "Mixed sequential workload performance (70% read, 30% write)"
            },
            "mixed_libaio_seq_write_256m_r30_w70": {
                "lat_clat_p99_us_max": 3000,
                "iops_read_min": 3000,
                "iops_write_min": 8000,
                "bw_read_mbs_min": 8,
                "bw_write_mbs_min": 30,
                "cpu_sys_pct_max": 25,
                "disk_util_pct_max": 85,
                "description": "Mixed sequential workload performance (30% read, 70% write) with mixed block sizes"
            },
            "mixed_libaio_seq_write_1g_r30_w70": {
                "lat_clat_p99_us_max": 250,
                "iops_read_min": 4000,
                "iops_write_min": 10000,
                "bw_read_mbs_min": 8,
                "bw_write_mbs_min": 40,
                "cpu_sys_pct_max": 20,
                "disk_util_pct_max": 85,
                "description": "1GB write-heavy mixed workload (30% read, 70% write) with mixed block sizes"
            },
            "solo_libaio_seek_1g": {
                "iops_read_min": 20000,
                "bw_read_mbs_min": 80,
                "cpu_sys_pct_max": 25,
                "disk_util_pct_max": 85,
                "lat_clat_avg_us_max": 200,
                "lat_clat_p99_us_max": 500,
                "description": "Random read performance test with libaio engine and 4k blocks at iodepth=4 (1GB dataset)"
            },
            "solo_libaio_seek_256m": {
                "iops_read_min": 25000,
                "bw_read_mbs_min": 100,
                "cpu_sys_pct_max": 25,
                "disk_util_pct_max": 95,
                "lat_clat_avg_us_max": 150,
                "lat_clat_p99_us_max": 400,
                "description": "Random read performance test with libaio engine and 4k blocks at iodepth=4 (256MB dataset)"
            },
            "basic_fio_random_latency": {
                "iops_write_min": 15000,
                "bw_write_mbs_min": 60,
                "cpu_sys_pct_max": 30,
                "disk_util_pct_max": 60,
                "lat_clat_avg_us_max": 50,
                "lat_clat_p99_us_max": 100,
                "description": "Random write latency test with libaio engine at iodepth=1 (16GB dataset, 4k blocks) - Critical for etcd write performance"
            },
            "basic_fio_random_bandwidth": {
                "iops_write_min": 3500,
                "bw_write_mbs_min": 400,
                "cpu_sys_pct_max": 8,
                "disk_util_pct_max": 100,
                "lat_clat_avg_us_max": 35000,
                "lat_clat_p99_us_max": 95000,
                "description": "Random write bandwidth test with libaio engine at iodepth=128 (16GB dataset, 128k blocks) - Measures maximum throughput capability"
            },
            "basic_fio_random_iops": {
                "iops_write_min": 60000,
                "bw_write_mbs_min": 250,
                "cpu_sys_pct_max": 70,
                "disk_util_pct_max": 100,
                "lat_clat_avg_us_max": 2000,
                "lat_clat_p99_us_max": 2500,
                "description": "Random write IOPS test with libaio engine at iodepth=128 (16GB dataset, 4k blocks) - Measures maximum concurrent write operations"
            }
        }

    def validate_against_baselines(self, results: List[Dict]) -> Dict[str, Any]:
        """Validate results against etcd performance baselines"""
        baselines = self.get_etcd_performance_baselines()
        validation_report = {
            "timestamp": datetime.now().isoformat(),
            "validations": [],
            "summary": {
                "passed": 0,
                "failed": 0,
                "warnings": 0
            },
            "critical_issues": [],  # New: collect critical failures
            "warnings_list": []     # New: collect warnings
        }
        
        for result in results:
            if result.get('error'):
                continue
                
            test_name = result.get('test_name')
            if test_name not in baselines:
                continue
                
            baseline = baselines[test_name]
            test_validation = {
                "test_name": test_name,
                "node_name": result.get('node_name'),
                "checks": []
            }
            
            for metric, threshold in baseline.items():
                if metric == "description":
                    continue
                    
                actual_value = result.get(metric.replace('_max', '').replace('_min', ''))
                if actual_value is None:
                    continue
                    
                check = {
                    "metric": metric,
                    "threshold": threshold,
                    "actual": actual_value,
                    "status": "unknown"
                }
                
                if metric.endswith('_max'):
                    if actual_value <= threshold:
                        check["status"] = "passed"
                        validation_report["summary"]["passed"] += 1
                    elif actual_value <= threshold * 1.5:
                        check["status"] = "warning"
                        check["message"] = f"{metric}: {actual_value:.2f} exceeds threshold {threshold:.2f} (within 50% tolerance)"
                        validation_report["summary"]["warnings"] += 1
                        validation_report["warnings_list"].append({
                            "test": test_name,
                            "node": result.get('node_name'),
                            "metric": metric,
                            "actual": actual_value,
                            "threshold": threshold,
                            "message": check["message"]
                        })
                    else:
                        check["status"] = "failed"
                        check["message"] = f"{metric}: {actual_value:.2f} SIGNIFICANTLY exceeds threshold {threshold:.2f}"
                        validation_report["summary"]["failed"] += 1
                        validation_report["critical_issues"].append({
                            "test": test_name,
                            "node": result.get('node_name'),
                            "metric": metric,
                            "actual": actual_value,
                            "threshold": threshold,
                            "message": check["message"]
                        })
                        
                elif metric.endswith('_min'):
                    if actual_value >= threshold:
                        check["status"] = "passed"
                        validation_report["summary"]["passed"] += 1
                    elif actual_value >= threshold * 0.8:
                        check["status"] = "warning"
                        check["message"] = f"{metric}: {actual_value:.2f} below threshold {threshold:.2f} (within 20% tolerance)"
                        validation_report["summary"]["warnings"] += 1
                        validation_report["warnings_list"].append({
                            "test": test_name,
                            "node": result.get('node_name'),
                            "metric": metric,
                            "actual": actual_value,
                            "threshold": threshold,
                            "message": check["message"]
                        })
                    else:
                        check["status"] = "failed"
                        check["message"] = f"{metric}: {actual_value:.2f} SIGNIFICANTLY below threshold {threshold:.2f}"
                        validation_report["summary"]["failed"] += 1
                        validation_report["critical_issues"].append({
                            "test": test_name,
                            "node": result.get('node_name'),
                            "metric": metric,
                            "actual": actual_value,
                            "threshold": threshold,
                            "message": check["message"]
                        })
                        
                test_validation["checks"].append(check)
                
            if test_validation["checks"]:
                validation_report["validations"].append(test_validation)
            
        return validation_report

    def create_extended_fio_test(self, test_name: str, **kwargs) -> Dict[str, Any]:
        """Create extended FIO test configuration for future tests - Updated with solo_libaio_seek_256m"""
        base_config = {
            "name": test_name,
            "size": kwargs.get("size", "1G"),
            "bs": kwargs.get("bs", "4k"),
            "ioengine": kwargs.get("ioengine", "libaio"),
            "iodepth": kwargs.get("iodepth", 1),
            "direct": kwargs.get("direct", True),
            "runtime": kwargs.get("runtime", 300),
            "ramp_time": kwargs.get("ramp_time", 10)
        }
        
        # Test-specific configurations
        test_configs = {
            "random_read": {
                "rw": "randread",
                "description": "Random read performance test"
            },
            "random_write": {
                "rw": "randwrite", 
                "description": "Random write performance test"
            },
            "sequential_read": {
                "rw": "read",
                "description": "Sequential read performance test"
            },
            "sequential_write": {
                "rw": "write",
                "description": "Sequential write performance test"
            },
            "mixed_workload": {
                "rw": "rw",
                "rwmixread": kwargs.get("rwmixread", 70),
                "rwmixwrite": kwargs.get("rwmixwrite", 30),
                "description": "Mixed read/write workload test"
            },
            "mixed_libaio_seq_read_256m_r70_w30": {
                "rw": "rw",
                "rwmixread": 70,
                "rwmixwrite": 30,
                "size": "256M",
                "filename": "/var/lib/etcd/fio4seqread256m",
                "description": "Sequential mixed workload test (256MB, 70% read, 30% write)"
            },
            "mixed_libaio_seq_write_256m_r30_w70": {
                "rw": "rw",
                "rwmixread": 30,
                "rwmixwrite": 70,
                "size": "256M",
                "bs": "2k,4k",
                "filename": "/var/lib/etcd/fio4seqwrite256m",
                "description": "Write-heavy mixed workload test (256MB, 30% read, 70% write, mixed block sizes)"
            },
            "mixed_libaio_seq_write_1g_r30_w70": {
                "rw": "rw",
                "rwmixread": 30,
                "rwmixwrite": 70,
                "size": "1G",
                "bs": "2k,4k",
                "filename": "/var/lib/etcd/fio4seqwrite1g",
                "description": "1GB write-heavy mixed workload test (30% read, 70% write, mixed block sizes)"
            },
            "solo_libaio_seek_1g": {
                "rw": "randread",
                "iodepth": 4,
                "bs": "4k",
                "size": "1G",
                "filename": "/var/lib/etcd/seek1g",
                "description": "Random read performance test with libaio engine (1GB dataset, iodepth=4)"
            },
            "solo_libaio_seek_256m": {
                "rw": "randread",
                "iodepth": 4,
                "bs": "4k",
                "size": "256M",
                "filename": "/var/lib/etcd/seek256m",
                "description": "Random read performance test with libaio engine (256MB dataset, iodepth=4)"
            },
            "latency_test": {
                "rw": "randread",
                "iodepth": 1,
                "bs": "4k",
                "description": "Low-latency random read test"
            },
            "throughput_test": {
                "rw": "read",
                "iodepth": kwargs.get("iodepth", 32),
                "bs": kwargs.get("bs", "1M"),
                "description": "High-throughput sequential test"
            }
        }
        
        if test_name in test_configs:
            base_config.update(test_configs[test_name])
        
        # Add any additional kwargs
        for key, value in kwargs.items():
            if key not in base_config:
                base_config[key] = value
                
        return base_config

    def cleanup_test_files(self, test_paths: List[str]) -> None:
        """Clean up test files after testing completion - Enhanced for better error handling"""
        cleaned_files = []
        failed_files = []
        
        for path in test_paths:
            try:
                if os.path.exists(path):
                    if os.path.isfile(path):
                        os.remove(path)
                        self.logger.info(f"Cleaned up test file: {path}")
                        cleaned_files.append(path)
                    elif os.path.isdir(path):
                        shutil.rmtree(path)
                        self.logger.info(f"Cleaned up test directory: {path}")
                        cleaned_files.append(path)
                else:
                    self.logger.debug(f"Path does not exist (may have been already cleaned): {path}")
                        
            except PermissionError as e:
                self.logger.warning(f"Permission denied cleaning {path}: {e}")
                failed_files.append(path)
            except OSError as e:
                self.logger.warning(f"OS error cleaning {path}: {e}")
                failed_files.append(path)
            except Exception as e:
                self.logger.warning(f"Unexpected error cleaning {path}: {e}")
                failed_files.append(path)
                
        # Log summary
        if cleaned_files:
            self.logger.info(f"Successfully cleaned {len(cleaned_files)} files/directories")
        if failed_files:
            self.logger.warning(f"Failed to clean {len(failed_files)} files/directories: {failed_files}")
            
    def cleanup_fio_test_files(self, test_name: str) -> None:
        """Clean up FIO test files based on test name - Updated with basic_fio_random_iops"""
        cleanup_patterns = {
            "fsync_write": ["/var/lib/etcd/fio4cleanfsync*"],
            "mixed_libaio_seq_read_1g_r70_w30": ["/var/lib/etcd/fio4seqread1g*"],
            "mixed_libaio_seq_read_256m_r70_w30": ["/var/lib/etcd/fio4seqread256m*"],
            "mixed_libaio_seq_write_256m_r30_w70": ["/var/lib/etcd/fio4seqwrite256m*"],
            "mixed_libaio_seq_write_1g_r30_w70": ["/var/lib/etcd/fio4seqwrite1g*"],
            "solo_libaio_seek_1g": ["/var/lib/etcd/seek1g*"],
            "solo_libaio_seek_256m": ["/var/lib/etcd/seek256m*"],
            "basic_fio_random_latency": ["/var/lib/etcd/fio-disk.bin*"],
            "basic_fio_random_bandwidth": ["/var/lib/etcd/fio-disk.bin*"],
            "basic_fio_random_iops": ["/var/lib/etcd/fio-disk.bin*"]
        }
        
        patterns = cleanup_patterns.get(test_name, [])
        
        files_to_clean = []
        for pattern in patterns:
            files_to_clean.extend(glob.glob(pattern))
            
        if files_to_clean:
            self.cleanup_test_files(files_to_clean)
        else:
            self.logger.debug(f"No files found to cleanup for test: {test_name}")

    def auto_cleanup_fio_files(self) -> None:
        """Automatically clean up common FIO test files"""
        common_fio_patterns = [
            "/var/lib/etcd/fio4*",
            "/tmp/fio_test*",
            "/var/tmp/fio_*"
        ]
        
        all_files = []
        for pattern in common_fio_patterns:
            all_files.extend(glob.glob(pattern))
            
        if all_files:
            self.logger.info(f"Found {len(all_files)} FIO test files for cleanup")
            self.cleanup_test_files(all_files)
        else:
            self.logger.info("No FIO test files found for cleanup")
            
    def generate_performance_summary(self, results: List[Dict]) -> Dict[str, Any]:
        """Generate performance summary with key insights - Enhanced for solo_libaio_seek_1g"""
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_tests": len(results),
            "successful_tests": len([r for r in results if not r.get('error')]),
            "failed_tests": len([r for r in results if r.get('error')]),
            "performance_insights": {},
            "recommendations": [],
            "test_analysis": {}
        }
        
        successful_results = [r for r in results if not r.get('error')]
        
        if not successful_results:
            summary["recommendations"].append("All tests failed - investigate storage subsystem")
            return summary
            
        # Calculate averages and insights
        total_read_iops = sum(r.get('iops_read', 0) for r in successful_results if r.get('iops_read'))
        total_write_iops = sum(r.get('iops_write', 0) for r in successful_results if r.get('iops_write'))
        avg_cpu_sys = sum(r.get('cpu_sys_pct', 0) for r in successful_results if r.get('cpu_sys_pct')) / len(successful_results)
        avg_disk_util = sum(r.get('disk_util_pct', 0) for r in successful_results if r.get('disk_util_pct')) / len(successful_results) if any(r.get('disk_util_pct') for r in successful_results) else 0
        
        summary["performance_insights"] = {
            "total_read_iops": total_read_iops,
            "total_write_iops": total_write_iops,
            "average_cpu_system": avg_cpu_sys,
            "average_disk_utilization": avg_disk_util
        }
        
        # Analyze by test type
        test_types = {}
        for result in successful_results:
            test_name = result.get('test_name')
            if test_name:
                test_types[test_name] = result
                
        # Specific analysis for write-heavy test
        if 'mixed_libaio_seq_write_256m_r30_w70' in test_types:
            write_heavy_result = test_types['mixed_libaio_seq_write_256m_r30_w70']
            summary["test_analysis"]["write_heavy_workload"] = {
                "read_iops": write_heavy_result.get('iops_read', 0),
                "write_iops": write_heavy_result.get('iops_write', 0),
                "read_bandwidth": write_heavy_result.get('bw_read_mbs', 0),
                "write_bandwidth": write_heavy_result.get('bw_write_mbs', 0),
                "cpu_system": write_heavy_result.get('cpu_sys_pct', 0),
                "disk_utilization": write_heavy_result.get('disk_util_pct', 0)
            }
        
        # Specific analysis for solo random read test
        if 'solo_libaio_seek_1g' in test_types:
            seek_result = test_types['solo_libaio_seek_1g']
            summary["test_analysis"]["random_read_workload"] = {
                "read_iops": seek_result.get('iops_read', 0),
                "read_bandwidth": seek_result.get('bw_read_mbs', 0),
                "cpu_system": seek_result.get('cpu_sys_pct', 0),
                "cpu_user": seek_result.get('cpu_usr_pct', 0),
                "disk_utilization": seek_result.get('disk_util_pct', 0),
                "completion_latency_avg": seek_result.get('lat_clat_avg_us', 0)
            }
        
        # Generate recommendations
        if avg_cpu_sys > 15:
            summary["recommendations"].append("High system CPU usage detected - consider I/O optimization")
            
        if avg_disk_util > 80:
            summary["recommendations"].append("High disk utilization detected - may indicate storage bottleneck")
            
        if total_read_iops < 5000:
            summary["recommendations"].append("Read IOPS below recommended baseline - investigate storage performance")
            
        if total_write_iops < 1000:
            summary["recommendations"].append("Write IOPS below recommended baseline - check disk subsystem")
            
        # Specific recommendations for write-heavy workload
        if 'mixed_libaio_seq_write_256m_r30_w70' in test_types:
            write_result = test_types['mixed_libaio_seq_write_256m_r30_w70']
            write_iops = write_result.get('iops_write', 0)
            read_iops = write_result.get('iops_read', 0)
            
            if write_iops < 8000:
                summary["recommendations"].append("Write IOPS in write-heavy test below baseline - may impact etcd performance under write load")
            if read_iops < 3000:
                summary["recommendations"].append("Read IOPS in write-heavy test below baseline - ensure read performance maintains under write pressure")
            if write_result.get('disk_util_pct', 0) > 85:
                summary["recommendations"].append("Disk utilization very high in write-heavy test - consider storage upgrade")
        
        # Specific recommendations for random read test
        if 'solo_libaio_seek_1g' in test_types:
            seek_result = test_types['solo_libaio_seek_1g']
            read_iops = seek_result.get('iops_read', 0)
            read_bw = seek_result.get('bw_read_mbs', 0)
            cpu_sys = seek_result.get('cpu_sys_pct', 0)
            
            if read_iops < 20000:
                summary["recommendations"].append("Random read IOPS below baseline - may impact etcd read performance under load")
            if read_bw < 80:
                summary["recommendations"].append("Random read bandwidth below baseline - check storage read performance")
            if cpu_sys > 25:
                summary["recommendations"].append("High system CPU usage in random read test - may indicate I/O inefficiency")
            if seek_result.get('disk_util_pct', 0) > 85:
                summary["recommendations"].append("High disk utilization in random read test - storage may be bottleneck")
                
        return summary

    def format_validation_report(self, validation_report: Dict[str, Any]) -> str:
        """Format validation report with highlighted warnings and failures"""
        lines = []
        
        lines.append("=" * 80)
        lines.append("VALIDATION SUMMARY")
        lines.append("=" * 80)
        
        summary = validation_report["summary"]
        lines.append(f"Passed: {summary['passed']}, Warnings: {summary['warnings']}, Failed: {summary['failed']}")
        
        # Critical failures section
        if validation_report.get("critical_issues"):
            lines.append("\n" + "!" * 80)
            lines.append("*  CRITICAL FAILURES - IMMEDIATE ATTENTION REQUIRED")
            lines.append("!" * 80)
            
            for issue in validation_report["critical_issues"]:
                lines.append(f"\nTest: {issue['test']}")
                lines.append(f"Node: {issue['node']}")
                lines.append(f"Metric: {issue['metric']}")
                lines.append(f"  Expected: {issue['threshold']:.2f}")
                lines.append(f"  Actual:   {issue['actual']:.2f} âœ— FAILED")
                lines.append(f"  Issue: {issue['message']}")
                lines.append("-" * 80)
        
        # Warnings section
        if validation_report.get("warnings_list"):
            lines.append("\n" + "*" * 80)
            lines.append("*  WARNINGS - PERFORMANCE CONCERNS")
            lines.append("*" * 80)
            
            for warning in validation_report["warnings_list"]:
                lines.append(f"\nTest: {warning['test']}")
                lines.append(f"Node: {warning['node']}")
                lines.append(f"Metric: {warning['metric']}")
                lines.append(f"  Expected: {warning['threshold']:.2f}")
                lines.append(f"  Actual:   {warning['actual']:.2f} *  WARNING")
                lines.append(f"  Note: {warning['message']}")
                lines.append("-" * 80)
        
        # If everything passed
        if not validation_report.get("critical_issues") and not validation_report.get("warnings_list"):
            lines.append("\nâœ All metrics passed validation thresholds!")
        
        return "\n".join(lines)



