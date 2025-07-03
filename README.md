# best_kernel_config_for_benchmarksql_test_tools
适用于数据库benchmarkaql参数优化场景, 例如: 华为 gaussdb, DM达梦, 腾讯 POSTGRESQL-XC 等


功能
基于上游A-Tune工具对postgresql场景下关注的内核配置,  通过迭代分析,识别出对多次轮训结果中选择最优的跑分时内核配置参数并进行打印 , 同时针对优化结果配置结果提供了 apply_sysctl_config.sh一键部署设置
支持参数扩展, 同时可满足POC工单数据库交付场景, 适配数据库厂商标准化的用例执行步骤



![示例图片](https://github.com/relaxzw/best_kernel_config_for_benchmarksql_test_tools/raw/main/print_report.png)



## 功能

- 基于上游 A-Tune 工具对 PostgreSQL 场景下关注的内核配置，通过迭代分析，识别出对多次轮训结果中选择最优的跑分时内核配置参数并进行打印。
- 针对优化结果配置结果提供 `apply_sysctl_config.sh` 一键部署设置。
- 支持参数扩展，同时可满足 POC 工单数据库交付场景，适配数据库厂商标准化的用例执行步骤。

## 前置调节

确保 benchmarkSQL 已与 pg 数据正常通信。

## 配置参数

```python
PARAM_CONFIG = [
    {"name": "kernel.sched_cfs_bandwidth_slice_us", "min": 1000, "max": 50000, "step": 1000},
    {"name": "kernel.sched_migration_cost_ns", "min": 100000, "max": 5000000, "step": 100000},
    {"name": "kernel.sched_latency_ns", "min": 1000000, "max": 100000000, "step": 1000000},
    {"name": "kernel.sched_min_granularity_ns", "min": 1000000, "max": 100000000, "step": 1000000},
    {"name": "kernel.sched_wakeup_granularity_ns", "min": 1000000, "max": 100000000, "step": 1000000},
    {"name": "net.core.rmem_default", "min": 8192, "max": 1048576, "step": 8192},
    {"name": "net.core.rmem_max", "min": 1048576, "max": 67108864, "step": 1048576},
    {"name": "net.core.wmem_default", "min": 8192, "max": 1048576, "step": 8192},
    {"name": "net.core.wmem_max", "min": 1048576, "max": 67108864, "step": 1048576},
    {"name": "net.ipv4.tcp_rmem", "min": 4096, "max": 4194304, "step": 4096},
    {"name": "net.ipv4.tcp_wmem", "min": 4096, "max": 4194304, "step": 4096},
    {"name": "net.core.dev_weight", "min": 16, "max": 1024, "step": 16},
    {"name": "net.ipv4.tcp_max_syn_backlog", "min": 1024, "max": 262144, "step": 1024},
    {"name": "net.core.somaxconn", "min": 128, "max": 65536, "step": 128}
]

CONFIG = {
    "destroy_script": "./runDatabaseDestroy.sh",  # benchmark 绝对路径，或者脚本在 benchmark/run/ 路径下运行
    "build_script": "./runDatabaseBuild.sh",      # benchmark 绝对路径，或者脚本在 benchmark/run/ 路径下运行
    "benchmark_script": "./runBenchmark.sh",     # benchmark 绝对路径，或者脚本在 benchmark/run/ 路径下运行
    "props_file": "props.openGauss",             # benchmark 跑测时指定的测试文件绝对路径，或者脚本在 benchmark/run/ 路径下运行
    "benchmark_timeout": 300,                    # 跑测一轮需要的耗时，基准测试超时时间（秒）。benchmark config 中 runMins=10 则是十分钟，那么这里就设置 >600，例如 605
    "iterations": 10,                            # 优化迭代次数，需要跑测多少次，取最优
    "stabilize_time": 5,                         # 系统稳定时间（秒），不建议修改
    "recovery_time": 3,                          # 系统恢复时间（秒），不建议修改
    "server_url": "http://192.168.1.2:5000"     # 服务器端地址
}


执行流程图说明

benchmark服务器确认与数据库可正常进行tpmC测试,修改optimize_benchmark.py 中server_url为数据库服务器地址, 确保2台机器防火墙已关闭或者允许5000端口 -> 数据库服务器执行pyhon3 optimize_benchmark.py server启动监听server -> benchmark服务器执行pyhon3 optimize_benchmark.py启动测试


数据库,benchmark环境均需执行安装依赖
pip3 install -r requirements.txt


数据库服务器环境下root权限运行脚本, 启动监听server
pyhon3 optimize_benchmark.py server 
benchmark服务器环境下root权限运行脚本 , 启动测试
pyhon3 optimize_benchmark.py

数据库环境下使能优化配置
optimize_benchmark.py 脚本同级目录下执行
sudo ./apply_sysctl_config.sh 



逻辑流程说明

# 工具在Benchmark 测试流程的使用说明

## 1. 初始化阶段

1. **启动测试**：
    - Benchmark 服务器启动测试。
    - 保留数据库环境下当前系统参数的默认值到 `default_sysctl.conf` 文件中，用于后续还原。

## 2. 参数准备阶段

2. **参数配置**：
    - 从 `PARAM_CONFIG` 中读取参数配置。

## 3. 迭代测试阶段

3. **随机分组**：
    - 根据 `iterations` 设置（默认 10 轮），对 `PARAM_CONFIG` 中的参数进行随机分组。

4. **每轮测试**：
    - 每轮测试开始前，调用以下脚本：
        - `./runDatabaseDestroy.sh`：销毁数据库环境。
        - `./runDatabaseBuild.sh`：构建数据库环境。
    - 将当前分组的参数下发到数据库服务器端，并使能这些参数。
    - 调用 `./runBenchmark.sh` 进行基准测试，生成 `NewOrders` 值。

5. **结果对比**：
    - 每轮测试结束后，对比生成的 `NewOrders` 值。
    - 选出最高值，并记录此时的 `PARAM_CONFIG` 参数到 `best_sysctl.conf` 文件中。

## 4. 结果处理阶段

6. **保存最佳配置**：
    - 将 `best_sysctl.conf` 和 `apply_sysctl_config.sh` 拷贝到数据库服务器同级目录下。
    - 执行以下命令快速部署最佳配置：
        ```bash
        sudo bash apply_sysctl_config.sh best_sysctl.conf
        ```

7. **还原默认配置**：
    - 如果需要还原默认配置，执行以下命令：
        ```bash
        sudo bash apply_sysctl_config.sh default_sysctl.conf
        ```

## 8. 测试结束

8. **手动关闭**：
    - 测试完成后，服务器端的server不会自动退出，需要手动关闭。
