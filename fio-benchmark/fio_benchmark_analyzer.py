#!/usr/bin/env python3
"""
FIO Benchmark Analyzer for OpenShift Master Nodes
Automatically discovers master nodes and runs FIO benchmarks with LLM analysis
Updated with DuckDB storage integration
"""

import json
import os
import subprocess
import re
import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import uuid
from dotenv import load_dotenv

from fio_benchmark_utils import fioAnalyzerUtils
from fio_benchmark_db import fioBenchmarkDB
from langchain.schema import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


@dataclass
class FioResult:
    """Data class to store FIO benchmark results"""
    test_name: str
    timestamp: str
    node_name: str
    run_uuid: Optional[str] = None
    iops_read: Optional[float] = None
    iops_write: Optional[float] = None
    bw_read_mbs: Optional[float] = None
    bw_write_mbs: Optional[float] = None
    lat_clat_avg_us: Optional[float] = None
    lat_clat_p99_us: Optional[float] = None
    lat_clat_p9999_us: Optional[float] = None
    sync_avg_us: Optional[float] = None
    sync_p99_us: Optional[float] = None
    sync_p9999_us: Optional[float] = None
    cpu_usr_pct: Optional[float] = None
    cpu_sys_pct: Optional[float] = None
    disk_util_pct: Optional[float] = None
    runtime_ms: Optional[int] = None
    raw_output: str = ""
    error: Optional[str] = None


class AgentState(TypedDict):
    messages: List
    fio_results: List[FioResult]
    analysis: str


class fioBenchmarkAnalyzer:
    """Main class for FIO benchmark analysis on OpenShift master nodes"""
    def __init__(self, enable_llm: bool = False, 
                 db_path: str = "fio_benchmarks.duckdb",
                 enable_db: bool = True):
        self.logger = self._setup_logging()
        self.utils = fioAnalyzerUtils()
        self.enable_llm = enable_llm
        self.enable_db = enable_db
        
        # Initialize database if enabled
        self.db = fioBenchmarkDB(db_path) if enable_db else None
        
        # FIO test configurations
        self.fio_tests = {
            "fsync_write": {
                "name": "fio4cleanfsync",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --rw=write --ioengine=sync --fdatasync=1 --directory=/var/lib/etcd/ --size=32m --bs=8000 --name=fio4cleanfsync",
                "cleanup": "rm -rf /var/lib/etcd/fio4cleanfsync*"
            },
            "mixed_libaio_seq_read_1g_r70_w30": {
                "name": "seqread1g",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --name=seqread1g --filename=/var/lib/etcd/fio4seqread1g --runtime=300 --ioengine=libaio --direct=1 --ramp_time=10 --readwrite=rw --rwmixread=70 --rwmixwrite=30 --iodepth=1 --bs=4k --size=1G --percentage_random=0",
                "cleanup": "rm -rf /var/lib/etcd/fio4seqread1g*"
            },
            "mixed_libaio_seq_read_256m_r70_w30": {
                "name": "seqread256mb",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --name=seqread256mb --filename=/var/lib/etcd/fio4seqread256m --runtime=300 --ioengine=libaio --direct=1 --ramp_time=10 --readwrite=rw --rwmixread=70 --rwmixwrite=30 --iodepth=1 --bs=4k --size=256M",
                "cleanup": "rm -rf /var/lib/etcd/fio4seqread256m*"
            },
            "mixed_libaio_seq_write_256m_r30_w70": {
                "name": "seqwrite1G",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --name=seqwrite1G --filename=/var/lib/etcd/fio4seqwrite256m --runtime=300 --ioengine=libaio --direct=1 --ramp_time=10 --readwrite=rw --rwmixread=30 --rwmixwrite=70 --iodepth=1 --bs=2k,4k --size=256M",
                "cleanup": "rm -rf /var/lib/etcd/fio4seqwrite256m*"
            },
            "mixed_libaio_seq_write_1g_r30_w70": {
                "name": "seqwrite1mb",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --name=seqwrite1mb --filename=/var/lib/etcd/fio4seqwrite1g --runtime=300 --ioengine=libaio --direct=1 --ramp_time=10 --readwrite=rw --rwmixread=30 --rwmixwrite=70 --iodepth=1 --bs=2k,4k --size=1G",
                "cleanup": "rm -rf /var/lib/etcd/fio4seqwrite1g*"
            },
            "solo_libaio_seek_1g": {
                "name": "seek1g",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --name=seek1g --filename=/var/lib/etcd/seek1g --runtime=300 --ioengine=libaio --direct=1 --ramp_time=10 --iodepth=4 --readwrite=randread --blocksize=4k --size=1G",
                "cleanup": "rm -rf /var/lib/etcd/seek1g*"
            },
            "solo_libaio_seek_256m": {
                "name": "seek256m",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --name=seek256m --filename=/var/lib/etcd/seek256m --runtime=300 --ioengine=libaio --direct=1 --ramp_time=10 --iodepth=4 --readwrite=randread --blocksize=4k --size=256M",
                "cleanup": "rm -rf /var/lib/etcd/seek256m*"
            },
            "basic_fio_random_latency": {
                "name": "4k_randwrite",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --ioengine=libaio --runtime=300s --direct=1 --time_based --group_reporting --ramp_time=0 --size=12G --bs=4k --iodepth=1 --filename=/var/lib/etcd/fio-disk.bin --name=4k_randwrite --rw=randwrite",
                "cleanup": "rm -rf /var/lib/etcd/fio-disk.bin*"
            },
            "basic_fio_random_bandwidth": {
                "name": "128k_randwrite",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --ioengine=libaio --runtime=300s --direct=1 --time_based --group_reporting --ramp_time=0 --size=12G --bs=128k --iodepth=128 --filename=/var/lib/etcd/fio-disk.bin --name=128k_randwrite --rw=randwrite",
                "cleanup": "rm -rf /var/lib/etcd/fio-disk.bin*"
            },
            "basic_fio_random_iops": {
                "name": "4k_randwrite",
                "command": "podman run -v /var/lib/etcd/:/var/lib/etcd/:Z --privileged quay.io/openshift-psap-qe/fio fio --ioengine=libaio --runtime=300s --direct=1 --time_based --group_reporting --ramp_time=0 --size=12G --bs=4k --iodepth=128 --filename=/var/lib/etcd/fio-disk.bin --name=4k_randwrite --rw=randwrite",
                "cleanup": "rm -rf /var/lib/etcd/fio-disk.bin*"
            }
        }

    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger(__name__)

    def _sanitize_llm_text(self, text: str) -> str:
        """Clean up LLM output: unescape literal newlines/tabs, trim spaces, and collapse blank lines.

        This makes the analysis easier to read in console and JSON reports by:
        - Converting literal "\\n"/"\\t" to actual newlines/tabs
        - Normalizing CRLF to LF
        - Stripping leading/trailing whitespace per line
        - Collapsing consecutive empty lines to at most one
        """
        if not text:
            return ""

        # Normalize newlines and unescape common sequences
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = cleaned.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "")

        # Trim each line and collapse multiple blank lines
        lines = [line.strip() for line in cleaned.split("\n")]
        normalized_lines = []
        previous_blank = False
        for line in lines:
            is_blank = (line == "")
            if is_blank and previous_blank:
                continue
            normalized_lines.append(line)
            previous_blank = is_blank

        # Remove leading/trailing blank lines
        while normalized_lines and normalized_lines[0] == "":
            normalized_lines.pop(0)
        while normalized_lines and normalized_lines[-1] == "":
            normalized_lines.pop()

        return "\n".join(normalized_lines)

    def get_master_nodes(self) -> List[str]:
        """Get list of master nodes using OpenShift JSON path"""
        try:
            cmd = [
                "oc", "get", "nodes", 
                "-l", "node-role.kubernetes.io/control-plane",
                "-o", "jsonpath={.items[*].metadata.name}"
            ]
            
            self.logger.info("Discovering master nodes...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                raise Exception(f"Failed to get master nodes: {result.stderr}")
                
            nodes = result.stdout.strip().split()
            self.logger.info(f"Found {len(nodes)} master nodes: {nodes}")
            return nodes
            
        except subprocess.TimeoutExpired:
            raise Exception("Timeout while getting master nodes")
        except Exception as e:
            self.logger.error(f"Error getting master nodes: {e}")
            raise
            
    def run_fio_on_node(self, node_name: str, test_name: str, run_uuid: Optional[str] = None) -> FioResult:
        """Run FIO test on specific node using oc debug with automatic cleanup"""
        test_config = self.fio_tests.get(test_name)
        if not test_config:
            raise ValueError(f"Unknown test: {test_name}")
            
        result = FioResult(
            test_name=test_name,
            timestamp=datetime.now().isoformat(),
            node_name=node_name,
            run_uuid=run_uuid
        )
        
        try:
            self.logger.info(f"Running {test_name} on node {node_name}")
            
            # Prepare oc debug command with cleanup
            debug_cmd = [
                "oc", "debug", f"node/{node_name}", "--", "chroot", "/host", "bash", "-c",
                f"{test_config['command']} && {test_config['cleanup']}"
            ]
            
            # Execute FIO test
            process = subprocess.run(
                debug_cmd,
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout
            )
            
            result.raw_output = process.stdout
            
            if process.returncode != 0:
                result.error = process.stderr
                self.logger.error(f"FIO test failed on {node_name}: {process.stderr}")
                # Attempt cleanup even if test failed
                self._cleanup_test_files_on_node(node_name, test_name)
                return result
                
            # Parse results
            self._parse_fio_output(result)
            self.logger.info(f"Successfully completed {test_name} on {node_name}")
            
        except subprocess.TimeoutExpired:
            result.error = "Test timeout (600s)"
            self.logger.error(f"FIO test timeout on {node_name}")
            # Cleanup after timeout
            self._cleanup_test_files_on_node(node_name, test_name)
        except Exception as e:
            result.error = str(e)
            self.logger.error(f"Error running FIO on {node_name}: {e}")
            
        return result

    def _parse_fio_output(self, result: FioResult) -> None:
        """Parse FIO output and extract metrics"""
        output = result.raw_output
        
        # Parse IOPS - Enhanced for 'k' suffix
        read_iops = self._extract_metric(output, r'read: IOPS=([0-9.]+)k?')
        write_iops = self._extract_metric(output, r'write: IOPS=([0-9.]+)k?')
        
        # Handle 'k' suffix for IOPS
        if read_iops and isinstance(read_iops, float):
            read_match = re.search(r'read: IOPS=([0-9.]+)k', output)
            if read_match:
                read_iops = float(read_match.group(1)) * 1000
        
        if write_iops and isinstance(write_iops, float):
            write_match = re.search(r'write: IOPS=([0-9.]+)k', output)
            if write_match:
                write_iops = float(write_match.group(1)) * 1000
        
        result.iops_read = read_iops
        result.iops_write = write_iops
        
        # Parse Bandwidth
        read_bw = self._extract_metric(output, r'read:.*?BW=([0-9.]+)MiB/s')
        write_bw = self._extract_metric(output, r'write:.*?BW=([0-9.]+)MiB/s')
        
        if not read_bw:
            read_bw = self._extract_metric(output, r'READ:.*?bw=([0-9.]+)MiB/s')
        if not write_bw:
            write_bw = self._extract_metric(output, r'WRITE:.*?bw=([0-9.]+)MiB/s')
            
        result.bw_read_mbs = read_bw
        result.bw_write_mbs = write_bw
        
        # Parse Latency
        clat_avg = self._extract_metric(output, r'clat \((?:usec|nsec)\): min=\d+, max=[\d.]+[kmg]?, avg=([0-9.]+)')
        
        if clat_avg:
            clat_match = re.search(r'clat \((usec|nsec)\):', output)
            if clat_match and clat_match.group(1) == 'nsec':
                clat_avg = clat_avg / 1000
        
        clat_p99 = self._extract_metric(output, r'99\.00th=\[\s*(\d+)\]')
        clat_p9999 = self._extract_metric(output, r'99\.99th=\[\s*(\d+)\]')
        
        if not clat_avg:
            clat_avg = self._extract_metric(output, r'clat.*?avg=([0-9.]+)')
            
        result.lat_clat_avg_us = clat_avg
        result.lat_clat_p99_us = clat_p99
        result.lat_clat_p9999_us = clat_p9999
        
        # Parse Sync latency
        sync_avg = self._extract_metric(output, r'sync \(usec\): min=\d+, max=\d+, avg=([0-9.]+)')
        sync_p99 = self._extract_metric(output, r'sync percentiles.*?99\.00th=\[\s*(\d+)\]')
        sync_p9999 = self._extract_metric(output, r'sync percentiles.*?99\.99th=\[\s*(\d+)\]')
        
        result.sync_avg_us = sync_avg
        result.sync_p99_us = sync_p99
        result.sync_p9999_us = sync_p9999
        
        # Parse CPU usage
        cpu_usr = self._extract_metric(output, r'cpu\s*:\s*usr=([0-9.]+)%')
        cpu_sys = self._extract_metric(output, r'cpu\s*:.*?sys=([0-9.]+)%')
        
        result.cpu_usr_pct = cpu_usr
        result.cpu_sys_pct = cpu_sys
        
        # Parse disk utilization
        disk_util = self._extract_metric(output, r'util=([0-9.]+)%')
        if not disk_util:
            disk_util = self._extract_metric(output, r'aggrutil=([0-9.]+)%')
        
        result.disk_util_pct = disk_util
        
        # Parse runtime
        runtime = self._extract_metric(output, r'run=(\d+)-\d+msec')
        if not runtime:
            runtime = self._extract_metric(output, r'run=(\d+)msec')
            
        result.runtime_ms = int(runtime) if runtime else None

    def _extract_metric(self, text: str, pattern: str) -> Optional[float]:
        """Extract numeric metric using regex pattern"""
        match = re.search(pattern, text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
        if match:
            try:
                value_str = match.group(1)
                if value_str.endswith('k'):
                    return float(value_str[:-1]) * 1000
                return float(value_str)
            except ValueError:
                return None
        return None

    def _cleanup_test_files_on_node(self, node_name: str, test_name: str) -> None:
        """Clean up test files on specific node after test completion or failure"""
        test_config = self.fio_tests.get(test_name)
        if not test_config or 'cleanup' not in test_config:
            return
            
        try:
            self.logger.info(f"Cleaning up test files for {test_name} on node {node_name}")
            
            cleanup_cmd = [
                "oc", "debug", f"node/{node_name}", "--", "chroot", "/host", "bash", "-c",
                test_config['cleanup']
            ]
            
            process = subprocess.run(
                cleanup_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if process.returncode == 0:
                self.logger.info(f"Successfully cleaned up test files for {test_name} on {node_name}")
            else:
                self.logger.warning(f"Cleanup command completed with warnings for {test_name} on {node_name}")
                
        except Exception as e:
            self.logger.warning(f"Failed to cleanup test files for {test_name} on {node_name}: {e}")

    def generate_summary_table(self, results: List[FioResult]) -> str:
        """Generate summary table"""
        summary = []
        
        for result in results:
            if result.error:
                summary.append(f"\n=== {result.test_name.upper()} Test Results ===")
                summary.append(f"Node: {result.node_name}")
                summary.append(f"ERROR: {result.error}")
                summary.append("-" * 50)
                continue
                
            summary.append(f"\n=== {result.test_name.upper()} Test Results ===")
            summary.append(f"Node: {result.node_name}")
            summary.append(f"Timestamp: {result.timestamp}")
            summary.append("-" * 50)
            
            metrics = [
                ("IOPS (Read)", result.iops_read, "Operations per second for read"),
                ("IOPS (Write)", result.iops_write, "Operations per second for write"),
                ("Bandwidth Read", f"{result.bw_read_mbs} MB/s" if result.bw_read_mbs else None, "Read throughput"),
                ("Bandwidth Write", f"{result.bw_write_mbs} MB/s" if result.bw_write_mbs else None, "Write throughput"),
                ("Completion Latency Avg", f"{result.lat_clat_avg_us} μs" if result.lat_clat_avg_us else None, "Average completion latency"),
                ("Completion Latency P99", f"{result.lat_clat_p99_us} μs" if result.lat_clat_p99_us else None, "99th percentile completion latency"),
                ("Sync Latency Avg", f"{result.sync_avg_us} μs" if result.sync_avg_us else None, "Average sync/fdatasync latency"),
                ("Sync Latency P99", f"{result.sync_p99_us} μs" if result.sync_p99_us else None, "99th percentile sync latency"),
                ("CPU User", f"{result.cpu_usr_pct}%" if result.cpu_usr_pct else None, "User CPU utilization"),
                ("CPU System", f"{result.cpu_sys_pct}%" if result.cpu_sys_pct else None, "System CPU utilization"),
                ("Disk Utilization", f"{result.disk_util_pct}%" if result.disk_util_pct else None, "Disk utilization percentage"),
                ("Runtime", f"{result.runtime_ms} ms" if result.runtime_ms else None, "Test duration"),
            ]
            
            summary.append(f"{'Metric':<25} {'Value':<15} {'Description'}")
            summary.append("-" * 70)
            
            for metric_name, value, description in metrics:
                if value is not None:
                    summary.append(f"{metric_name:<25} {str(value):<15} {description}")
                    
        return "\n".join(summary)
        
    def run_benchmark_suite(self, target_node: Optional[str] = None, 
                          specific_test: Optional[str] = None,
                          run_uuid: Optional[str] = None) -> List[FioResult]:
        """Run complete benchmark suite on master nodes"""
        master_nodes = self.get_master_nodes()
        if not master_nodes:
            raise Exception("No master nodes found")
            
        if target_node:
            if target_node not in master_nodes:
                raise Exception(f"Node {target_node} is not a master node")
            nodes_to_test = [target_node]
        else:
            nodes_to_test = [master_nodes[0]]
            
        results = []
        
        if specific_test:
            if specific_test not in self.fio_tests:
                raise Exception(f"Unknown test: {specific_test}")
            tests_to_run = [specific_test]
        else:
            tests_to_run = list(self.fio_tests.keys())
        
        for node in nodes_to_test:
            self.logger.info(f"Running benchmark suite on node: {node}")
            
            for test_name in tests_to_run:
                result = self.run_fio_on_node(node, test_name, run_uuid)
                results.append(result)
                time.sleep(5)
                
        return results

    def create_analysis_workflow(self) -> StateGraph:
        """Create LangGraph workflow for FIO analysis"""
        if not self.enable_llm:
            raise Exception("LLM analysis disabled. Pass enable_llm=True or --enable-llm")
            
        workflow = StateGraph(AgentState)
        
        def analyze_results(state: AgentState) -> AgentState:
            """Analyze FIO results using LLM with streaming"""
            load_dotenv()
            api_key = os.getenv("OPENAI_API_KEY")
            base_url = os.getenv("BASE_URL")        

            llm = ChatOpenAI(
                model="gemini-2.5-pro",
                base_url=base_url,
                api_key=api_key,
                temperature=0.1,
                streaming=True
            )
            
            results_text = self.generate_summary_table(state["fio_results"])
            
            system_msg = SystemMessage(content="""
            You are a storage performance expert analyzing FIO benchmark results from OpenShift master nodes.
            Analyze the provided FIO test results and provide insights on:

            1. Overall performance assessment
            2. Potential bottlenecks or issues
            3. Comparison with expected etcd performance requirements
            4. Recommendations for optimization
            5. Any concerning metrics or patterns

            For etcd performance requirements:
            - fsync latency P99 should be <10ms
            - Random write latency P99 should be <100us (iodepth=1)
            - Random write IOPS should be >15k (iodepth=1)
            - System CPU usage should be <30%
            - Disk utilization <60% for low queue depth

            Format your analysis clearly with sections and concise, actionable insights.
            Focus on practical implications for OpenShift cluster health and etcd performance.
            """)
            
            human_msg = HumanMessage(content=f"FIO Benchmark Results:\n\n{results_text}")
            
            # Stream the response
            full_response = ""
            print("\n" + "="*80)
            print("LLM ANALYSIS (Streaming)")
            print("="*80 + "\n")
            
            for chunk in llm.stream([system_msg, human_msg]):
                if hasattr(chunk, 'content') and chunk.content:
                    print(chunk.content, end='', flush=True)
                    full_response += chunk.content
            
            print("\n")  # Add newline after streaming completes
            
            state["analysis"] = full_response
            state["messages"].append(HumanMessage(content=full_response))
            
            return state

        workflow.add_node("analyze", analyze_results)
        workflow.set_entry_point("analyze")
        workflow.add_edge("analyze", END)
        
        return workflow.compile()

    def analyze_with_llm(self, results: List[FioResult]) -> str:
        """Analyze results using LangGraph workflow"""
        try:
            workflow = self.create_analysis_workflow()
            
            initial_state = {
                "messages": [],
                "fio_results": results,
                "analysis": ""
            }
            
            final_state = workflow.invoke(initial_state)
            return final_state["analysis"]
            
        except Exception as e:
            self.logger.error(f"Error in LLM analysis: {e}")
            return f"Analysis failed: {e}"

    def run_complete_analysis(self, target_node: Optional[str] = None, 
                            specific_test: Optional[str] = None,
                            save_results: bool = True,
                            save_to_db: bool = True) -> Dict:
        """Run complete FIO analysis workflow"""
        self.logger.info("Starting complete FIO benchmark analysis")
        
        run_uuid = str(uuid.uuid4())
        results = self.run_benchmark_suite(target_node, specific_test, run_uuid)
        
        db_uuids = []
        cluster_name = None
        infrastructure_type = None
        
        if save_to_db and self.enable_db and self.db:
            try:
                self.logger.info("Saving results to database...")
                cluster_name = self.db.get_cluster_name()
                infrastructure_type = self.db.get_infrastructure_type()
                db_uuids = self.db.save_results_batch(
                    [asdict(r) for r in results],
                    cluster_name=cluster_name,
                    infrastructure_type=infrastructure_type
                )
                self.logger.info(f"Saved {len(db_uuids)} results to database")
            except Exception as e:
                self.logger.error(f"Failed to save to database: {e}")
        
        summary = self.generate_summary_table(results)
        
        # LLM analysis with streaming (output happens in real-time)
        llm_analysis = ""
        if self.enable_llm:
            llm_analysis = self.analyze_with_llm(results)
            # Analysis is already sanitized and streamed to console
        
        validation_report = self.utils.validate_against_baselines(
            [asdict(r) for r in results]
        )
        
        report = {
            "timestamp": datetime.now().isoformat(),
            "run_uuid": run_uuid,
            "cluster_name": cluster_name,
            "infrastructure_type": infrastructure_type,
            "target_node": target_node,
            "specific_test": specific_test,
            "results": [asdict(r) for r in results],
            "summary": summary,
            "llm_analysis": llm_analysis,
            "validation_report": validation_report,
            "successful_tests": len([r for r in results if not r.error]),
            "failed_tests": len([r for r in results if r.error]),
            "db_uuids": db_uuids if save_to_db else []
        }
        
        if save_results:
            timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"fio_benchmark_report_{timestamp_str}.json"
            self.utils.save_results_to_file(report, filename, "json")
            
            html_filename = f"fio_benchmark_report_{timestamp_str}.html"
            self.utils.generate_test_report_html(
                [asdict(r) for r in results], 
                html_filename
            )
            
        return report

    def __del__(self):
        """Cleanup database connection"""
        if self.enable_db and self.db:
            self.db.close()


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='FIO Benchmark Analyzer for OpenShift')
    parser.add_argument('--node', help='Target specific master node')
    parser.add_argument('--test', help='Run specific test only', 
                    choices=['fsync_write', 'mixed_libaio_seq_read_1g_r70_w30', 'mixed_libaio_seq_read_256m_r70_w30', 
                            'mixed_libaio_seq_write_256m_r30_w70', 'mixed_libaio_seq_write_1g_r30_w70', 
                            'solo_libaio_seek_1g', 'solo_libaio_seek_256m', 'basic_fio_random_latency', 
                            'basic_fio_random_bandwidth', 'basic_fio_random_iops'])
    parser.add_argument('--enable-llm', action='store_true', help='Enable LLM analysis (reads OPENAI_API_KEY from .env)')
    parser.add_argument('--no-save', action='store_true', help='Do not save results to file')
    parser.add_argument('--no-db', action='store_true', help='Do not save results to database')
    parser.add_argument('--db-path', default='fio_benchmarks.duckdb', help='Path to DuckDB database file')
    
    # Comparison mode arguments
    parser.add_argument('--compare', action='store_true', help='Enter comparison mode')
    parser.add_argument('--cluster1', help='First cluster name for comparison')
    parser.add_argument('--cluster2', help='Second cluster name for comparison')
    parser.add_argument('--trend', help='Analyze trends for specific cluster')
    parser.add_argument('--list-clusters', action='store_true', help='List all clusters in database')
    
    args = parser.parse_args()
    
    # Comparison mode
    if args.compare or args.trend or args.list_clusters:
        from fio_benchmark_comparison import fioComparison
        
        comparison = fioComparison(db_path=args.db_path)
        
        try:
            if args.list_clusters:
                print(comparison.list_all_clusters_summary())
                
            elif args.trend:
                if not args.test:
                    print("Error: --test is required for trend analysis")
                    return 1
                print(comparison.generate_trend_report(args.trend, args.test))
                
            elif args.cluster1 and args.cluster2:
                print(comparison.generate_comparison_report(
                    args.cluster1, args.cluster2, args.test
                ))
            else:
                print("Error: --cluster1 and --cluster2 required for comparison")
                print("Or use --trend CLUSTER_NAME --test TEST_NAME for trend analysis")
                print("Or use --list-clusters to see all clusters")
                return 1
                
        except Exception as e:
            print(f"Comparison error: {e}")
            return 1
        finally:
            comparison.close()
            
        return 0
    
    # Normal benchmark mode
    analyzer = fioBenchmarkAnalyzer(
        enable_llm=args.enable_llm,
        db_path=args.db_path,
        enable_db=not args.no_db
    )
    
    try:
        report = analyzer.run_complete_analysis(
            target_node=args.node,
            specific_test=args.test,
            save_results=not args.no_save,
            save_to_db=not args.no_db
        )
        
        print("\n" + "="*80)
        print("FIO BENCHMARK ANALYSIS REPORT")
        print("="*80)
        
        if report.get("cluster_name"):
            print(f"Cluster: {report['cluster_name']}")
        if report.get("infrastructure_type"):
            print(f"Infrastructure: {report['infrastructure_type']}")
        if report.get("run_uuid"):
            print(f"Run UUID: {report['run_uuid']}")
            
        print(report["summary"])
        
        if report["llm_analysis"]:
            print("\n" + "="*80)
            print("LLM ANALYSIS")
            print("="*80)
            print(report["llm_analysis"])
            
        if report["validation_report"]:
            from fio_benchmark_utils import fioAnalyzerUtils
            utils = fioAnalyzerUtils()
            formatted_validation = utils.format_validation_report(report["validation_report"])
            print("\n" + formatted_validation)
            
        print(f"\nCompleted: {report['successful_tests']} successful, {report['failed_tests']} failed tests")
        
        if report.get("db_uuids") and len(report["db_uuids"]) > 0:
            print(f"Saved {len(report['db_uuids'])} results to database")
        
    except Exception as e:
        print(f"Error: {e}")
        return 1
        
    return 0


if __name__ == "__main__":
    exit(main())