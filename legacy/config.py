# -*- coding: utf-8 -*-
"""
config.py —— 读配置文件 config.ini，返回一个嵌套字典。

优先级总原则（高 -> 低）：
    命令行参数  >  环境变量  >  config.ini  >  代码里的默认值 / llm.py 常量

想自己换模型 / 接口 / Key？直接编辑 config.ini 即可（不用改任何代码），
保存后重新运行。本文件不用你管 —— 看懂 load_config() 怎么「兜底」就够了。
"""

from __future__ import annotations

import configparser
import os

# config.ini 的位置：和本文件同一个目录
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

# 默认值兜底：就算 config.ini 被删了 / 缺字段，程序也能用默认配置跑起来
_DEFAULTS = {
    "model": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "temperature": "0.7",
    },
    "agent": {
        "max_rounds": "8",
    },
    "research": {
        # 查资料类任务（deep-research 技能 / 流水线研究员）的独立轮数预算：
        # 联网检索天生要多轮，默认给大预算；普通任务仍用 [agent] max_rounds
        "max_rounds": "20",
        # Fan-out 并行研究员的最大并行数（master 拆子题并行研究时生效）
        "parallel": "3",
    },
}


def load_config() -> dict[str, dict[str, str]]:
    """把 config.ini 读成 {分区: {键: 字符串}}，缺什么补什么默认值。"""
    result = {section: dict(keys) for section, keys in _DEFAULTS.items()}
    parser = configparser.ConfigParser(interpolation=None)  # 不需要 % 变量插值
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            parser.read_file(f)
    except FileNotFoundError:
        return result  # 没有配置文件也不报错，全部用默认值
    for section in result:      # 逐分区、逐键覆盖：文件里写了什么就用什么
        if parser.has_section(section):
            for key in result[section]:
                if parser.has_option(section, key):
                    result[section][key] = parser.get(section, key).strip()
    return result
