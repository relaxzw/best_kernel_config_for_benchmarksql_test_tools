"""
changelog:

V0.1
考虑到会对系统当前配置做变更, 故追加逻辑 再benchmark运行前保留系统当前设置, 工具执行完成后进行还原

"""

import os
import re
import subprocess
import numpy as np
import time
import logging
import tempfile
import shutil
import signal
import sys
import warnings
import requests

# 设置日志记录
logging.basicConfig(
    level=logging.INFO,  # logging.WARNING, logging.ERROR, logging.INFO
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("sysctl_optimizer.log"),
        logging.StreamHandler()
    ]
)

# 参数配置定义,如果有新的关切对象,可以再这里加
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

# 配置参数（可外部传入）
CONFIG = {
    "destroy_script": "./runDatabaseDestroy.sh", #benchmark绝对路径 ,或者脚本在benchmark/run/路径下运行
    "build_script": "./runDatabaseBuild.sh",  #benchmark绝对路径,或者脚本在benchmark/run/路径下运行
    "benchmark_script": "./runBenchmark.sh", #benchmark 绝对路径,或者脚本在benchmark/run/路径下运行
    "props_file": "props.openGauss",  # benchmark 跑测时指定的测试文件绝对路径, 或者脚本在benchmark/run/路径下运行
    "benchmark_timeout": 300,  # 跑测一轮需要的耗时, 基准测试超时时间（秒）,benchmark config 中runMins=10 则是十分钟, 那么这里就设置>600, 例如605
    "iterations": 10,          # 优化迭代次数, 需要跑测多少次, 取最优
    "stabilize_time": 5,       # 系统稳定时间（秒）,不建议修改
    "recovery_time": 3,        # 系统恢复时间（秒）,不建议修改
    "server_url": "http://192.168.1.2:5000"  # 服务器端地址
}

class SysctlOptimizer:
    def __init__(self, param_config, config=CONFIG):
        self.param_config = param_config
        self.config = config
        self.server_url = config["server_url"]  # 统一管理服务器端地址
        self.destroy_script = config["destroy_script"]
        self.build_script = config["build_script"]
        self.benchmark_script = config["benchmark_script"]
        self.props_file = config["props_file"]
        self.benchmark_timeout = config["benchmark_timeout"]
        self.stabilize_time = config["stabilize_time"]
        self.recovery_time = config["recovery_time"]
        
        self.baseline_values = self._get_baseline_values()
        self.best_score = -np.inf
        self.best_params = None
        self.iteration = 0
        self.start_time = time.time()
        self.temp_dir = tempfile.mkdtemp(prefix="benchmark_")
        logging.info(f"Created temporary directory: {self.temp_dir}")
        
    def __del__(self):
        """清理临时目录"""
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
                logging.info(f"Cleaned temporary directory: {self.temp_dir}")
        except Exception as e:
            logging.warning(f"Failed to clean temp directory: {str(e)}")
        
    def _get_baseline_values(self):
        """获取当前系统参数值作为基线"""
        baseline = {}
        for param in self.param_config:
            try:
                result = subprocess.run(
                    ["sysctl", "-n", param["name"]],
                    capture_output=True, text=True, check=True
                )
                baseline[param["name"]] = result.stdout.strip()
                logging.info(f"Baseline {param['name']}: {baseline[param['name']]}")
            except subprocess.CalledProcessError as e:
                logging.warning(f"Failed to get baseline for {param['name']}: {e.stderr}")
                baseline[param["name"]] = "0"
        return baseline
    
    def _set_sysctl(self, params):
        """设置系统参数"""
        # 设置每个参数
        for name, value in params.items():
            try:
                result = subprocess.run(
                    ["sudo", "sysctl", "-w", f"{name}={value}"],
                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                logging.info(f"Set {name}={value}: {result.stdout.strip()}")
            except subprocess.CalledProcessError as e:
                logging.error(f"Error setting {name}={value}: {e.stderr}")
        
        # 然后执行 sysctl -p 使所有更改生效
        try:
            logging.info("Applying system parameters with 'sysctl -p'")
            apply_result = subprocess.run(
                ["sudo", "sysctl", "-p"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            logging.info(f"sysctl -p output: {apply_result.stdout.strip()}")
        except subprocess.CalledProcessError as e:
            logging.warning(f"warning applying system parameters with 'sysctl -p': {e.stderr}")

    def _restore_baseline(self):
        """恢复基线参数值"""
        self._set_sysctl(self.baseline_values)
        logging.info("Restored baseline parameters")
    
    def _run_database_scripts(self):
        """运行数据库销毁和构建脚本"""
        try:
            # 运行数据库销毁脚本
            logging.info(f"Running database destroy: {self.destroy_script} {self.props_file}")
            destroy_result = subprocess.run(
                [self.destroy_script, self.props_file],
                capture_output=True, text=True
            )
            
            if destroy_result.returncode != 0:
                logging.error(f"Database destroy failed: {destroy_result.stderr}")
                return False
            
            # 运行数据库构建脚本
            logging.info(f"Running database build: {self.build_script} {self.props_file}")
            build_result = subprocess.run(
                [self.build_script, self.props_file],
                capture_output=True, text=True
            )
            
            if build_result.returncode != 0:
                logging.error(f"Database build failed: {build_result.stderr}")
                return False
            
            return True
        except Exception as e:
            logging.error(f"Database scripts execution failed: {str(e)}")
            return False
    
    def _run_benchmark(self):
        """运行基准测试并提取tpmC值"""
        # 创建唯一的输出文件名
        output_file = os.path.join(self.temp_dir, f"benchmark_output_{self.iteration}.log")
        
        try:
            # 先运行数据库销毁和构建
            if not self._run_database_scripts():
                return 0.0
            
            # 运行基准测试
            logging.info(f"Running benchmark: {self.benchmark_script} {self.props_file}")
            logging.info(f"Output will be saved to: {output_file}")
            
            # 启动基准测试进程
            with open(output_file, "w") as f:
                process = subprocess.Popen(
                    [self.benchmark_script, self.props_file],
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    text=True,
                    preexec_fn=os.setsid  # 创建新的进程组
                )
            
            # 等待基准测试完成
            start_time = time.time()
            
            while process.poll() is None:
                elapsed = time.time() - start_time
                if elapsed > self.benchmark_timeout:
                    logging.error(f"Benchmark timed out after {self.benchmark_timeout} seconds")
                    # 终止整个进程组
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    return 0.0
                time.sleep(5)  # 每5秒检查一次
            
            # 检查返回码
            returncode = process.returncode
            if (returncode != 0):
                logging.error(f"Benchmark exited with non-zero status: {returncode}")
                return 0.0
            
            # 等待文件写入完成
            time.sleep(1)
            
            # 读取输出文件内容
            with open(output_file, "r") as f:
                output = f.read()
            
            # 记录输出文件路径以便手动检查
            logging.info(f"Benchmark output saved to: {output_file}")
            
            # 使用正则表达式提取tpmC值
            # 尝试匹配: "Measured tpmC (NewOrders) = 2696.08"
            match = re.search(r"Measured tpmC \(NewOrders\)\s*=\s*(\d+\.?\d*)", output)
            if match:
                tpmc = float(match.group(1))
                logging.info(f"Extracted tpmC (NewOrders): {tpmc}")
                return tpmc
            
            # 尝试匹配: "tpmC\s*:\s*(\d+\.?\d*)"
            match = re.search(r"tpmC\s*:\s*(\d+\.?\d*)", output)
            if match:
                tpmc = float(match.group(1))
                logging.info(f"Extracted tpmC: {tpmc}")
                return tpmc
            
            # 尝试匹配: "TPM:\s*(\d+\.?\d*)"
            match = re.search(r"TPM:\s*(\d+\.?\d*)", output)
            if match:
                tpmc = float(match.group(1))
                logging.info(f"Extracted TPM: {tpmc}")
                return tpmc
            
            # 如果所有匹配都失败，记录最后50行输出
            lines = output.splitlines()
            last_lines = "\n".join(lines[-50:]) if len(lines) > 50 else output
            logging.error(f"Failed to find tpmC in benchmark output. Last 50 lines:\n{last_lines}")
            return 0.0
                
        except Exception as e:
            logging.error(f"Benchmark execution failed: {str(e)}")
            return 0.0
        finally:
            # 保留输出文件用于调试
            logging.info(f"Benchmark output preserved at: {output_file}")
    
    def _generate_random_params(self):
        """生成随机参数组合"""
        params = {}
        for config in self.param_config:
            # 计算可能的取值数量
            num_steps = (config["max"] - config["min"]) // config["step"]
            # 随机选择一个步长
            step_index = np.random.randint(0, num_steps + 1)
            value = config["min"] + step_index * config["step"]
            params[config["name"]] = str(value)
        return params

    def _send_params_to_database(self, params):
        """发送参数配置到数据库端"""
        try:
            response = requests.post(f"{self.server_url}/apply_params", json=params)
            response.raise_for_status()
            result = response.json()
            if result.get("status") == "success":
                logging.info("Parameters applied successfully on database server")
                return True
            else:
                logging.error("Failed to apply parameters on database server")
                return False
        except requests.RequestException as e:
            logging.error(f"Error sending parameters to database server: {str(e)}")
            return False

    def _save_config_to_server(self, endpoint, params):
        """发送配置到服务器端保存"""
        try:
            response = requests.post(f"{self.server_url}/{endpoint}", json=params)
            response.raise_for_status()
            result = response.json()
            if result.get("status") == "success":
                logging.info(f"Configuration saved successfully to server: {endpoint}")
                return True
            else:
                logging.error(f"Failed to save configuration to server: {endpoint}")
                return False
        except requests.RequestException as e:
            logging.error(f"Error saving configuration to server: {str(e)}")
            return False

    def evaluate_params(self, params):
        """评估参数组合的性能"""
        logging.info(f"Evaluating parameter set #{self.iteration}")
        logging.info(f"Parameters: {params}")
        try:
            # 发送参数到数据库端
            if not self._send_params_to_database(params):
                return 0.0
            
            # 添加延迟让系统稳定
            logging.info(f"Waiting {self.stabilize_time} seconds for system to stabilize...")
            time.sleep(self.stabilize_time)
            
            # 运行基准测试
            score = self._run_benchmark()
            return score
        except Exception as e:
            logging.error(f"Error during evaluation: {str(e)}")
            return 0.0
        finally:
            self.iteration += 1

    def random_search(self, iterations=10):
        """随机搜索优化"""
        logging.info(f"Starting random search with {iterations} iterations")
        
        results = []
        for i in range(iterations):
            logging.info(f"\n{'='*50}")
            logging.info(f"Starting iteration {i+1}/{iterations}")
            logging.info(f"{'='*50}")
            
            params = self._generate_random_params()
            score = self.evaluate_params(params)
            
            # 记录结果
            elapsed = time.time() - self.start_time
            results.append((params, score, elapsed))
            
            if score > self.best_score:
                self.best_score = score
                self.best_params = params
                logging.info(f"New best: tpmC={score:.2f} at iteration {i}")
            
            logging.info(f"Iteration {i+1}/{iterations} completed: tpmC={score:.2f} | Time: {elapsed:.1f}s")
        
        return self.best_params, self.best_score, results

    def save_best_config(self, filename="best_sysctl.conf"):
        """保存最佳配置到文件"""
        if not self.best_params:
            logging.error("No best parameters to save")
            return False
        
        try:
            with open(filename, "w") as f:
                for name, value in self.best_params.items():
                    f.write(f"{name} = {value}\n")
            logging.info(f"Saved best configuration to {filename}")
            return True
        except Exception as e:
            logging.error(f"Failed to save configuration: {str(e)}")
            return False

    def save_default_config(self, filename="default_sysctl.conf"):
        """保存默认配置到文件"""
        try:
            with open(filename, "w") as f:
                for name, value in self.baseline_values.items():
                    f.write(f"{name} = {value}\n")
            logging.info(f"Saved default configuration to {filename}")
            return True
        except Exception as e:
            logging.error(f"Failed to save default configuration: {str(e)}")
            return False

    def restore_default_config(self):
        """请求服务器端恢复默认配置"""
        try:
            response = requests.post(f"{self.server_url}/restore_default_config")
            response.raise_for_status()
            result = response.json()
            if result.get("status") == "success":
                logging.info("Default configuration restored successfully on server")
                return True
            else:
                logging.error("Failed to restore default configuration on server")
                return False
        except requests.RequestException as e:
            logging.error(f"Error restoring default configuration on server: {str(e)}")
            return False

def validate_script(script_path):
    """验证脚本是否存在且可执行"""
    if not os.path.exists(script_path):
        logging.error(f"Script not found: {script_path}")
        return False
    
    if not os.access(script_path, os.X_OK):
        logging.error(f"Script is not executable: {script_path}")
        return False
    
    logging.info(f"Script validated: {script_path}")
    return True

def main():
    logging.info("Starting sysctl optimizer")
   
    # 验证所有必要的脚本
    scripts_to_validate = [
        CONFIG["destroy_script"],
        CONFIG["build_script"],
        CONFIG["benchmark_script"]
    ]
    
    for script in scripts_to_validate:
        if not validate_script(script):
            logging.error(f"Validation failed for script: {script}")
            return
    
    # 验证属性文件是否存在
    if not os.path.exists(CONFIG["props_file"]):
        logging.error(f"Properties file not found: {CONFIG['props_file']}")
        return
    
    # 初始化优化器
    optimizer = SysctlOptimizer(PARAM_CONFIG, CONFIG)
    
    # 保存当前系统配置为默认值
    optimizer.save_default_config()
    
    try:
        logging.info("Running baseline configuration...")
        baseline_score = optimizer.evaluate_params(optimizer.baseline_values)
        logging.info(f"Baseline tpmC: {baseline_score:.2f}")
        
        if baseline_score == 0.0:
            logging.error("Baseline benchmark failed. Aborting optimization.")
            return
        
        # 运行随机搜索优化
        best_params, best_score, results = optimizer.random_search(iterations=CONFIG["iterations"])
        
        logging.info("\nOptimization completed!")
        logging.info(f"Best tpmC: {best_score:.2f}")
        logging.info("Best parameters:")
        for name, value in best_params.items():
            logging.info(f"{name}: {value}")
        
        # 保存最佳配置
        optimizer.save_best_config()
        
        # 保存所有结果
        try:
            with open("optimization_results.csv", "w") as f:
                f.write("iteration,param_set,tpmC,time\n")
                for i, (params, score, elapsed) in enumerate(results):
                    param_str = ";".join([f"{k}={v}" for k, v in params.items()])
                    f.write(f"{i},{param_str},{score},{elapsed}\n")
            logging.info("Saved optimization results to optimization_results.csv")
        except Exception as e:
            logging.error(f"Failed to save results: {str(e)}")
    finally:
        # 无论优化是否成功，最后都恢复默认配置
        optimizer.restore_default_config()

if __name__ == "__main__":
    main()
