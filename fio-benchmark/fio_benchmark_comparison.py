#!/usr/bin/env python3
"""
FIO Benchmark Comparison Module
Compares FIO benchmark results across different clusters and time periods
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import pandas as pd
from fio_benchmark_db import fioBenchmarkDB


class fioComparison:
    """Compare FIO benchmark results across clusters and time periods"""
    
    def __init__(self, db_path: str = "fio_benchmarks.duckdb"):
        self.logger = self._setup_logging()
        self.db = fioBenchmarkDB(db_path)
        
    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger(f"{__name__}.comparison")
        
    def compare_clusters(self, cluster1: str, cluster2: str, 
                        test_name: Optional[str] = None) -> Dict[str, Any]:
        """Compare latest results between two clusters"""
        self.logger.info(f"Comparing clusters: {cluster1} vs {cluster2}")
        
        # Get latest results for both clusters
        results1 = self.db.get_latest_results_by_cluster(cluster1)
        results2 = self.db.get_latest_results_by_cluster(cluster2)
        
        if not results1:
            raise ValueError(f"No results found for cluster: {cluster1}")
        if not results2:
            raise ValueError(f"No results found for cluster: {cluster2}")
            
        # Filter by test name if specified
        if test_name:
            results1 = [r for r in results1 if r['test_name'] == test_name]
            results2 = [r for r in results2 if r['test_name'] == test_name]
            
        # Build comparison
        comparison = {
            "cluster1": {
                "name": cluster1,
                "infrastructure": results1[0]['infrastructure_type'] if results1 else "Unknown",
                "results": results1
            },
            "cluster2": {
                "name": cluster2,
                "infrastructure": results2[0]['infrastructure_type'] if results2 else "Unknown",
                "results": results2
            },
            "comparisons": []
        }
        
        # Compare each test
        tests1 = {r['test_name']: r for r in results1}
        tests2 = {r['test_name']: r for r in results2}
        
        common_tests = set(tests1.keys()) & set(tests2.keys())
        
        for test in common_tests:
            test_comparison = self._compare_test_results(
                tests1[test], tests2[test], cluster1, cluster2
            )
            comparison["comparisons"].append(test_comparison)
            
        return comparison
        
    def _compare_test_results(self, result1: Dict, result2: Dict,
                            cluster1_name: str, cluster2_name: str) -> Dict[str, Any]:
        """Compare two test results and calculate differences"""
        metrics = [
            ('iops_read', 'IOPS Read', 'ops/sec', 'higher_better'),
            ('iops_write', 'IOPS Write', 'ops/sec', 'higher_better'),
            ('bw_read_mbs', 'Bandwidth Read', 'MB/s', 'higher_better'),
            ('bw_write_mbs', 'Bandwidth Write', 'MB/s', 'higher_better'),
            ('lat_clat_avg_us', 'Latency Avg', 'μs', 'lower_better'),
            ('lat_clat_p99_us', 'Latency P99', 'μs', 'lower_better'),
            ('sync_avg_us', 'Sync Latency Avg', 'μs', 'lower_better'),
            ('sync_p99_us', 'Sync Latency P99', 'μs', 'lower_better'),
            ('cpu_sys_pct', 'CPU System', '%', 'lower_better'),
            ('disk_util_pct', 'Disk Utilization', '%', 'lower_better')
        ]
        
        comparison = {
            "test_name": result1['test_name'],
            "cluster1_name": cluster1_name,
            "cluster2_name": cluster2_name,
            "metrics": []
        }
        
        for metric_key, metric_name, unit, direction in metrics:
            val1 = result1.get(metric_key)
            val2 = result2.get(metric_key)
            
            # Skip if both values are None/NaN
            if (val1 is None or (isinstance(val1, float) and val1 != val1)) and \
            (val2 is None or (isinstance(val2, float) and val2 != val2)):
                continue
                
            # Skip if either value is None/NaN (can't compare)
            if val1 is None or (isinstance(val1, float) and val1 != val1) or \
            val2 is None or (isinstance(val2, float) and val2 != val2):
                continue
            
            diff = val2 - val1
            pct_change = (diff / val1 * 100) if val1 != 0 else 0
            
            # Determine if change is improvement or degradation
            if direction == 'higher_better':
                status = 'improved' if diff > 0 else 'degraded' if diff < 0 else 'same'
            else:  # lower_better
                status = 'improved' if diff < 0 else 'degraded' if diff > 0 else 'same'
                
            comparison["metrics"].append({
                "name": metric_name,
                "key": metric_key,
                "unit": unit,
                "cluster1_value": val1,
                "cluster2_value": val2,
                "difference": diff,
                "percent_change": pct_change,
                "status": status,
                "direction": direction
            })
                
        return comparison

    def compare_cluster_over_time(self, cluster_name: str, 
                                 test_name: str,
                                 limit: int = 10) -> Dict[str, Any]:
        """Compare results for same cluster over time"""
        self.logger.info(f"Analyzing trends for cluster {cluster_name}, test {test_name}")
        
        results = self.db.get_results_by_cluster(cluster_name, test_name)
        
        if not results:
            raise ValueError(f"No results found for cluster {cluster_name}, test {test_name}")
            
        # Limit to most recent results
        results = results[:limit]
        
        if len(results) < 2:
            return {
                "cluster_name": cluster_name,
                "test_name": test_name,
                "message": "Insufficient data for trend analysis (need at least 2 results)"
            }
            
        # Convert to DataFrame for easier analysis
        df = pd.DataFrame(results)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp')
        
        # Calculate trends
        metrics = ['iops_read', 'iops_write', 'bw_read_mbs', 'bw_write_mbs',
                  'lat_clat_avg_us', 'lat_clat_p99_us', 'cpu_sys_pct', 'disk_util_pct']
        
        trends = {
            "cluster_name": cluster_name,
            "test_name": test_name,
            "total_runs": len(df),
            "first_run": df['timestamp'].iloc[0].isoformat(),
            "latest_run": df['timestamp'].iloc[-1].isoformat(),
            "metric_trends": []
        }
        
        for metric in metrics:
            if metric in df.columns and df[metric].notna().sum() >= 2:
                values = df[metric].dropna()
                
                if len(values) >= 2:
                    first_val = values.iloc[0]
                    latest_val = values.iloc[-1]
                    avg_val = values.mean()
                    min_val = values.min()
                    max_val = values.max()
                    
                    change = latest_val - first_val
                    pct_change = (change / first_val * 100) if first_val != 0 else 0
                    
                    trends["metric_trends"].append({
                        "metric": metric,
                        "first_value": float(first_val),
                        "latest_value": float(latest_val),
                        "average": float(avg_val),
                        "min": float(min_val),
                        "max": float(max_val),
                        "change": float(change),
                        "percent_change": float(pct_change)
                    })
                    
        return trends
        
    def generate_comparison_report(self, cluster1: str, cluster2: str,
                                  test_name: Optional[str] = None) -> str:
        """Generate human-readable comparison report"""
        comparison = self.compare_clusters(cluster1, cluster2, test_name)
        
        lines = []
        lines.append("=" * 80)
        lines.append("FIO BENCHMARK COMPARISON REPORT")
        lines.append("=" * 80)
        lines.append(f"\nCluster 1: {comparison['cluster1']['name']}")
        lines.append(f"Infrastructure: {comparison['cluster1']['infrastructure']}")
        lines.append(f"\nCluster 2: {comparison['cluster2']['name']}")
        lines.append(f"Infrastructure: {comparison['cluster2']['infrastructure']}")
        lines.append("\n" + "=" * 80)
        
        for test_comp in comparison["comparisons"]:
            lines.append(f"\nTest: {test_comp['test_name'].upper()}")
            lines.append("-" * 80)
            
            # Create table header
            lines.append(f"{'Metric':<25} {'Cluster 1':<15} {'Cluster 2':<15} {'Change':<15} {'Status':<10}")
            lines.append("-" * 80)
            
            for metric in test_comp["metrics"]:
                val1 = f"{metric['cluster1_value']:.2f}"
                val2 = f"{metric['cluster2_value']:.2f}"
                
                if metric['percent_change'] >= 0:
                    change = f"+{metric['percent_change']:.1f}%"
                else:
                    change = f"{metric['percent_change']:.1f}%"
                    
                status_symbol = {
                    'improved': '✓ Better',
                    'degraded': '✗ Worse',
                    'same': '= Same'
                }.get(metric['status'], metric['status'])
                
                lines.append(f"{metric['name']:<25} {val1:<15} {val2:<15} {change:<15} {status_symbol:<10}")
                
            lines.append("")
            
        # Summary statistics
        lines.append("\n" + "=" * 80)
        lines.append("SUMMARY")
        lines.append("=" * 80)
        
        total_metrics = sum(len(tc["metrics"]) for tc in comparison["comparisons"])
        improved = sum(sum(1 for m in tc["metrics"] if m['status'] == 'improved') 
                      for tc in comparison["comparisons"])
        degraded = sum(sum(1 for m in tc["metrics"] if m['status'] == 'degraded') 
                      for tc in comparison["comparisons"])
        same = total_metrics - improved - degraded
        
        lines.append(f"Total Metrics Compared: {total_metrics}")
        lines.append(f"Improved: {improved} ({improved/total_metrics*100:.1f}%)")
        lines.append(f"Degraded: {degraded} ({degraded/total_metrics*100:.1f}%)")
        lines.append(f"Same: {same} ({same/total_metrics*100:.1f}%)")
        
        return "\n".join(lines)
        
    def generate_trend_report(self, cluster_name: str, test_name: str,
                            limit: int = 10) -> str:
        """Generate human-readable trend report"""
        trends = self.compare_cluster_over_time(cluster_name, test_name, limit)
        
        if "message" in trends:
            return trends["message"]
            
        lines = []
        lines.append("=" * 80)
        lines.append("FIO BENCHMARK TREND ANALYSIS")
        lines.append("=" * 80)
        lines.append(f"\nCluster: {trends['cluster_name']}")
        lines.append(f"Test: {trends['test_name']}")
        lines.append(f"Total Runs Analyzed: {trends['total_runs']}")
        lines.append(f"First Run: {trends['first_run']}")
        lines.append(f"Latest Run: {trends['latest_run']}")
        lines.append("\n" + "=" * 80)
        
        # Metrics table
        lines.append(f"{'Metric':<20} {'First':<12} {'Latest':<12} {'Average':<12} {'Change':<12} {'Trend':<10}")
        lines.append("-" * 80)
        
        for metric_trend in trends["metric_trends"]:
            metric_name = metric_trend['metric'].replace('_', ' ').title()
            first = f"{metric_trend['first_value']:.2f}"
            latest = f"{metric_trend['latest_value']:.2f}"
            avg = f"{metric_trend['average']:.2f}"
            
            pct_change = metric_trend['percent_change']
            if pct_change >= 0:
                change = f"+{pct_change:.1f}%"
            else:
                change = f"{pct_change:.1f}%"
                
            if abs(pct_change) < 5:
                trend = "Stable"
            elif pct_change > 0:
                trend = "↑ Up"
            else:
                trend = "↓ Down"
                
            lines.append(f"{metric_name:<20} {first:<12} {latest:<12} {avg:<12} {change:<12} {trend:<10}")
            
        return "\n".join(lines)
        
    def list_all_clusters_summary(self) -> str:
        """Generate summary of all clusters in database"""
        clusters = self.db.get_all_clusters()
        
        if not clusters:
            return "No clusters found in database"
            
        lines = []
        lines.append("=" * 80)
        lines.append("ALL CLUSTERS SUMMARY")
        lines.append("=" * 80)
        lines.append(f"\nTotal Clusters: {len(clusters)}\n")
        
        for cluster in clusters:
            info = self.db.get_cluster_info(cluster)
            if info:
                lines.append(f"Cluster: {info['cluster_name']}")
                lines.append(f"  Infrastructure: {info['infrastructure_type']}")
                lines.append(f"  Total Results: {info['total_results']}")
                lines.append(f"  Unique Tests: {info['unique_tests']}")
                lines.append(f"  First Test: {info['first_test']}")
                lines.append(f"  Latest Test: {info['latest_test']}")
                lines.append("")
                
        return "\n".join(lines)
        
    def close(self) -> None:
        """Close database connection"""
        self.db.close()

