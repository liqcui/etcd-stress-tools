#!/usr/bin/env python3
"""
FIO Benchmark Database Storage Module
Handles storage and retrieval of FIO benchmark results in DuckDB
"""

import duckdb
import uuid
import logging
import subprocess
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path


class fioBenchmarkDB:
    """Database handler for FIO benchmark results using DuckDB"""
    
    def __init__(self, db_path: str = "fio_benchmarks.duckdb"):
        self.db_path = db_path
        self.logger = self._setup_logging()
        self.conn = None
        self._initialize_database()
        
    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger(f"{__name__}.db")
        
    def _initialize_database(self) -> None:
        """Initialize DuckDB connection and create tables if not exist"""
        try:
            self.conn = duckdb.connect(self.db_path)
            self._create_tables()
            self.logger.info(f"Database initialized: {self.db_path}")
        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}")
            raise
            
    def _create_tables(self) -> None:
        """Create database tables for storing FIO results"""
        
        # Main results table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS fio_benchmark_results (
                uuid VARCHAR PRIMARY KEY,
                cluster_name VARCHAR NOT NULL,
                infrastructure_type VARCHAR NOT NULL,
                node_name VARCHAR NOT NULL,
                test_name VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                iops_read DOUBLE,
                iops_write DOUBLE,
                bw_read_mbs DOUBLE,
                bw_write_mbs DOUBLE,
                lat_clat_avg_us DOUBLE,
                lat_clat_p99_us DOUBLE,
                lat_clat_p9999_us DOUBLE,
                sync_avg_us DOUBLE,
                sync_p99_us DOUBLE,
                sync_p9999_us DOUBLE,
                cpu_usr_pct DOUBLE,
                cpu_sys_pct DOUBLE,
                disk_util_pct DOUBLE,
                runtime_ms INTEGER,
                error VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migration: ensure run_uuid column exists (added after initial releases)
        try:
            cols_df = self.conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'fio_benchmark_results'"
            ).fetchdf()
            existing_cols = set(c.lower() for c in cols_df['column_name'].tolist())
            if 'run_uuid' not in existing_cols:
                self.conn.execute("ALTER TABLE fio_benchmark_results ADD COLUMN run_uuid VARCHAR")
                self.logger.info("Added missing column 'run_uuid' to fio_benchmark_results")
        except Exception as e:
            self.logger.warning(f"Failed to verify/add run_uuid column: {e}")
        
        # Index for faster queries
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cluster_test 
            ON fio_benchmark_results(cluster_name, test_name, timestamp)
        """)
        
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_infrastructure 
            ON fio_benchmark_results(infrastructure_type, test_name)
        """)
        
        self.logger.info("Database tables created/verified")
        
    def get_infrastructure_type(self) -> str:
        """Get infrastructure type from OpenShift cluster"""
        try:
            cmd = [
                "oc", "get", "infrastructure", "cluster",
                "-o", "jsonpath={.spec.platformSpec.type}"
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
            
            if result.returncode == 0 and result.stdout.strip():
                infra_type = result.stdout.strip()
                self.logger.info(f"Infrastructure type: {infra_type}")
                return infra_type
            else:
                self.logger.warning("Failed to get infrastructure type, using 'Unknown'")
                return "Unknown"
                
        except Exception as e:
            self.logger.error(f"Error getting infrastructure type: {e}")
            return "Unknown"
            
    def get_cluster_name(self) -> str:
        """Get OpenShift cluster name"""
        try:
            # Try to get cluster name from infrastructure object
            cmd = [
                "oc", "get", "infrastructure", "cluster",
                "-o", "jsonpath={.status.infrastructureName}"
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
            
            if result.returncode == 0 and result.stdout.strip():
                cluster_name = result.stdout.strip()
                self.logger.info(f"Cluster name: {cluster_name}")
                return cluster_name
                
            # Fallback: try to get from clusterversion
            cmd = [
                "oc", "get", "clusterversion", "version",
                "-o", "jsonpath={.spec.clusterID}"
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
            
            if result.returncode == 0 and result.stdout.strip():
                cluster_id = result.stdout.strip()
                self.logger.info(f"Cluster ID: {cluster_id}")
                return cluster_id
                
            self.logger.warning("Failed to get cluster name, using 'unknown-cluster'")
            return "unknown-cluster"
            
        except Exception as e:
            self.logger.error(f"Error getting cluster name: {e}")
            return "unknown-cluster"
            
    def save_result(self, result: Dict[str, Any], 
                cluster_name: Optional[str] = None,
                infrastructure_type: Optional[str] = None) -> str:
        """Save a single FIO result to database"""
        try:
            # Generate UUID for this result (unique per result)
            result_uuid = str(uuid.uuid4())
            
            # Get cluster info if not provided
            if cluster_name is None:
                cluster_name = self.get_cluster_name()
            if infrastructure_type is None:
                infrastructure_type = self.get_infrastructure_type()
                
            # Parse timestamp
            timestamp_str = result.get('timestamp', datetime.now().isoformat())
            if isinstance(timestamp_str, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                except:
                    timestamp = datetime.now()
            else:
                timestamp = timestamp_str
            
            # Get run_uuid from result - this groups all tests from one run
            run_uuid = result.get('run_uuid')
                
            # Insert into database
            self.conn.execute("""
                INSERT INTO fio_benchmark_results (
                    uuid,
                    run_uuid,
                    cluster_name,
                    infrastructure_type,
                    node_name,
                    test_name,
                    timestamp,
                    iops_read,
                    iops_write,
                    bw_read_mbs,
                    bw_write_mbs,
                    lat_clat_avg_us,
                    lat_clat_p99_us,
                    lat_clat_p9999_us,
                    sync_avg_us,
                    sync_p99_us,
                    sync_p9999_us,
                    cpu_usr_pct,
                    cpu_sys_pct,
                    disk_util_pct,
                    runtime_ms,
                    error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result_uuid,
                run_uuid,
                cluster_name,
                infrastructure_type,
                result.get('node_name', 'unknown'),
                result.get('test_name', 'unknown'),
                timestamp,
                result.get('iops_read'),
                result.get('iops_write'),
                result.get('bw_read_mbs'),
                result.get('bw_write_mbs'),
                result.get('lat_clat_avg_us'),
                result.get('lat_clat_p99_us'),
                result.get('lat_clat_p9999_us'),
                result.get('sync_avg_us'),
                result.get('sync_p99_us'),
                result.get('sync_p9999_us'),
                result.get('cpu_usr_pct'),
                result.get('cpu_sys_pct'),
                result.get('disk_util_pct'),
                result.get('runtime_ms'),
                result.get('error')
            ))
            
            self.logger.info(f"Saved result {result_uuid} for test {result.get('test_name')} on cluster {cluster_name} (run_uuid: {run_uuid})")
            return result_uuid
            
        except Exception as e:
            self.logger.error(f"Failed to save result: {e}")
            raise

    def save_results_batch(self, results: List[Dict[str, Any]], 
                          cluster_name: Optional[str] = None,
                          infrastructure_type: Optional[str] = None) -> List[str]:
        """Save multiple FIO results to database"""
        uuids = []
        
        # Get cluster info once for all results
        if cluster_name is None:
            cluster_name = self.get_cluster_name()
        if infrastructure_type is None:
            infrastructure_type = self.get_infrastructure_type()
            
        for result in results:
            try:
                result_uuid = self.save_result(result, cluster_name, infrastructure_type)
                uuids.append(result_uuid)
            except Exception as e:
                self.logger.error(f"Failed to save result: {e}")
                continue
                
        self.logger.info(f"Saved {len(uuids)} results to database")
        return uuids
        
    def get_results_by_cluster(self, cluster_name: str, 
                              test_name: Optional[str] = None) -> List[Dict]:
        """Get all results for a specific cluster"""
        try:
            if test_name:
                query = """
                    SELECT * FROM fio_benchmark_results 
                    WHERE cluster_name = ? AND test_name = ?
                    ORDER BY timestamp DESC
                """
                result = self.conn.execute(query, (cluster_name, test_name)).fetchdf()
            else:
                query = """
                    SELECT * FROM fio_benchmark_results 
                    WHERE cluster_name = ?
                    ORDER BY timestamp DESC
                """
                result = self.conn.execute(query, (cluster_name,)).fetchdf()
                
            return result.to_dict('records')
            
        except Exception as e:
            self.logger.error(f"Failed to get results: {e}")
            return []
            
    def get_latest_results_by_cluster(self, cluster_name: str) -> List[Dict]:
        """Get latest results for each test type for a cluster"""
        try:
            query = """
                WITH ranked_results AS (
                    SELECT *,
                           ROW_NUMBER() OVER (PARTITION BY test_name ORDER BY timestamp DESC) as rn
                    FROM fio_benchmark_results
                    WHERE cluster_name = ? AND error IS NULL
                )
                SELECT * FROM ranked_results WHERE rn = 1
                ORDER BY test_name
            """
            result = self.conn.execute(query, (cluster_name,)).fetchdf()
            return result.to_dict('records')
            
        except Exception as e:
            self.logger.error(f"Failed to get latest results: {e}")
            return []
            
    def get_all_clusters(self) -> List[str]:
        """Get list of all clusters in database"""
        try:
            query = "SELECT DISTINCT cluster_name FROM fio_benchmark_results ORDER BY cluster_name"
            result = self.conn.execute(query).fetchdf()
            return result['cluster_name'].tolist()
            
        except Exception as e:
            self.logger.error(f"Failed to get clusters: {e}")
            return []
            
    def get_cluster_info(self, cluster_name: str) -> Optional[Dict]:
        """Get cluster information"""
        try:
            query = """
                SELECT 
                    cluster_name,
                    infrastructure_type,
                    COUNT(*) as total_results,
                    COUNT(DISTINCT test_name) as unique_tests,
                    MIN(timestamp) as first_test,
                    MAX(timestamp) as latest_test
                FROM fio_benchmark_results
                WHERE cluster_name = ?
                GROUP BY cluster_name, infrastructure_type
            """
            result = self.conn.execute(query, (cluster_name,)).fetchdf()
            
            if len(result) > 0:
                return result.to_dict('records')[0]
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to get cluster info: {e}")
            return None
            
    def close(self) -> None:
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.logger.info("Database connection closed")