"""
集成测试配置和共享fixture
"""
import pytest
import tempfile
import shutil
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime, timezone

# 添加repo目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "repo"))

from libgitmusic.context import Context
from libgitmusic.metadata import MetadataManager
from libgitmusic.object_store import ObjectStore
from libgitmusic.transport import TransportAdapter


@pytest.fixture
def temp_dir():
    """创建隔离的临时测试目录"""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def test_config():
    """测试配置"""
    return {
        "transport": {
            "host": "test.example.com",
            "user": "testuser", 
            "path": "/tmp/test",
            "private_key": "/tmp/key",
        },
        "test": {
            "mock_transport": True,
            "mock_network": True,
            "failure_injection": False,
        }
    }


@pytest.fixture
def test_context(temp_dir, test_config):
    """创建测试上下文环境"""
    # 创建目录结构
    work_dir = temp_dir / "work"
    cache_root = temp_dir / "cache"
    metadata_file = temp_dir / "metadata.jsonl"
    release_dir = temp_dir / "release"
    logs_dir = temp_dir / "logs"
    repo_root = temp_dir / "repo"
    
    for dir_path in [work_dir, cache_root, release_dir, logs_dir, repo_root]:
        dir_path.mkdir(parents=True, exist_ok=True)
    
    # 创建上下文
    ctx = Context(
        project_root=temp_dir,
        config=test_config,
        work_dir=work_dir,
        cache_root=cache_root,
        metadata_file=metadata_file,
        release_dir=release_dir,
        logs_dir=logs_dir,
    )
    
    return ctx


@pytest.fixture
def mock_transport():
    """模拟传输适配器"""
    transport = Mock(spec=TransportAdapter)
    
    # 模拟远程文件列表
    transport.list_remote_files.return_value = []
    transport.upload.return_value = True
    transport.download.return_value = True
    # 注意：TransportAdapter接口中没有exists方法，移除这一行
    # transport.exists.return_value = False
    
    return transport


@pytest.fixture
def metadata_manager(test_context):
    """创建元数据管理器"""
    return MetadataManager(test_context)


@pytest.fixture
def object_store(test_context):
    """创建对象存储"""
    return ObjectStore(test_context)


@pytest.fixture
def sample_audio_file():
    """创建样本音频文件"""
    def _create_sample_audio(filename="test_song.mp3", with_metadata=True, with_cover=False):
        # 这里创建一个最小的MP3文件用于测试
        # 在实际环境中，我们使用预生成的测试音频文件
        test_data_dir = Path(__file__).parent / "data"
        test_data_dir.mkdir(exist_ok=True)
        
        # 复制预生成的测试音频文件
        sample_file = test_data_dir / filename
        if not sample_file.exists():
            # 创建虚拟MP3文件（ID3标签头部）
            sample_file.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x23\x54\x53\x53\x45\x00\x00\x00\x0f\x00\x00\x03\x54\x54\x32\x00\x00\x00\x0f\x00\x00\x03\x54\x50\x45\x31\x00\x00\x00\x0f\x00\x00\x03\x54\x41\x4c\x42\x00\x00\x00\x0f\x00\x00\x03\x54\x44\x52\x43\x00\x00\x00\x0f\x00\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00")
        
        return sample_file
    
    return _create_sample_audio


@pytest.fixture
def sample_cover_file():
    """创建样本封面文件"""
    def _create_sample_cover(filename="test_cover.jpg"):
        test_data_dir = Path(__file__).parent / "data"
        test_data_dir.mkdir(exist_ok=True)
        
        sample_file = test_data_dir / filename
        if not sample_file.exists():
            # 创建最小的JPEG文件
            jpeg_header = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x01\x01\x11\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\xaa\xff\xd9'
            sample_file.write_bytes(jpeg_header)
        
        return sample_file
    
    return _create_sample_cover


@pytest.fixture
def failure_injector():
    """故障注入框架"""
    class FailureInjector:
        def __init__(self):
            self.failure_scenarios = {}
            self.call_count = {}
            
        def set_failure_scenario(self, name, scenario):
            """设置故障场景"""
            self.failure_scenarios[name] = scenario
            self.call_count[name] = 0
            
        def inject_failure(self, name):
            """注入故障"""
            if name not in self.failure_scenarios:
                return None
                
            scenario = self.failure_scenarios[name]
            self.call_count[name] += 1
            
            # 检查是否应该触发故障
            if "call_count" in scenario:
                if self.call_count[name] != scenario["call_count"]:
                    return None
                    
            if "probability" in scenario:
                import random
                if random.random() > scenario["probability"]:
                    return None
            
            # 返回故障异常或值
            if "exception" in scenario:
                raise scenario["exception"]
            if "return_value" in scenario:
                return scenario["return_value"]
                
            return None
            
        def reset(self):
            """重置故障注入器"""
            self.failure_scenarios.clear()
            self.call_count.clear()
    
    return FailureInjector()


@pytest.fixture
def network_simulator():
    """网络条件模拟器"""
    class NetworkSimulator:
        def __init__(self):
            self.conditions = {
                "latency": 0,
                "bandwidth_limit": None,
                "packet_loss": 0,
                "intermittent_failure": False,
            }
            
        def set_network_condition(self, condition, value):
            """设置网络条件"""
            if condition in self.conditions:
                self.conditions[condition] = value
                
        def simulate_network_delay(self):
            """模拟网络延迟"""
            if self.conditions["latency"] > 0:
                import time
                time.sleep(self.conditions["latency"])
                
        def should_fail_request(self):
            """判断请求是否应该失败"""
            import random
            
            # 间歇性故障
            if self.conditions["intermittent_failure"]:
                return random.random() < 0.3
                
            # 包丢失
            if self.conditions["packet_loss"] > 0:
                return random.random() < self.conditions["packet_loss"]
                
            return False
            
        def get_bandwidth_limit(self):
            """获取带宽限制"""
            return self.conditions["bandwidth_limit"]
            
        def reset(self):
            """重置网络条件"""
            self.conditions = {
                "latency": 0,
                "bandwidth_limit": None,
                "packet_loss": 0,
                "intermittent_failure": False,
            }
    
    return NetworkSimulator()


@pytest.fixture
def integration_test_env(test_context, mock_transport, failure_injector, network_simulator):
    """集成测试环境"""
    class IntegrationTestEnvironment:
        def __init__(self, context, transport, failure_injector, network_simulator):
            self.context = context
            self.transport = transport
            self.failure_injector = failure_injector
            self.network_simulator = network_simulator
            self.original_functions = {}
            
        def setup(self):
            """设置测试环境"""
            # 模拟网络条件
            self._mock_network_functions()
            
            # 设置故障注入
            self._setup_failure_injection()
            
        def teardown(self):
            """清理测试环境"""
            # 恢复原始函数
            self._restore_original_functions()
            
            # 重置故障注入器
            self.failure_injector.reset()
            
            # 重置网络模拟器
            self.network_simulator.reset()
            
        def _mock_network_functions(self):
            """模拟网络相关函数"""
            # 这里可以添加网络函数的mock
            pass
            
        def _setup_failure_injection(self):
            """设置故障注入"""
            # 这里可以设置各种故障场景
            pass
            
        def _restore_original_functions(self):
            """恢复原始函数"""
            # 恢复被mock的函数
            for module, func_name, original in self.original_functions.values():
                setattr(module, func_name, original)
            self.original_functions.clear()
            
        def create_test_scenario(self, scenario_name, **kwargs):
            """创建测试场景"""
            scenarios = {
                "network_interruption": {
                    "network_conditions": {
                        "intermittent_failure": True,
                        "packet_loss": 0.1,
                    },
                    "failure_scenarios": {
                        "transport_upload": {
                            "probability": 0.2,
                            "exception": Exception("Network interruption during upload"),
                        },
                        "transport_download": {
                            "probability": 0.2,
                            "exception": Exception("Network interruption during download"),
                        },
                    },
                },
                "hash_mismatch": {
                    "failure_scenarios": {
                        "hash_verification": {
                            "probability": 0.3,
                            "return_value": False,  # 哈希验证失败
                        },
                    },
                },
                "file_conflict": {
                    "setup_files": [
                        {"path": "work/existing_song.mp3", "content": b"existing_file"},
                    ],
                },
            }
            
            scenario = scenarios.get(scenario_name, {})
            
            # 应用网络条件
            if "network_conditions" in scenario:
                for condition, value in scenario["network_conditions"].items():
                    self.network_simulator.set_network_condition(condition, value)
            
            # 应用故障场景
            if "failure_scenarios" in scenario:
                for name, failure_scenario in scenario["failure_scenarios"].items():
                    self.failure_injector.set_failure_scenario(name, failure_scenario)
            
            # 设置文件
            if "setup_files" in scenario:
                for file_info in scenario["setup_files"]:
                    file_path = self.context.project_root / file_info["path"]
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    if "content" in file_info:
                        file_path.write_bytes(file_info["content"])
                    elif "copy_from" in file_info:
                        shutil.copy(file_info["copy_from"], file_path)
    
    env = IntegrationTestEnvironment(test_context, mock_transport, failure_injector, network_simulator)
    env.setup()
    
    yield env
    
    env.teardown()


@pytest.fixture
def sample_metadata_entries():
    """样本元数据条目"""
    return [
        {
            "audio_oid": "sha256:2cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f0",
            "title": "Test Song 1",
            "artists": ["Test Artist 1", "Test Artist 2"],
            "album": "Test Album 1",
            "date": "2024-01-01",
            "created_at": "2024-01-01T00:00:00Z",
            "cover_oid": "sha256:1cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f0",
        },
        {
            "audio_oid": "sha256:3cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f1",
            "title": "Test Song 2",
            "artists": ["Test Artist 3"],
            "album": "Test Album 2",
            "date": "2024-01-02",
            "created_at": "2024-01-02T00:00:00Z",
            "cover_oid": "sha256:2cf24dba4f21d4288094e9b9eb389f15e76c1e5b09e940a0c8fb9c37c6a0a8f1",
        },
    ]