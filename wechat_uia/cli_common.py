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


def add_surface_arguments(parser: argparse.ArgumentParser) -> None:
    """给诊断命令补上通用的窗口选择参数。"""
    parser.add_argument(
        "--chat",
        help="可选：按会话名选择窗口；不会切换会话",
    )
    parser.add_argument(
        "--hwnd",
        type=lambda value: int(value, 0),
        help="可选：目标窗口句柄；支持十进制或 0x 十六进制",
    )
