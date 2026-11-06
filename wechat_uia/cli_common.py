"""命令行参数解析的共用小工具。"""

import argparse


def positive_int(value: str) -> int:
    """argparse 类型：必须是大于 0 的整数。"""
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def runtime_id(value: str) -> tuple[int, ...]:
    """argparse 类型：逗号分隔的 RuntimeId 整数序列。"""
    try:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError
        return tuple(int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "RuntimeId 必须是逗号分隔的整数"
        ) from exc
