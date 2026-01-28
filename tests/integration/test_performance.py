"""
性能测试
测试GitMusic在各种负载下的性能表现
"""
import pytest
import tempfile
import time
import psutil
import threading
from pathlib import Path
from unittest.mock import Mock, patch
import json
import os

# 导入被测试的模块
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.context import Context
from libgitmusic.commands.publish import publish_logic, execute_publish
from libgitmusic.commands.sync import sync_logic
from libgitmusic.commands.release import release_logic, execute_release


class TestPerformance:
    """性能测试类"""
    
    @pytest.fixture
    def performance_test_environment(self):
        """创建性能测试环境"""
        class PerformanceEnvironment:
            def __init__(self):
                self.temp_dir = None
                self.context = None
                self.performance_metrics = {}
                
            def setup(self, test_file_count=100):
                """设置性能测试环境"""
                self.temp_dir = Path(tempfile.mkdtemp(prefix="gitmusic_performance_test_"))
                
                # 创建目录结构
                work_dir = self.temp_dir / "work"
                cache_root = self.temp_dir / "cache"
                metadata_file = self.temp_dir / "metadata.jsonl"
                release_dir = self.temp_dir / "release"
                logs_dir = self.temp_dir / "logs"
                repo_root = self.temp_dir / "repo"
                
                for dir_path in [work_dir, cache_root, release_dir, logs_dir, repo_root]:
                    dir_path.mkdir(parents=True, exist_ok=True)
                
                # 创建配置
                config = {
                    "transport": {
                        "host": "performance.test.com",
                        "user": "performance_user",
                        "path": "/performance/test",
                        "private_key": "/performance/key",
                    },
                    "performance": {
                        "test_mode": True,
                        "mock_network": True,
                        "file_count": test_file_count,
                    }
                }
                
                # 创建上下文
                self.context = Context(
                    project_root=self.temp_dir,
                    config=config,
                    work_dir=work_dir,
                    cache_root=cache_root,
                    metadata_file=metadata_file,
                    release_dir=release_dir,
                    logs_dir=logs_dir,
                )
                
                # 创建测试文件
                self.create_test_files(test_file_count)
                
            def create_test_files(self, count):
                """创建指定数量的测试文件"""
                for i in range(count):
                    test_file = self.context.work_dir / f"performance_test_{i:04d}.mp3"
                    # 创建模拟MP3文件内容
                    content = f"PERFORMANCE_TEST_AUDIO_DATA_{i}".encode()
                    test_file.write_bytes(content)
                    
            def cleanup(self):
                """清理性能测试环境"""
                if self.temp_dir and self.temp_dir.exists():
                    import shutil
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                    
            def get_metadata_manager(self):
                """获取元数据管理器"""
                return MetadataManager(self.context)
                
            def get_object_store(self):
                """获取对象存储"""
                return ObjectStore(self.context)
                
            def measure_performance(self, operation, operation_name, *args, **kwargs):
                """测量操作性能"""
                start_time = time.time()
                start_memory = psutil.Process().memory_info().rss
                
                result = operation(*args, **kwargs)
                
                end_time = time.time()
                end_memory = psutil.Process().memory_info().rss
                
                metrics = {
                    "operation": operation_name,
                    "duration": end_time - start_time,
                    "memory_delta": end_memory - start_memory,
                    "start_memory": start_memory,
                    "end_memory": end_memory,
                }
                
                self.performance_metrics[operation_name] = metrics
                
                return result, metrics
                
            def assert_performance_requirements(self, operation_name, max_duration=None, max_memory_delta=None):
                """验证性能要求"""
                if operation_name not in self.performance_metrics:
                    pytest.skip(f"Performance metrics not available for {operation_name}")
                    
                metrics = self.performance_metrics[operation_name]
                
                if max_duration is not None:
                    assert metrics["duration"] <= max_duration, f"{operation_name} took {metrics['duration']:.2f}s, expected <= {max_duration}s"
                    
                if max_memory_delta is not None:
                    assert metrics["memory_delta"] <= max_memory_delta, f"{operation_name} used {metrics['memory_delta']} bytes, expected <= {max_memory_delta} bytes"
        
        env = PerformanceEnvironment()
        yield env
        env.cleanup()
        
    @pytest.mark.performance
    def test_publish_performance_small_dataset(self, performance_test_environment):
        """测试小数据集发布性能"""
        env = performance_test_environment
        env.setup(test_file_count=10)
        
        metadata_manager = env.get_metadata_manager()
        
        # 测量发布逻辑性能
        result, metrics = env.measure_performance(
            publish_logic,
            "publish_logic_small",
            metadata_manager
        )
        
        # 验证发布逻辑性能要求
        env.assert_performance_requirements(
            "publish_logic_small",
            max_duration=2.0,  # 2秒以内
            max_memory_delta=50 * 1024 * 1024  # 50MB内存增长
        )
        
        # 如果有文件需要处理，测量执行性能
        if result and len(result[0]) > 0:
            _, metrics = env.measure_performance(
                execute_publish,
                "execute_publish_small",
                metadata_manager,
                result[0]
            )
            
            env.assert_performance_requirements(
                "execute_publish_small",
                max_duration=5.0,  # 5秒以内
                max_memory_delta=100 * 1024 * 1024  # 100MB内存增长
            )
            
    @pytest.mark.performance
    def test_publish_performance_large_dataset(self, performance_test_environment):
        """测试大数据集发布性能"""
        env = performance_test_environment
        env.setup(test_file_count=100)
        
        metadata_manager = env.get_metadata_manager()
        
        # 测量发布逻辑性能
        result, metrics = env.measure_performance(
            publish_logic,
            "publish_logic_large",
            metadata_manager
        )
        
        # 验证大数据集性能要求（更宽松的要求）
        env.assert_performance_requirements(
            "publish_logic_large",
            max_duration=10.0,  # 10秒以内
            max_memory_delta=200 * 1024 * 1024  # 200MB内存增长
        )
        
    @pytest.mark.performance
    def test_sync_performance(self, performance_test_environment):
        """测试同步性能"""
        env = performance_test_environment
        env.setup(test_file_count=50)
        
        # 创建模拟传输
        mock_transport = Mock()
        mock_transport.list_remote_files.return_value = []
        mock_transport.upload.return_value = True
        mock_transport.download.return_value = True
        
        # 测量同步逻辑性能
        result, metrics = env.measure_performance(
            sync_logic,
            "sync_logic",
            env.context.cache_root,
            mock_transport,
            "both",
            dry_run=True  # 避免实际文件传输
        )
        
        # 验证同步性能要求
        env.assert_performance_requirements(
            "sync_logic",
            max_duration=3.0,  # 3秒以内
            max_memory_delta=30 * 1024 * 1024  # 30MB内存增长
        )
        
    @pytest.mark.performance
    def test_release_performance(self, performance_test_environment):
        """测试发布性能"""
        env = performance_test_environment
        env.setup(test_file_count=20)
        
        # 首先发布一些文件
        metadata_manager = env.get_metadata_manager()
        to_process, error = publish_logic(metadata_manager)
        if to_process:
            execute_publish(metadata_manager, to_process)
            
        object_store = env.get_object_store()
        
        # 测量发布逻辑性能
        result, metrics = env.measure_performance(
            release_logic,
            "release_logic",
            metadata_manager,
            object_store,
            env.context.release_dir,
            "local"
        )
        
        # 验证发布性能要求
        env.assert_performance_requirements(
            "release_logic",
            max_duration=5.0,  # 5秒以内
            max_memory_delta=80 * 1024 * 1024  # 80MB内存增长
        )
        
        # 如果有需要发布的文件，测量执行性能
        if result and len(result[0]) > 0:
            _, metrics = env.measure_performance(
                execute_release,
                "execute_release",
                metadata_manager,
                object_store,
                env.context.release_dir,
                result[0]
            )
            
            env.assert_performance_requirements(
                "execute_release",
                max_duration=15.0,  # 15秒以内
                max_memory_delta=150 * 1024 * 1024  # 150MB内存增长
            )
            
    @pytest.mark.performance
    def test_metadata_performance(self, performance_test_environment):
        """测试元数据操作性能"""
        env = performance_test_environment
        env.setup(test_file_count=5)  # 只需要少量文件来测试元数据性能
        
        metadata_manager = env.get_metadata_manager()
        
        # 创建大量元数据条目
        test_entries = []
        for i in range(1000):
            entry = {
                "audio_oid": f"sha256:performance_test_hash_{i:06d}",
                "title": f"Performance Test Song {i}",
                "artists": [f"Performance Artist {i}"],
                "album": f"Performance Album {i}",
                "date": "2024-01-01",
                "created_at": "2024-01-01T00:00:00Z",
            }
            test_entries.append(entry)
            
        # 测量元数据保存性能
        _, metrics = env.measure_performance(
            metadata_manager.save_all,
            "metadata_save_all",
            test_entries
        )
        
        # 验证元数据保存性能要求
        env.assert_performance_requirements(
            "metadata_save_all",
            max_duration=2.0,  # 2秒以内
            max_memory_delta=100 * 1024 * 1024  # 100MB内存增长
        )
        
        # 测量元数据加载性能
        _, metrics = env.measure_performance(
            metadata_manager.load_all,
            "metadata_load_all"
        )
        
        # 验证元数据加载性能要求
        env.assert_performance_requirements(
            "metadata_load_all",
            max_duration=1.0,  # 1秒以内
            max_memory_delta=50 * 1024 * 1024  # 50MB内存增长
        )
        
    @pytest.mark.performance
    def test_concurrent_performance(self, performance_test_environment):
        """测试并发性能"""
        env = performance_test_environment
        env.setup(test_file_count=30)
        
        metadata_manager = env.get_metadata_manager()
        
        def concurrent_publish_operation(thread_id):
            """并发发布操作"""
            try:
                # 每个线程处理不同的文件子集
                to_process, error = publish_logic(metadata_manager)
                return len(to_process) if to_process else 0, error
            except Exception as e:
                return 0, str(e)
                
        # 测量并发操作性能
        start_time = time.time()
        start_memory = psutil.Process().memory_info().rss
        
        threads = []
        for i in range(3):  # 3个并发线程
            thread = threading.Thread(target=concurrent_publish_operation, args=(i,))
            threads.append(thread)
            thread.start()
            
        # 等待所有线程完成
        for thread in threads:
            thread.join()
            
        end_time = time.time()
        end_memory = psutil.Process().memory_info().rss
        
        metrics = {
            "operation": "concurrent_publish",
            "duration": end_time - start_time,
            "memory_delta": end_memory - start_memory,
            "thread_count": 3,
        }
        
        env.performance_metrics["concurrent_publish"] = metrics
        
        # 验证并发性能要求
        env.assert_performance_requirements(
            "concurrent_publish",
            max_duration=10.0,  # 10秒以内
            max_memory_delta=200 * 1024 * 1024  # 200MB内存增长
        )
        
    @pytest.mark.performance
    def test_memory_leak_detection(self, performance_test_environment):
        """测试内存泄漏检测"""
        env = performance_test_environment
        env.setup(test_file_count=10)
        
        metadata_manager = env.get_metadata_manager()
        
        # 多次执行相同操作，检查内存使用趋势
        memory_snapshots = []
        
        for i in range(10):
            # 执行发布逻辑
            to_process, error = publish_logic(metadata_manager)
            
            # 记录内存快照
            memory_info = psutil.Process().memory_info()
            memory_snapshots.append(memory_info.rss)
            
            # 小延迟避免系统过载
            time.sleep(0.1)
            
        # 分析内存使用趋势
        initial_memory = memory_snapshots[0]
        final_memory = memory_snapshots[-1]
        memory_growth = final_memory - initial_memory
        
        # 验证内存使用没有异常增长（允许合理增长）
        assert memory_growth < 100 * 1024 * 1024, f"Potential memory leak detected: {memory_growth} bytes growth over 10 iterations"
        
        # 验证内存使用趋势相对平稳
        memory_variance = max(memory_snapshots) - min(memory_snapshots)
        assert memory_variance < 50 * 1024 * 1024, f"Memory usage too volatile: {memory_variance} bytes variance"
        
    @pytest.mark.performance
    def test_scalability_analysis(self, performance_test_environment):
        """测试可扩展性分析"""
        file_counts = [10, 50, 100]  # 不同规模的测试
        results = {}
        
        for file_count in file_counts:
            env = performance_test_environment
            env.setup(test_file_count=file_count)
            
            metadata_manager = env.get_metadata_manager()
            
            # 测量发布逻辑性能
            result, metrics = env.measure_performance(
                publish_logic,
                f"publish_logic_{file_count}_files",
                metadata_manager
            )
            
            results[file_count] = {
                "duration": metrics["duration"],
                "memory_delta": metrics["memory_delta"],
                "files_processed": len(result[0]) if result else 0,
            }
            
            # 清理环境
            env.cleanup()
            
        # 分析可扩展性趋势
        durations = [results[count]["duration"] for count in file_counts]
        memory_deltas = [results[count]["memory_delta"] for count in file_counts]
        
        # 验证性能增长趋势合理
        # 文件数量翻倍，时间增长不应超过3倍
        if len(durations) >= 2:
            time_ratio = durations[-1] / durations[0]
            file_ratio = file_counts[-1] / file_counts[0]
            assert time_ratio <= file_ratio * 3, f"Performance degradation too severe: {time_ratio}x time increase for {file_ratio}x file increase"
            
        # 内存增长趋势合理
        if len(memory_deltas) >= 2:
            memory_ratio = memory_deltas[-1] / memory_deltas[0]
            assert memory_ratio <= file_ratio * 2, f"Memory usage growth too high: {memory_ratio}x memory increase for {file_ratio}x file increase"
            
    @pytest.mark.performance
    def test_performance_regression_detection(self, performance_test_environment):
        """测试性能回归检测"""
        env = performance_test_environment
        env.setup(test_file_count=50)
        
        metadata_manager = env.get_metadata_manager()
        
        # 基线性能测试（模拟历史性能）
        baseline_metrics = {
            "publish_logic": {"duration": 2.0, "memory_delta": 50 * 1024 * 1024},
            "metadata_save_all": {"duration": 1.0, "memory_delta": 30 * 1024 * 1024},
        }
        
        # 当前性能测试
        current_metrics = {}
        
        # 测量发布逻辑性能
        result, metrics = env.measure_performance(
            publish_logic,
            "publish_logic_current",
            metadata_manager
        )
        current_metrics["publish_logic"] = metrics
        
        # 创建测试元数据
        test_entries = []
        for i in range(100):
            entry = {
                "audio_oid": f"sha256:regression_test_hash_{i:03d}",
                "title": f"Regression Test Song {i}",
                "artists": [f"Regression Artist {i}"],
                "created_at": "2024-01-01T00:00:00Z",
            }
            test_entries.append(entry)
            
        # 测量元数据保存性能
        _, metrics = env.measure_performance(
            metadata_manager.save_all,
            "metadata_save_all_current",
            test_entries
        )
        current_metrics["metadata_save_all"] = metrics
        
        # 检测性能回归
        performance_regression_threshold = 2.0  # 允许2倍以内的性能退化
        memory_regression_threshold = 2.0
        
        for operation in baseline_metrics:
            if operation in current_metrics:
                baseline_duration = baseline_metrics[operation]["duration"]
                current_duration = current_metrics[operation]["duration"]
                duration_ratio = current_duration / baseline_duration
                
                baseline_memory = baseline_metrics[operation]["memory_delta"]
                current_memory = current_metrics[operation]["memory_delta"]
                memory_ratio = current_memory / baseline_memory if baseline_memory > 0 else 0
                
                # 验证性能没有严重回归
                assert duration_ratio <= performance_regression_threshold, f"Performance regression detected in {operation}: {duration_ratio}x slower than baseline"
                assert memory_ratio <= memory_regression_threshold, f"Memory regression detected in {operation}: {memory_ratio}x more memory than baseline"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])