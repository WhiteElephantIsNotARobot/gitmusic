# GitMusic 集成测试框架

## 概述

本框架提供了一套完整的集成测试解决方案，用于验证GitMusic CLI工具的功能、性能、可靠性和边界条件处理。

## 测试组件

### 核心框架 (`conftest.py`)
- 隔离环境创建
- 测试数据生成
- 故障注入机制
- 模拟传输层

### 功能测试
- **命令链测试** (`test_command_chains.py`): 验证完整工作流
- **故障注入测试** (`test_fault_injection.py`): 验证错误处理能力
- **恢复场景测试** (`test_recovery_scenarios.py`): 验证系统恢复能力

### 性能测试
- **性能基准测试** (`test_performance.py`): 性能监控和回归检测
- **并发测试**: 多线程/多进程场景验证
- **内存泄漏检测**: 长时间运行稳定性验证

### 边界条件测试
- **边界情况测试** (`test_edge_cases.py`): 极端条件处理
- **隔离环境测试** (`test_isolation.py`): 环境隔离性验证

## 快速开始

### 运行简单测试
```bash
python tests/integration/simple_test_runner.py
```

### 运行特定测试模块
```bash
# 需要安装pytest
python -m pytest tests/integration/test_command_chains.py -v
```

### 运行所有集成测试
```bash
python -m pytest tests/integration/ -v --tb=short
```

## 测试环境

### 隔离环境特性
- 每个测试使用独立的临时目录
- 自动环境变量隔离
- 文件系统完全隔离
- 测试完成后自动清理

### 测试数据生成
- 自动生成样本音频文件
- 可配置的元数据模板
- 支持多种文件格式和大小
- Unicode和特殊字符支持

## 故障注入

### 支持的故障类型
- **网络中断**: 模拟网络连接失败
- **哈希不匹配**: 模拟文件完整性验证失败
- **权限错误**: 模拟文件系统权限问题
- **磁盘空间不足**: 模拟存储空间耗尽
- **部分上传**: 模拟不完整的数据传输

### 故障配置示例
```python
# 网络中断在50%完成后触发
mock_transport.inject_failure('network_interrupt', after_percent=0.5)

# 哈希不匹配在特定文件上触发
mock_transport.inject_failure('hash_mismatch', target_files=[1, 3])
```

## 性能测试

### 监控指标
- 执行时间测量
- 内存使用监控
- CPU使用率跟踪
- 并发性能分析

### 性能基准
- 发布时间: < 10秒 (100个文件)
- 同步时间: < 3秒 (50个文件)
- 发布时间: < 15秒 (发布阶段)
- 内存增长: < 200MB (大数据集)

## 使用示例

### 创建隔离测试环境
```python
def test_my_feature(isolated_env):
    """使用隔离环境进行测试"""
    env = isolated_env
    
    # 创建测试文件
    test_file = env.work_dir / "test.mp3"
    test_file.write_bytes(b"TEST_AUDIO_DATA")
    
    # 执行测试操作
    result = run_gitmusic_command([
        'publish', 
        '--work-dir', str(env.work_dir),
        '--cache-root', str(env.cache_root)
    ])
    
    assert result.returncode == 0
```

### 故障注入测试
```python
def test_network_recovery(isolated_env, mock_transport):
    """测试网络中断恢复"""
    env = isolated_env
    
    # 注入网络故障
    mock_transport.inject_failure('network_interrupt', after_percent=0.5)
    
    # 第一次尝试应该失败
    result1 = run_gitmusic_command(['sync', 'upload'])
    assert result1.returncode != 0
    
    # 移除故障后重试
    mock_transport.remove_failure()
    result2 = run_gitmusic_command(['sync', 'upload'])
    assert result2.returncode == 0
```

### 性能测试
```python
def test_performance_benchmark(performance_env):
    """性能基准测试"""
    env = performance_env
    env.setup(test_file_count=100)
    
    metadata_manager = env.get_metadata_manager()
    
    # 测量操作性能
    result, metrics = env.measure_performance(
        publish_logic,
        "publish_benchmark",
        metadata_manager
    )
    
    # 验证性能要求
    env.assert_performance_requirements(
        "publish_benchmark",
        max_duration=10.0,  # 10秒以内
        max_memory_delta=200 * 1024 * 1024  # 200MB内存增长
    )
```

## 测试数据

### 样本音频文件
- 小型MP3文件 (3秒静音)
- 可配置的音频参数
- 支持嵌入ID3元数据
- 多种文件大小选项

### 测试元数据模板
```python
sample_metadata = {
    "audio_oid": "sha256:test_hash",
    "title": "Test Song",
    "artists": ["Test Artist"],
    "album": "Test Album", 
    "date": "2024-01-01",
    "created_at": "2024-01-01T00:00:00Z",
}
```

## 故障排除

### 常见问题

1. **测试运行失败**
   - 检查依赖项是否安装
   - 验证Python路径配置
   - 检查文件权限

2. **隔离环境创建失败**
   - 检查磁盘空间
   - 验证临时目录权限
   - 检查系统资源限制

3. **故障注入不工作**
   - 确认mock_transport正确配置
   - 检查故障触发条件
   - 验证异常处理逻辑

### 调试建议
- 使用`-v`选项获取详细输出
- 检查测试日志文件
- 使用`--tb=long`获取完整错误跟踪
- 手动检查临时目录内容

## 扩展测试

### 添加新的测试模块
1. 在`tests/integration/`创建新文件
2. 导入必要的fixture和工具函数
3. 遵循现有的测试模式
4. 使用适当的测试标记

### 自定义故障场景
1. 扩展`FaultInjector`类
2. 添加新的故障类型
3. 实现故障触发逻辑
4. 更新测试用例

### 性能基准调整
1. 修改`performance_test_environment`
2. 调整性能阈值
3. 添加新的性能指标
4. 更新回归检测逻辑

## 最佳实践

1. **保持测试独立**: 每个测试应该独立运行，不依赖其他测试
2. **使用隔离环境**: 总是使用fixture提供的隔离环境
3. **清理资源**: 确保测试完成后清理所有资源
4. **有意义的断言**: 使用具体和有意义的断言消息
5. **性能监控**: 定期运行性能测试以检测回归
6. **故障覆盖**: 确保测试覆盖各种故障场景

## 持续集成

建议将集成测试集成到CI/CD流程中：

```yaml
# GitHub Actions 示例
- name: Run Integration Tests
  run: |
    python -m pip install -r requirements-test.txt
    python -m pytest tests/integration/ -v --tb=short
```

## 相关文档

- [项目架构文档](../ARCHITECTURE.md)
- [开发指南](../DEVELOPMENT.md)
- [性能调优指南](../PERFORMANCE.md)
- [故障排除指南](../TROUBLESHOOTING.md)