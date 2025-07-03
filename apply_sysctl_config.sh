#!/bin/bash

# 定义配置文件路径
CONFIG_FILE=$1

# 检查文件是否存在
if [ ! -f "$CONFIG_FILE" ]; then
    echo "配置文件 $CONFIG_FILE 不存在"
    exit 1
fi

# 逐行读取配置文件并设置参数
while IFS='=' read -r key value; do
    # 去除首尾空格
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs)
    
    # 使用 sysctl 设置参数
    sudo sysctl -w "$key=$value"
done < "$CONFIG_FILE"
sysctl -p
echo "所有参数已设置完成"
