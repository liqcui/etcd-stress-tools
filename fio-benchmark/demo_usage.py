#!/usr/bin/env python3
"""
Example usage script for the new FIO test functionality
Demonstrates how to use the mixed_libaio_seq_write_256m_r30_w70 test
and other enhanced features of the FIO benchmark analyzer
"""

import sys
import os
from fio_benchmark_analyzer import fioBenchmarkAnalyzer
from fio_benchmark_utils import fioAnalyzerUtils

def example_run_new_test():
    """Example 1: Run only the new write-heavy test"""
    print("="*60)
    print("EXAMPLE 1: Running New Write-Heavy Test")
    print("="*60)
    
    # Initialize analyzer (LLM enabled reads OPENAI_API_KEY from .env)
    analyzer = fioBenchmarkAnalyzer(enable_llm=True)
    
    try:
        # Run only the new write-heavy test
        print("Running mixed_libaio_seq_write_256m_r30_w70 test...")
        report = analyzer.run_complete_analysis(
            target_node=None,  # Will use first master node
            specific_test="mixed_libaio_seq_write_256m_r30_w70",
            save_results=True
        )
        
        print(f"✓ Test completed! Results: {report['successful_tests']} successful, {report['failed_tests']} failed")
        
        # Print key metrics from the test
        for result in report['results']:
            if not result.get('error'):
                print(f"\nKey Metrics for {result['test_name']}:")
                print(f"  Read IOPS:        {result.get('iops_read', 'N/A')}")
                print(f"  Write IOPS:       {result.get('iops_write', 'N/A')}")
                print(f"  Read Bandwidth:   {result.get('bw_read_mbs', 'N/A')} MB/s")
                print(f"  Write Bandwidth:  {result.get('bw_write_mbs', 'N/A')} MB/s")
                print(f"  CPU System:       {result.get('cpu_sys_pct', 'N/A')}%")
                print(f"  Disk Utilization: {result.get('disk_util_pct', 'N/A')}%")
                print(f"  Runtime:          {result.get('runtime_ms', 'N/A')} ms")
        
        return report
        
    except Exception as e:
        print(f"Error running test: {e}")
        return None

def example_cleanup_files():
    """Example 2: Demonstrate cleanup functionality"""
    print("\n" + "="*60)
    print("EXAMPLE 2: Cleanup Test Files")
    print("="*60)
    
    utils = fioAnalyzerUtils()
    
    # Method 1: Clean up specific test files
    print("1. Cleaning up files for specific test...")
    utils.cleanup_fio_test_files("mixed_libaio_seq_write_256m_r30_w70")
    
    # Method 2: Auto-cleanup all FIO files
    print("\n2. Auto-cleaning all FIO test files...")
    utils.auto_cleanup_fio_files()
    
    # Method 3: Clean up specific file list
    print("\n3. Cleaning up custom file list...")
    custom_files = [
        "/var/lib/etcd/fio4seqwrite256m",
        "/tmp/custom_fio_test"
    ]
    utils.cleanup_test_files(custom_files)

def example_performance_analysis(report_data):
    """Example 3: Advanced performance analysis"""
    if not report_data:
        print("\nSkipping performance analysis - no test data available")
        return
        
    print("\n" + "="*60)
    print("EXAMPLE 3: Performance Analysis")
    print("="*60)
    
    utils = fioAnalyzerUtils()
    
    # Generate performance summary
    perf_summary = utils.generate_performance_summary(report_data['results'])
    
    print("Performance Insights:")
    insights = perf_summary['performance_insights']
    for key, value in insights.items():
        print(f"  {key.replace('_', ' ').title()}: {value}")
    
    # Show write-heavy workload specific analysis
    if 'write_heavy_workload' in perf_summary.get('test_analysis', {}):
        print("\nWrite-Heavy Workload Analysis:")
        write_analysis = perf_summary['test_analysis']['write_heavy_workload']
        for key, value in write_analysis.items():
            unit = "%" if 'cpu' in key or 'utilization' in key else ("MB/s" if 'bandwidth' in key else "")
            print(f"  {key.replace('_', ' ').title()}: {value} {unit}")
    
    # Show recommendations
    if perf_summary['recommendations']:
        print("\nRecommendations:")
        for i, rec in enumerate(perf_summary['recommendations'], 1):
            print(f"  {i}. {rec}")

def example_validation_report(report_data):
    """Example 4: Validation against baselines"""
    if not report_data:
        print("\nSkipping validation - no test data available")
        return
        
    print("\n" + "="*60)
    print("EXAMPLE 4: Baseline Validation")
    print("="*60)
    
    validation = report_data['validation_report']
    summary = validation['summary']
    
    print("Validation Summary:")
    print(f"  ✓ Passed:   {summary['passed']}")
    print(f"  ⚠ Warnings: {summary['warnings']}")  
    print(f"  ✗ Failed:   {summary['failed']}")
    
    # Show detailed validation results
    if validation.get('validations'):
        print("\nDetailed Validation Results:")
        for test_val in validation['validations']:
            print(f"\nTest: {test_val['test_name']}")
            for check in test_val['checks']:
                status_symbol = "✓" if check['status'] == 'passed' else ("⚠" if check['status'] == 'warning' else "✗")
                print(f"  {status_symbol} {check['metric']}: {check['actual']} (threshold: {check['threshold']})")

def example_custom_test_config():
    """Example 5: Create custom test configurations"""
    print("\n" + "="*60)
    print("EXAMPLE 5: Custom Test Configuration")
    print("="*60)
    
    utils = fioAnalyzerUtils()
    
    # Create custom configuration for the new test
    custom_config = utils.create_extended_fio_test(
        "mixed_libaio_seq_write_256m_r30_w70",
        size="512M",        # Larger test size
        runtime=600,        # 10 minutes runtime
        ramp_time=15,       # Longer ramp time
        iodepth=2          # Higher iodepth
    )
    
    print("Custom Test Configuration:")
    for key, value in custom_config.items():
        if key != "description":
            print(f"  {key}: {value}")
    
    print(f"\nDescription: {custom_config.get('description', 'N/A')}")
    
    # Show how to create FIO command from config
    fio_command = utils.create_fio_command(custom_config)
    print(f"\nGenerated FIO Command:")
    print(f"  {fio_command}")

def example_compare_results():
    """Example 6: Compare results with baselines"""
    print("\n" + "="*60)
    print("EXAMPLE 6: Result Comparison (Simulated)")
    print("="*60)
    
    utils = fioAnalyzerUtils()
    
    # Show baseline expectations for the new test
    baselines = utils.get_etcd_performance_baselines()
    
    if 'mixed_libaio_seq_write_256m_r30_w70' in baselines:
        baseline = baselines['mixed_libaio_seq_write_256m_r30_w70']
        print("Expected Performance Baselines:")
        for metric, threshold in baseline.items():
            if metric != 'description':
                unit = "μs" if 'us' in metric else ("%" if 'pct' in metric else ("MB/s" if 'mbs' in metric else ""))
                direction = "maximum" if metric.endswith('_max') else "minimum"
                print(f"  {metric.replace('_', ' ').title()}: {threshold} {unit} ({direction})")
        
        print(f"\nTest Purpose: {baseline.get('description', 'N/A')}")

def example_run_all_tests():
    """Example 7: Run all available tests"""
    print("\n" + "="*60)
    print("EXAMPLE 7: Run All Available Tests")
    print("="*60)
    
    analyzer = fioBenchmarkAnalyzer()
    
    print("Available tests:")
    for test_name, config in analyzer.fio_tests.items():
        print(f"  - {test_name}")
    
    try:
        print("\nRunning all tests (this may take a while)...")
        report = analyzer.run_complete_analysis(
            target_node=None,
            specific_test=None,  # Run all tests
            save_results=True
        )
        
        print(f"\nAll tests completed!")
        print(f"Results: {report['successful_tests']} successful, {report['failed_tests']} failed")
        
        # Show summary of each test
        print("\nTest Results Summary:")
        for result in report['results']:
            status = "✗ FAILED" if result.get('error') else "✓ SUCCESS"
            print(f"  {result['test_name']}: {status}")
            if result.get('error'):
                print(f"    Error: {result['error']}")
        
        return report
        
    except Exception as e:
        print(f"Error running all tests: {e}")
        return None

def main():
    """Main function demonstrating all examples"""
    print("FIO Benchmark Analyzer - Complete Examples")
    print("="*80)
    print("This script demonstrates the new mixed_libaio_seq_write_256m_r30_w70 test")
    print("and other enhanced features of the FIO benchmark analyzer.")
    print("="*80)
    
    # Check if we should run actual tests or just show examples
    run_actual_tests = len(sys.argv) > 1 and sys.argv[1] == "--run-tests"
    
    if not run_actual_tests:
        print("\nNOTE: Running in demo mode. Use '--run-tests' to execute actual FIO tests.")
        print("This demo shows the code structure and expected outputs without running tests.\n")
    
    report_data = None
    
    # Example 1: Run the new test
    if run_actual_tests:
        report_data = example_run_new_test()
    else:
        print("="*60)
        print("EXAMPLE 1: Running New Write-Heavy Test (Demo Mode)")
        print("="*60)
        print("This would run: mixed_libaio_seq_write_256m_r30_w70")
        print("Expected output: IOPS, bandwidth, latency, and utilization metrics")
        print("Test characteristics: 30% read, 70% write, mixed block sizes (2k/4k)")
    
    # Example 2: Cleanup
    example_cleanup_files()
    
    # Example 3: Performance analysis
    example_performance_analysis(report_data)
    
    # Example 4: Validation
    example_validation_report(report_data)
    
    # Example 5: Custom configuration
    example_custom_test_config()
    
    # Example 6: Compare results
    example_compare_results()
    
    # Example 7: Run all tests (only if requested)
    if run_actual_tests:
        all_tests_report = example_run_all_tests()
        if all_tests_report:
            print(f"\nFinal Summary:")
            print(f"Total tests executed: {len(all_tests_report['results'])}")
            print(f"Successful: {all_tests_report['successful_tests']}")
            print(f"Failed: {all_tests_report['failed_tests']}")
    
    print("\n" + "="*80)
    print("Examples completed! Check generated files:")
    print("- fio_benchmark_report_*.json (detailed results)")
    print("- fio_benchmark_report_*.html (HTML report)")
    print("="*80)

def show_usage():
    """Show usage information"""
    print("Usage:")
    print(f"  python {sys.argv[0]}           # Run in demo mode (no actual tests)")
    print(f"  python {sys.argv[0]} --run-tests    # Execute actual FIO tests")
    print()
    print("Demo mode shows code structure and expected outputs.")
    print("--run-tests mode executes actual FIO benchmarks (requires OpenShift access).")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help']:
        show_usage()
        sys.exit(0)
        
    try:
        exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)