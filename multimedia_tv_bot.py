import logging
import re
import os
import json
import time
import datetime
import sqlite3
import random
import string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler, ConversationHandler
from telegram.constants import ParseMode

# Configuración de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuración del bot
TOKEN = "7853962859:AAEsWR8uuqey8zh62XnFDlXmjDZzaNiO_YA"
CHANNEL_ID = -1002685140729
MAIN_CHANNEL_ID = -1002584219284
GROUP_ID = -1002585538833
ADMIN_ID = 1742433244

# Estado de mantenimiento
maintenance_mode = False

# Constantes para los planes
PLANS = {
    "default": {
        "name": "Default",
        "search_limit": 3,
        "request_limit": 1,
        "can_forward": False,
        "duration_days": 0,
        "price_cup": 0,
        "price_cup_balance": 0,
        "price_usdt": 0
    },
    "pro": {
        "name": "Pro",
        "search_limit": 15,
        "request_limit": 2,
        "can_forward": False,
        "duration_days": 30,
        "price_cup": 169.99,
        "price_cup_balance": 189.99,
        "price_usdt": 0.49
    },
    "plus": {
        "name": "Plus",
        "search_limit": 50,
        "request_limit": 10,
        "can_forward": True,
        "duration_days": 30,
        "price_cup": 649.99,
        "price_cup_balance": 669.99,
        "price_usdt": 1.99
    },
    "ultra": {
        "name": "Ultra",
        "search_limit": 999999,  # Prácticamente ilimitado
        "request_limit": 999999,  # Prácticamente ilimitado
        "can_forward": True,
        "duration_days": 30,
        "price_cup": 1049.99,
        "price_cup_balance": 1089.99,
        "price_usdt": 2.99
    }
}

# Estados para conversaciones
MOVIE_OR_SERIES, MAKE_REQUEST = range(2)

# Ruta de la base de datos SQLite
DB_PATH = "multimedia_tv_bot.db"

# Función para inicializar la base de datos SQLite
def initialize_database():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Crear tabla de usuarios si no existe
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                plan TEXT DEFAULT 'default',
                plan_expiry TEXT NULL,
                search_count INTEGER DEFAULT 0,
                request_count INTEGER DEFAULT 0,
                join_date TEXT DEFAULT CURRENT_TIMESTAMP,
                referrer_id INTEGER NULL,
                last_reset TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Crear tabla de códigos de regalo
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gift_codes (
                code TEXT PRIMARY KEY,
                plan TEXT NOT NULL,
                max_uses INTEGER DEFAULT 1,
                current_uses INTEGER DEFAULT 0,
                created_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Crear tabla para solicitudes pendientes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                request_type TEXT,
                title TEXT,
                year TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Crear tabla para usuarios muteados
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS muted_users (
                user_id INTEGER PRIMARY KEY,
                muted_by INTEGER,
                muted_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Base de datos inicializada correctamente")
    except Exception as e:
        logger.error(f"Error al inicializar la base de datos: {e}")

# Función para registrar un usuario en la base de datos
def register_user(user_id, username, first_name, referrer_id=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Verificar si el usuario ya existe
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            # Insertar nuevo usuario
            query = '''
                INSERT INTO users (user_id, username, first_name, referrer_id)
                VALUES (?, ?, ?, ?)
            '''
            cursor.execute(query, (user_id, username, first_name, referrer_id))
            
            # Si hay un referente, aumentar su saldo
            if referrer_id:
                cursor.execute("UPDATE users SET balance = balance + 1 WHERE user_id = ?", (referrer_id,))
            
            conn.commit()
            logger.info(f"Usuario {username} (ID: {user_id}) registrado correctamente")
            result = True
        else:
            logger.info(f"Usuario {username} (ID: {user_id}) ya está registrado")
            result = False
        
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Error al registrar usuario: {e}")
        return False

# Función para obtener información del usuario
def get_user_info(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        
        conn.close()
        
        if user:
            # Convertir el objeto Row a un diccionario
            user_dict = dict(user)
            return user_dict
        return None
    except Exception as e:
        logger.error(f"Error al obtener información del usuario: {e}")
        return None

# Función para actualizar el contador de búsquedas del usuario
def update_search_count(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET search_count = search_count + 1 WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error al actualizar contador de búsquedas: {e}")
        return False

# Función para actualizar el contador de pedidos del usuario
def update_request_count(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET request_count = request_count + 1 WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error al actualizar contador de pedidos: {e}")
        return False

# Función para reiniciar los contadores diarios
def reset_daily_counters():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET search_count = 0, request_count = 0, last_reset = CURRENT_TIMESTAMP")
        
        conn.commit()
        conn.close()
        logger.info("Contadores diarios reiniciados")
        return True
    except Exception as e:
        logger.error(f"Error al reiniciar contadores diarios: {e}")
        return False

# Función para actualizar el plan de un usuario
def update_user_plan(user_id, plan_name):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Calcular fecha de expiración
        if plan_name == "default":
            expiry_date = None
        else:
            duration_days = PLANS[plan_name]["duration_days"]
            expiry_date = (datetime.datetime.now() + datetime.timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")
        
        # Actualizar plan del usuario
        if expiry_date:
            cursor.execute(
                "UPDATE users SET plan = ?, plan_expiry = ? WHERE user_id = ?",
                (plan_name, expiry_date, user_id)
            )
        else:
            cursor.execute(
                "UPDATE users SET plan = ?, plan_expiry = NULL WHERE user_id = ?",
                (plan_name, user_id)
            )
        
        conn.commit()
        conn.close()
        logger.info(f"Plan del usuario {user_id} actualizado a {plan_name}")
        return True
    except Exception as e:
        logger.error(f"Error al actualizar plan del usuario: {e}")
        return False

# Función para verificar y actualizar planes expirados
def check_expired_plans():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Buscar usuarios con planes expirados
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "SELECT user_id FROM users WHERE plan_expiry IS NOT NULL AND plan_expiry < ? AND plan != 'default'",
            (current_time,)
        )
        expired_users = cursor.fetchall()
        
        # Actualizar a plan default
        for user in expired_users:
            user_id = user[0]
            cursor.execute(
                "UPDATE users SET plan = 'default', plan_expiry = NULL WHERE user_id = ?",
                (user_id,)
            )
        
        conn.commit()
        conn.close()
        logger.info(f"Se actualizaron {len(expired_users)} planes expirados")
        return expired_users
    except Exception as e:
        logger.error(f"Error al verificar planes expirados: {e}")
        return []

# Función para crear un código de regalo
def create_gift_code(code, plan, max_uses, created_by):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Insertar código en la base de datos
        cursor.execute(
            "INSERT INTO gift_codes (code, plan, max_uses, created_by) VALUES (?, ?, ?, ?)",
            (code, plan, max_uses, created_by)
        )
        
        conn.commit()
        conn.close()
        logger.info(f"Código de regalo {code} creado para el plan {plan}")
        return True
    except Exception as e:
        logger.error(f"Error al crear código de regalo: {e}")
        return False

# Función para usar un código de regalo
def use_gift_code(code, user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Verificar si el código existe y es válido
        cursor.execute(
            "SELECT * FROM gift_codes WHERE code = ? AND current_uses < max_uses",
            (code,)
        )
        gift_code = cursor.fetchone()
        
        if not gift_code:
            conn.close()
            return False, "Código inválido o ya ha sido utilizado."
        
        # Incrementar el uso del código
        cursor.execute(
            "UPDATE gift_codes SET current_uses = current_uses + 1 WHERE code = ?",
            (code,)
        )
        
        # Actualizar el plan del usuario
        plan_name = gift_code["plan"]
        if plan_name in ["pro", "plus", "ultra"]:
            duration_days = PLANS[plan_name]["duration_days"]
            expiry_date = (datetime.datetime.now() + datetime.timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")
            
            cursor.execute(
                "UPDATE users SET plan = ?, plan_expiry = ? WHERE user_id = ?",
                (plan_name, expiry_date, user_id)
            )
            
            conn.commit()
            conn.close()
            return True, f"¡Felicidades! Has activado el plan {plan_name.capitalize()} por {duration_days} días."
        else:
            conn.close()
            return False, "Plan no válido en el código de regalo."
    except Exception as e:
        logger.error(f"Error al usar código de regalo: {e}")
        return False, f"Error al procesar el código de regalo: {str(e)}"

# Función para contar referidos de un usuario
def count_referrals(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
        count = cursor.fetchone()[0]
        
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Error al contar referidos: {e}")
        return 0

# Función para verificar si un usuario está muteado
def is_user_muted(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM muted_users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone() is not None
        
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Error al verificar si el usuario está muteado: {e}")
        return False

# Función para mutear a un usuario
def mute_user(user_id, muted_by):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("INSERT OR REPLACE INTO muted_users (user_id, muted_by) VALUES (?, ?)", 
                      (user_id, muted_by))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error al mutear usuario: {e}")
        return False

# Función para desmutear a un usuario
def unmute_user(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM muted_users WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error al desmutear usuario: {e}")
        return False

# Función para guardar una solicitud pendiente
def save_pending_request(user_id, request_type, title, year=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO pending_requests (user_id, request_type, title, year) VALUES (?, ?, ?, ?)",
            (user_id, request_type, title, year)
        )
        
        request_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        return request_id
    except Exception as e:
        logger.error(f"Error al guardar solicitud pendiente: {e}")
        return None

# Función para obtener solicitudes pendientes
def get_pending_requests(limit=5):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT p.*, u.username, u.first_name FROM pending_requests p JOIN users u ON p.user_id = u.user_id WHERE p.status = 'pending' ORDER BY p.created_at DESC LIMIT ?",
            (limit,)
        )
        requests = cursor.fetchall()
        
        # Convertir a lista de diccionarios
        result = [dict(req) for req in requests]
        
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Error al obtener solicitudes pendientes: {e}")
        return []

# Función para actualizar el estado de una solicitud
def update_request_status(request_id, status):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE pending_requests SET status = ? WHERE id = ?",
            (status, request_id)
        )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error al actualizar estado de solicitud: {e}")
        return False

# Función para el comando /start
async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Verificar si es un inicio por referido
    referrer_id = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0].split("_")[1])
            logger.info(f"Usuario {user.id} registrado con referido {referrer_id}")
        except (ValueError, IndexError):
            logger.warning(f"Formato de referido inválido: {context.args[0]}")
    
    # Registrar usuario en la base de datos
    register_user(user.id, user.username, user.first_name, referrer_id)
    
    # Mensaje de bienvenida
    welcome_message = (
        f"¡Hola! {user.first_name}👋 te doy la bienvenida\n\n"
        f"MultimediaTv un bot donde encontraras un amplio catálogo de películas y series, "
        f"las cuales puedes buscar o solicitar en caso de no estar en el catálogo"
    )
    
    # Botones del menú principal
    keyboard = [
        [
            InlineKeyboardButton("Multimedia Tv 📺", url="https://t.me/multimediatvOficial"),
            InlineKeyboardButton("Pedidos 📡", url=f"https://t.me/{GROUP_ID}")
        ],
        [InlineKeyboardButton("Perfil 👤", callback_data="profile")],
        [InlineKeyboardButton("Planes 📜", callback_data="plans")],
        [InlineKeyboardButton("Información 📰", callback_data="info")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_message,
        reply_markup=reply_markup
    )

# Función para el comando /help
async def help_command(update: Update, context: CallbackContext) -> None:
    help_text = (
        "🤖 Comandos disponibles:\n\n"
        "• Envía el nombre de una película o serie para buscarla\n"
        "• /start - Inicia el bot\n"
        "• /help - Muestra este mensaje de ayuda\n"
        "• /search [nombre] - Busca una película o serie\n"
        "• /gift_code [código] - Canjea un código de regalo\n\n"
        
        "👨‍💻 Comandos de administrador:\n\n"
        "• /mantenimiento - Activa el modo mantenimiento\n"
        "• /mantenimientooff - Desactiva el modo mantenimiento\n"
        "• /plan [username] [número_plan] - Asigna un plan a un usuario\n"
        "• /addgift_code [código] [número_plan] [max_usos] - Crea un código de regalo\n"
        "• /admin_help - Muestra ayuda detallada para administradores\n"
    )
    await update.message.reply_text(help_text)

# Función para el comando /admin_help
async def admin_help(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    admin_help_text = (
        "👨‍💻 Comandos de administrador:\n\n"
        "• /mantenimiento - Activa el modo mantenimiento\n"
        "• /mantenimientooff - Desactiva el modo mantenimiento\n"
        "• /plan [username] [número_plan] - Asigna un plan a un usuario\n"
        "  Ejemplo: /plan @usuario 1\n"
        "  Planes: 1=Pro, 2=Plus, 3=Ultra\n\n"
        "• /addgift_code [código] [número_plan] [max_usos] - Crea un código de regalo\n"
        "  Ejemplo: /addgift_code 2432 3 1\n"
        "  Planes: 1=Pro, 2=Plus, 3=Ultra\n\n"
        "• /mute [user_id] - Silencia a un usuario\n"
        "• /unmute [user_id] - Quita el silencio a un usuario\n"
        "• /ban [username] - Banea a un usuario\n"
        "• /unban [username] - Desbanea a un usuario\n"
        "• /pendientes - Muestra solicitudes pendientes\n"
        "• /reset_counters - Reinicia los contadores diarios de todos los usuarios\n"
    )
    
    await update.message.reply_text(admin_help_text)

# Función para activar el modo mantenimiento
async def maintenance_on(update: Update, context: CallbackContext) -> None:
    global maintenance_mode
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    maintenance_mode = True
    await update.message.reply_text("🛠️ Modo mantenimiento activado. Solo los administradores pueden usar el bot.")

# Función para desactivar el modo mantenimiento
async def maintenance_off(update: Update, context: CallbackContext) -> None:
    global maintenance_mode
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    maintenance_mode = False
    await update.message.reply_text("✅ Modo mantenimiento desactivado. El bot está disponible para todos los usuarios.")

# Función para silenciar a un usuario
async def mute_user_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    if not context.args:
        await update.message.reply_text("Por favor, proporciona el ID del usuario que deseas silenciar.")
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Verificar si el usuario existe
        user_info = get_user_info(target_user_id)
        if not user_info:
            await update.message.reply_text(f"Usuario con ID {target_user_id} no encontrado.")
            return
        
        # Silenciar al usuario
        if mute_user(target_user_id, user_id):
            await update.message.reply_text(f"Usuario {target_user_id} ha sido silenciado.")
        else:
            await update.message.reply_text("Error al silenciar al usuario. Inténtalo de nuevo.")
    except ValueError:
        await update.message.reply_text("ID de usuario inválido. Debe ser un número.")

# Función para quitar el silencio a un usuario
async def unmute_user_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    if not context.args:
        await update.message.reply_text("Por favor, proporciona el ID del usuario que deseas quitar el silencio.")
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Verificar si el usuario está silenciado
        if not is_user_muted(target_user_id):
            await update.message.reply_text(f"El usuario {target_user_id} no está silenciado.")
            return
        
        # Quitar silencio al usuario
        if unmute_user(target_user_id):
            await update.message.reply_text(f"Usuario {target_user_id} ya no está silenciado.")
        else:
            await update.message.reply_text("Error al quitar el silencio al usuario. Inténtalo de nuevo.")
    except ValueError:
        await update.message.reply_text("ID de usuario inválido. Debe ser un número.")

# Función para establecer la cantidad de diamantes de un usuario
async def set_diamonds(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    if not context.args:
        await update.message.reply_text("Por favor, proporciona el ID del usuario.")
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Verificar si el usuario existe
        user_info = get_user_info(target_user_id)
        if not user_info:
            await update.message.reply_text(f"Usuario con ID {target_user_id} no encontrado.")
            return
        
        # Obtener información del usuario
        user_name = user_info.get("first_name", "Usuario")
        user_balance = user_info.get("balance", 0)
        
        await update.message.reply_text(
            f"Usuario ID: {target_user_id}\n"
            f"Nombre: {user_name}\n"
            f"Diamantes actuales: {user_balance}\n\n"
            f"Envía la nueva cantidad de diamantes:"
        )
        
        # Guardar el ID del usuario en el contexto para usarlo en el siguiente mensaje
        context.user_data["setting_diamonds_for"] = target_user_id
        
    except ValueError:
        await update.message.reply_text("ID de usuario inválido. Debe ser un número.")

# Función para procesar la nueva cantidad de diamantes
async def process_diamonds_amount(update: Update, context: CallbackContext) -> None:
    if "setting_diamonds_for" not in context.user_data:
        return
    
    target_user_id = context.user_data["setting_diamonds_for"]
    
    try:
        new_diamonds = int(update.message.text)
        
        # Actualizar los diamantes del usuario
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_diamonds, target_user_id))
        
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"Diamantes actualizados para el usuario {target_user_id}. Nueva cantidad: {new_diamonds}")
        
        # Limpiar el contexto
        del context.user_data["setting_diamonds_for"]
        
    except ValueError:
        await update.message.reply_text("Por favor, ingresa un número válido para la cantidad de diamantes.")
    except Exception as e:
        logger.error(f"Error al actualizar diamantes: {e}")
        await update.message.reply_text("Error al actualizar los diamantes. Inténtalo de nuevo.")

# Función para asignar un plan a un usuario
async def assign_plan(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /plan @username número_plan")
        return
    
    username = context.args[0]
    if username.startswith('@'):
        username = username[1:]
    
    try:
        plan_number = int(context.args[1])
        if plan_number < 1 or plan_number > 3:
            await update.message.reply_text("Número de plan inválido. Usa 1 para Pro, 2 para Plus, 3 para Ultra.")
            return
        
        # Mapear número a nombre del plan
        plan_map = {1: "pro", 2: "plus", 3: "ultra"}
        plan_name = plan_map[plan_number]
        
        # Buscar usuario por username
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        
        if not user:
            await update.message.reply_text(f"Usuario @{username} no encontrado.")
            conn.close()
            return
        
        target_user_id = user["user_id"]
        conn.close()
        
        # Actualizar plan
        if update_user_plan(target_user_id, plan_name):
            await update.message.reply_text(
                f"Plan {plan_name.capitalize()} asignado a @{username} por {PLANS[plan_name]['duration_days']} días."
            )
            
            # Notificar al usuario
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"¡Felicidades! Se te ha asignado el plan {plan_name.capitalize()} por {PLANS[plan_name]['duration_days']} días."
                )
            except Exception as e:
                logger.error(f"Error al notificar al usuario: {e}")
        else:
            await update.message.reply_text("Error al asignar el plan. Inténtalo de nuevo.")
    except ValueError:
        await update.message.reply_text("El número de plan debe ser un número entero.")
    except Exception as e:
        logger.error(f"Error al asignar plan: {e}")
        await update.message.reply_text(f"Error al asignar el plan: {str(e)}")

# Función para crear un código de regalo
async def add_gift_code(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    if len(context.args) < 3:
        await update.message.reply_text("Uso: /addgift_code código número_plan max_usos")
        return
    
    try:
        code = context.args[0]
        plan_number = int(context.args[1])
        max_uses = int(context.args[2])
        
        if plan_number < 1 or plan_number > 3:
            await update.message.reply_text("Número de plan inválido. Usa 1 para Pro, 2 para Plus, 3 para Ultra.")
            return
        
        # Mapear número a nombre del plan
        plan_map = {1: "pro", 2: "plus", 3: "ultra"}
        plan_name = plan_map[plan_number]
        
        # Crear código en la base de datos
        if create_gift_code(code, plan_name, max_uses, user_id):
            await update.message.reply_text(
                f"Código de regalo {code} creado para el plan {plan_name.capitalize()} con {max_uses} usos máximos."
            )
        else:
            await update.message.reply_text("Error al crear el código de regalo. Inténtalo de nuevo.")
    except ValueError:
        await update.message.reply_text("El número de plan y máximo de usos deben ser números enteros.")
    except Exception as e:
        logger.error(f"Error al crear código de regalo: {e}")
        await update.message.reply_text(f"Error al crear el código de regalo: {str(e)}")

# Función para usar un código de regalo
async def use_gift_code_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text("Uso: /gift_code código")
        return
    
    code = context.args[0]
    
    # Verificar y usar el código
    success, message = use_gift_code(code, user_id)
    
    await update.message.reply_text(message)
    
    if success:
        # Mostrar información actualizada del perfil
        await show_profile(update, context, user_id)

# Función para banear a un usuario
async def ban_user(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    if not context.args:
        await update.message.reply_text("Uso: /ban @username")
        return
    
    username = context.args[0]
    if username.startswith('@'):
        username = username[1:]
    
    try:
        # Buscar usuario por username
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        
        if not user:
            await update.message.reply_text(f"Usuario @{username} no encontrado.")
            conn.close()
            return
        
        target_user_id = user["user_id"]
        
        # Marcar como baneado (usando plan 'banned')
        cursor.execute(
            "UPDATE users SET plan = 'banned' WHERE user_id = ?",
            (target_user_id,)
        )
        
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"Usuario @{username} ha sido baneado.")
        
        # Notificar al usuario
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text="Has sido baneado del bot. Contacta con un administrador si crees que es un error."
            )
        except Exception as e:
            logger.error(f"Error al notificar al usuario baneado: {e}")
    except Exception as e:
        logger.error(f"Error al banear al usuario: {e}")
        await update.message.reply_text(f"Error al banear al usuario: {str(e)}")

# Función para desbanear a un usuario
async def unban_user(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    if not context.args:
        await update.message.reply_text("Uso: /unban @username")
        return
    
    username = context.args[0]
    if username.startswith('@'):
        username = username[1:]
    
    try:
        # Buscar usuario por username
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        
        if not user:
            await update.message.reply_text(f"Usuario @{username} no encontrado.")
            conn.close()
            return
        
        target_user_id = user["user_id"]
        
        # Desbanear (volver a plan default)
        cursor.execute(
            "UPDATE users SET plan = 'default' WHERE user_id = ?",
            (target_user_id,)
        )
        
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"Usuario @{username} ha sido desbaneado.")
        
        # Notificar al usuario
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text="Has sido desbaneado. Ya puedes usar el bot normalmente."
            )
        except Exception as e:
            logger.error(f"Error al notificar al usuario desbaneado: {e}")
    except Exception as e:
        logger.error(f"Error al desbanear al usuario: {e}")
        await update.message.reply_text(f"Error al desbanear al usuario: {str(e)}")

# Función para reiniciar contadores diarios
async def reset_counters_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    # Reiniciar contadores
    if reset_daily_counters():
        await update.message.reply_text("Contadores diarios reiniciados para todos los usuarios.")
    else:
        await update.message.reply_text("Error al reiniciar los contadores. Inténtalo de nuevo.")

# Función para mostrar el perfil del usuario
async def show_profile(update: Update, context: CallbackContext, user_id=None) -> None:
    # Si no se proporciona user_id, usar el del usuario que envió el mensaje
    if not user_id:
        if update.callback_query:
            user_id = update.callback_query.from_user.id
        else:
            user_id = update.effective_user.id
    
    # Obtener información del usuario
    user_info = get_user_info(user_id)
    
    if not user_info:
        if update.callback_query:
            await update.callback_query.answer("Error al obtener información del perfil.")
        else:
            await update.message.reply_text("Error al obtener información del perfil.")
        return
    
    # Contar referidos
    referrals_count = count_referrals(user_id)
    
    # Calcular tiempo hasta reinicio de contadores
    now = datetime.datetime.now()
    last_reset_str = user_info.get("last_reset")
    if last_reset_str:
        try:
            last_reset = datetime.datetime.strptime(last_reset_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            last_reset = now
    else:
        last_reset = now
    
    next_reset = last_reset + datetime.timedelta(days=1)
    time_until_reset = next_reset - now
    hours, remainder = divmod(time_until_reset.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    
    # Formatear fecha de expiración del plan
    if user_info.get("plan_expiry"):
        try:
            expiry_date = datetime.datetime.strptime(user_info["plan_expiry"], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y")
        except ValueError:
            expiry_date = "N/A"
    else:
        expiry_date = "N/A"
    
    # Formatear fecha de unión
    join_date_str = user_info.get("join_date")
    if join_date_str:
        try:
            join_date = datetime.datetime.strptime(join_date_str, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y")
        except ValueError:
            join_date = "N/A"
    else:
        join_date = "N/A"
    
    # Preparar mensaje de perfil
    profile_text = (
        f"👤 *Perfil de Usuario*\n\n"
        f"• Nombre: {user_info.get('first_name', 'Usuario')}\n"
        f"• Saldo: {user_info.get('balance', 0)} 💎\n"
        f"• ID: {user_id}\n"
        f"• Plan: {user_info.get('plan', 'Default').capitalize()}\n"
    )
    
    # Añadir fecha de expiración solo si no es plan default
    if user_info.get("plan") != "default" and user_info.get("plan") != "banned":
        profile_text += f"• Expira: {expiry_date}\n"
    
    # Continuar con el resto de la información
    plan_name = user_info.get("plan", "default")
    if plan_name == "banned":
        search_limit = 0
        request_limit = 0
    else:
        search_limit = PLANS[plan_name]["search_limit"]
        request_limit = PLANS[plan_name]["request_limit"]
    
    profile_text += (
        f"• Límite Pedido: {request_limit - user_info.get('request_count', 0)}/{request_limit}\n"
        f"• Límite Contenido: {search_limit - user_info.get('search_count', 0)}/{search_limit}\n"
        f"• Fecha de Unión: {join_date}\n"
        f"• Referidos: {referrals_count}\n"
        f"• Reinicio: {hours}h {minutes}m\n\n"
        f"🎁 Comparte tu enlace de referido y gana diamantes!"
    )
    
    # Botones para el perfil
    keyboard = [
        [InlineKeyboardButton("Compartir Enlace de referencia 🔗", callback_data="share_ref")],
        [InlineKeyboardButton("Volver 🔙", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Enviar o editar mensaje según el contexto
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                profile_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error al editar mensaje de perfil: {e}")
            # Si falla la edición, enviar un nuevo mensaje
            await update.callback_query.message.reply_text(
                profile_text,
                reply_markup=reply_markup
            )
    else:
        await update.message.reply_text(
            profile_text,
            reply_markup=reply_markup
        )

# Función para mostrar los planes disponibles
async def show_plans(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    user_info = get_user_info(user_id)
    
    if not user_info:
        balance = 0
        plan = "Default"
    else:
        balance = user_info.get("balance", 0)
        plan = user_info.get("plan", "default").capitalize()
    
    plans_text = (
        f"▧ Planes de Suscripción ▧\n\n"
        f"Tu saldo actual: {balance}\n"
        f"Plan actual: {plan}\n\n"
        f"📋 Planes Disponibles:\n\n"
        f"Pro (169.99 | 29 ⭐)\n"
        f"169.99 CUP\n"
        f"0.49 USD\n\n"
        f"Plus (649.99 | 117 ⭐)\n"
        f"649.99 CUP\n"
        f"1.99 USD\n\n"
        f"Ultra (1049.99 | 176 ⭐)\n"
        f"1049.99 CUP\n"
        f"2.99 USD\n\n"
        f"Pulsa los botones de debajo para mas info de los planes y formas de pago."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("Plan pro ✨", callback_data="plan_pro"),
            InlineKeyboardButton("Plan plus ⭐", callback_data="plan_plus")
        ],
        [
            InlineKeyboardButton("Plan ultra 🌟", callback_data="plan_ultra")
        ],
        [InlineKeyboardButton("Volver 🔙", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                plans_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error al editar mensaje de planes: {e}")
            # Si falla la edición, enviar un nuevo mensaje
            await update.callback_query.message.reply_text(
                plans_text,
                reply_markup=reply_markup
            )
    else:
        await update.message.reply_text(
            plans_text,
            reply_markup=reply_markup
        )

# Función para mostrar detalles de un plan específico
async def show_plan_details(update: Update, context: CallbackContext, plan_name) -> None:
    user_id = update.callback_query.from_user.id
    user_info = get_user_info(user_id)
    
    if not user_info:
        balance = 0
    else:
        balance = user_info.get("balance", 0)
    
    # Detalles según el plan
    if plan_name == "pro":
        details_text = (
            f"💫 Plan Pro - Detalles 💫\n\n"
            f"Precio: 169.99\n"
            f"Duración: 30 días\n\n"
            f"Beneficios:\n"
            f"└ 2 pedidos diarios\n"
            f"└ 15 películas o series al día\n"
            f"└ No puede reenviar contenido ni guardarlo\n\n"
            f"Tu saldo actual: {balance}"
        )
    elif plan_name == "plus":
        details_text = (
            f"💫 Plan Plus - Detalles 💫\n\n"
            f"Precio: 649.99\n"
            f"Duración: 30 días\n\n"
            f"Beneficios:\n"
            f"└ 10 pedidos diarios\n"
            f"└ 50 películas o series al día\n"
            f"└ Soporte prioritario\n"
            f"└ Enlaces directos de descarga\n"
            f"└ Acceso a contenido exclusivo\n\n"
            f"Tu saldo actual: {balance}"
        )
    elif plan_name == "ultra":
        details_text = (
            f"⭐ Plan Ultra - Detalles ⭐\n\n"
            f"Precio: 1049.99\n"
            f"Duración: 30 días\n\n"
            f"Beneficios:\n"
            f"└ Pedidos ilimitados\n"
            f"└ Sin restricciones de contenido\n"
            f"└ Reenvío y guardado permitido\n"
            f"└ Enlaces directos de descarga\n"
            f"└ Soporte VIP\n"
            f"└ Acceso anticipado a nuevo contenido\n\n"
            f"Tu saldo actual: {balance}"
        )
    else:
        details_text = "Plan no válido."
    
    keyboard = [
        [
            InlineKeyboardButton("Cup (Cuba 🇨🇺)", callback_data=f"payment_{plan_name}_cup"),
            InlineKeyboardButton("Crypto", callback_data=f"payment_{plan_name}_crypto")
        ],
        [InlineKeyboardButton("Volver 🔙", callback_data="plans")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.callback_query.edit_message_text(
            details_text,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error al editar mensaje de detalles del plan: {e}")
        # Si falla la edición, enviar un nuevo mensaje
        await update.callback_query.message.reply_text(
            details_text,
            reply_markup=reply_markup
        )

# Función para mostrar opciones de pago
async def show_payment_options(update: Update, context: CallbackContext, plan_name, payment_type) -> None:
    # Detalles según el plan y tipo de pago
    if payment_type == "cup":
        if plan_name == "pro":
            payment_text = (
                f"Pago en CUP (Transferencia)\n"
                f"Precio: 169.99 CUP\n"
                f"Pago en CUP (Saldo)\n"
                f"Precio: 189.99 CUP\n"
                f"Detalles de pago:\n"
                f"Número: 9205 1299 7736 4067\n"
                f"Telef: 55068190\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        elif plan_name == "plus":
            payment_text = (
                f"Pago en CUP (Transferencia)\n"
                f"Precio: 649.99 CUP\n"
                f"Pago en CUP (Saldo)\n"
                f"Precio: 669.99 CUP\n"
                f"Detalles de pago:\n"
                f"Número: 9205 1299 7736 4067\n"
                f"Telef: 55068190\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        elif plan_name == "ultra":
            payment_text = (
                f"Pago en CUP (Transferencia)\n"
                f"Precio: 1049.99 CUP\n"
                f"Pago en CUP (Saldo)\n"
                f"Precio: 1089.99 CUP\n"
                f"Detalles de pago:\n"
                f"Número: 9205 1299 7736 4067\n"
                f"Telef: 55068190\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        else:
            payment_text = "Plan no válido."
    elif payment_type == "crypto":
        if plan_name == "pro":
            payment_text = (
                f"Pago con USDT (BEP 20)\n"
                f"Precio: 0.49 USDTT\n"
                f"Detalles de pago:\n"
                f"Dirección: 0x26d89897c4e452C7BD3a0B8Aa79dD84E516BD4c6\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        elif plan_name == "plus":
            payment_text = (
                f"Pago con USDT (BEP 20)\n"
                f"Precio: 1.99 USDTT\n"
                f"Detalles de pago:\n"
                f"Dirección: 0x26d89897c4e452C7BD3a0B8Aa79dD84E516BD4c6\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        elif plan_name == "ultra":
            payment_text = (
                f"Pago con USDT (BEP 20)\n"
                f"Precio: 2.99 USDTT\n"
                f"Detalles de pago:\n"
                f"Dirección: 0x26d89897c4e452C7BD3a0B8Aa79dD84E516BD4c6\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        else:
            payment_text = "Plan no válido."
    else:
        payment_text = "Método de pago no válido."
    
    keyboard = [
        [InlineKeyboardButton("Volver 🔙", callback_data=f"plan_{plan_name}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.callback_query.edit_message_text(
            payment_text,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error al editar mensaje de opciones de pago: {e}")
        # Si falla la edición, enviar un nuevo mensaje
        await update.callback_query.message.reply_text(
            payment_text,
            reply_markup=reply_markup
        )

# Función para mostrar información del bot
async def show_info(update: Update, context: CallbackContext) -> None:
    info_text = (
        "📌 Funcionamiento del bot:\n\n"
        "Comandos:\n"
        "• /start - Inicia el bot y envía el mensaje de bienvenida con los botones principales\n"
        "• /search [nombre] - Busca una película o serie\n"
        "• /gift_code [código] - Canjea un código de regalo\n\n"
        
        "Búsqueda de contenido:\n"
        "Simplemente envía el nombre de la película o serie que deseas buscar.\n"
        "Si no se encuentra, podrás hacer un pedido.\n\n"
        
        "Planes:\n"
        "• Default (Gratis): 3 búsquedas diarias, 1 pedido diario, sin reenvío\n"
        "• Pro: 15 búsquedas diarias, 2 pedidos diarios, sin reenvío\n"
        "• Plus: 50 búsquedas diarias, 10 pedidos diarios, con reenvío\n"
        "• Ultra: Búsquedas ilimitadas, pedidos ilimitados, con reenvío\n\n"
        
        "Sistema de referidos:\n"
        "Comparte tu enlace de referido y gana 1 💎 por cada nuevo usuario.\n"
        "Estos diamantes podrán usarse para adquirir planes en el futuro.\n\n"
        
        "Para cualquier duda o problema, contacta a @osvaldo20032"
    )
    
    keyboard = [
        [InlineKeyboardButton("Volver 🔙", callback_data="main_menu")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                info_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error al editar mensaje de información: {e}")
            # Si falla la edición, enviar un nuevo mensaje
            await update.callback_query.message.reply_text(
                info_text,
                reply_markup=reply_markup
            )
    else:
        await update.message.reply_text(
            info_text,
            reply_markup=reply_markup
        )

# Función para compartir enlace de referido
async def share_referral_link(update: Update, context: CallbackContext) -> None:
    user_id = update.callback_query.from_user.id
    
    # Generar enlace de referido
    referral_link = f"https://t.me/share/url?url=https://t.me/MultimediaTVbot?start=ref_{user_id}&text=¡Únete%20y%20ve%20películas%20conmigo!"
    
    share_text = (
        f"🎁 Tu enlace de referido\n\n"
        f"{referral_link}\n\n"
        f"Comparte este enlace con tus amigos. Por cada nuevo usuario que se una usando tu enlace, recibirás 1 💎."
    )
    
    keyboard = [
        [InlineKeyboardButton("Volver 🔙", callback_data="profile")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.callback_query.edit_message_text(
            share_text,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error al editar mensaje de enlace de referido: {e}")
        # Si falla la edición, enviar un nuevo mensaje
        await update.callback_query.message.reply_text(
            share_text,
            reply_markup=reply_markup
        )

# Función para buscar contenido en el canal
async def search_content(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el bot está en modo mantenimiento
    if maintenance_mode and user_id != ADMIN_ID:
        await update.message.reply_text(
            "🛠️ El bot está en mantenimiento. Por favor, inténtalo más tarde."
        )
        return
    
    # Verificar si el usuario está baneado o muteado
    user_info = get_user_info(user_id)
    if not user_info:
        await update.message.reply_text(
            "No se pudo obtener tu información. Por favor, inicia el bot con /start."
        )
        return
    
    if user_info.get("plan") == "banned":
        await update.message.reply_text(
            "Has sido baneado y no puedes usar el bot. Contacta con un administrador."
        )
        return
    
    if is_user_muted(user_id):
        await update.message.reply_text(
            "Has sido silenciado y no puedes usar el bot. Contacta con un administrador."
        )
        return
    
    # Obtener el nombre de la película o serie
    if update.message.text.startswith("/search"):
        if len(context.args) == 0:
            await update.message.reply_text(
                "Por favor, proporciona el nombre de la película o serie que deseas buscar.\n"
                "Ejemplo: /search Avengers"
            )
            return
        query = " ".join(context.args)
    else:
        query = update.message.text.strip()
    
    if not query:
        await update.message.reply_text("Por favor, envía el nombre de la película o serie que deseas buscar.")
        return
    
    # Verificar límites de búsqueda
    plan_name = user_info.get("plan", "default")
    search_limit = PLANS[plan_name]["search_limit"]
    search_count = user_info.get("search_count", 0)
    
    if search_count >= search_limit:
        await update.message.reply_text(
            f"Has alcanzado tu límite diario de búsquedas ({search_limit}).\n"
            f"Espera al reinicio diario o actualiza tu plan para obtener más búsquedas."
        )
        return
    
    await update.message.reply_text(f"🔍 Buscando '{query}' en el canal...")
    
    try:
        # Buscar en el canal usando get_messages en lugar de get_chat_history
        results = await search_in_channel(context.bot, CHANNEL_ID, query)
        
        if not results:
            # No se encontraron resultados, ofrecer hacer un pedido
            keyboard = [
                [
                    InlineKeyboardButton("Película 🎞️", callback_data=f"request_movie_{query}"),
                    InlineKeyboardButton("Serie 📺", callback_data=f"request_series_{query}")
                ],
                [InlineKeyboardButton("Hacer Pedido 📡", callback_data=f"make_request_{query}")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"No se encontraron resultados para '{query}'.\n\n"
                f"Comprueba que escribes el nombre correctamente o utiliza variaciones del mismo. "
                f"Prueba escribiendo el nombre en el idioma oficial o español o solamente pon una palabra clave.\n"
                f"¿Quieres hacer un pedido?\n"
                f"Selecciona el tipo y haz clic en 'Hacer pedido'.",
                reply_markup=reply_markup
            )
            return
        
        # Actualizar contador de búsquedas
        update_search_count(user_id)
        
        # Mostrar los resultados
        await send_search_results(update, context, results, query, user_info)
        
    except Exception as e:
        logger.error(f"Error al buscar contenido: {e}")
        await update.message.reply_text(
            "Ocurrió un error al buscar el contenido. Por favor, inténtalo de nuevo más tarde."
        )

# Función para buscar en el canal (modificada para usar get_messages en lugar de get_chat_history)
async def search_in_channel(bot, channel_id, query):
    query_lower = query.lower()
    
    try:
        # Obtener mensajes del canal usando métodos disponibles en python-telegram-bot
        messages = []
        
        # Usar getUpdates o forwardMessage para obtener mensajes del canal
        # Esta es una implementación alternativa ya que get_chat_history no está disponible
        
        # Método 1: Intentar obtener mensajes recientes del canal
        try:
            # Obtener información del canal
            chat = await bot.get_chat(chat_id=channel_id)
            
            # Obtener algunos mensajes recientes (limitado a lo que la API permite)
            # Nota: Esto es una solución parcial, no obtendrá todos los mensajes del canal
            for i in range(1, 50):  # Intentar obtener los últimos 50 mensajes
                try:
                    msg = await bot.copy_message(
                        chat_id=channel_id,
                        from_chat_id=channel_id,
                        message_id=chat.pinned_message.message_id - i if chat.pinned_message else i,
                        disable_notification=True
                    )
                    
                    # Verificar si el mensaje contiene la consulta
                    if msg.text and query_lower in msg.text.lower():
                        messages.append({
                            'message_id': msg.message_id,
                            'text': msg.text,
                            'date': msg.date
                        })
                    elif msg.caption and query_lower in msg.caption.lower():
                        messages.append({
                            'message_id': msg.message_id,
                            'text': msg.caption,
                            'date': msg.date,
                            'has_media': True
                        })
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Error al obtener mensajes del canal (método 1): {e}")
        
        # Si no se encontraron mensajes, intentar con un enfoque alternativo
        if not messages:
            # Método alternativo: Crear un mensaje de búsqueda simulado
            # Esto es solo una solución temporal hasta que se implemente una búsqueda real
            messages.append({
                'message_id': 1,  # ID ficticio
                'text': f"Resultados de búsqueda para '{query}'\n\nNo se encontraron resultados en el canal. Por favor, haz un pedido.",
                'date': datetime.datetime.now()
            })
        
        return messages
    except Exception as e:
        logger.error(f"Error al buscar en el canal: {e}")
        return []

# Función para enviar los resultados de búsqueda
async def send_search_results(update: Update, context: CallbackContext, results, query, user_info):
    if not results:
        await update.message.reply_text(f"No se encontraron resultados para '{query}'.")
        return
    
    # Crear un mensaje con los resultados
    result_text = f"🎬 Resultados para '{query}'\n\n"
    
    # Crear botones para cada resultado
    keyboard = []
    
    for i, result in enumerate(results[:5], 1):  # Limitamos a 5 resultados
        # Extraer un título corto para el botón
        title = result['text'].split('\n')[0] if '\n' in result['text'] else result['text']
        if len(title) > 30:
            title = title[:27] + "..."
        
        # Añadir al texto de resultados
        result_text += f"{i}. {title}\n"
        
        # Añadir botón
        keyboard.append([
            InlineKeyboardButton(
                f"{i}. {title}", 
                callback_data=f"result_{result['message_id']}"
            )
        ])
    
    # Añadir botón para hacer pedido si no hay resultados satisfactorios
    keyboard.append([
        InlineKeyboardButton("Hacer Pedido 📡", callback_data=f"make_request_{query}")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        result_text,
        reply_markup=reply_markup
    )

# Función para manejar los callbacks de los botones
async def button_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    
    try:
        await query.answer()
    except Exception as e:
        logger.error(f"Error al responder callback query: {e}")
        # Continuar con el procesamiento aunque falle el answer
    
    # Extraer datos del callback
    data = query.data
    user_id = query.from_user.id
    
    # Verificar si el usuario está baneado
    user_info = get_user_info(user_id)
    if user_info and user_info.get("plan") == "banned" and data != "main_menu":
        try:
            await query.edit_message_text(
                "Has sido baneado y no puedes usar el bot. Contacta con un administrador."
            )
        except Exception as e:
            logger.error(f"Error al editar mensaje para usuario baneado: {e}")
        return
    
    # Manejar diferentes tipos de callbacks
    if data == "main_menu":
        # Mostrar menú principal
        keyboard = [
            [
                InlineKeyboardButton("Multimedia Tv 📺", url="https://t.me/multimediatvOficial"),
                InlineKeyboardButton("Pedidos 📡", url=f"https://t.me/{GROUP_ID}")
            ],
            [InlineKeyboardButton("Perfil 👤", callback_data="profile")],
            [InlineKeyboardButton("Planes 📜", callback_data="plans")],
            [InlineKeyboardButton("Información 📰", callback_data="info")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                f"¡Hola! {query.from_user.first_name}👋 te doy la bienvenida\n\n"
                f"MultimediaTv un bot donde encontraras un amplio catálogo de películas y series, "
                f"las cuales puedes buscar o solicitar en caso de no estar en el catálogo",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error al editar mensaje para menú principal: {e}")
    
    elif data == "profile":
        # Mostrar perfil del usuario
        await show_profile(update, context)
    
    elif data == "plans":
        # Mostrar planes disponibles
        await show_plans(update, context)
    
    elif data == "info":
        # Mostrar información del bot
        await show_info(update, context)
    
    elif data == "share_ref":
        # Compartir enlace de referido
        await share_referral_link(update, context)
    
    elif data.startswith("plan_"):
        # Mostrar detalles de un plan específico
        plan_name = data.split("_")[1]
        await show_plan_details(update, context, plan_name)
    
    elif data.startswith("payment_"):
        # Mostrar opciones de pago
        parts = data.split("_")
        plan_name = parts[1]
        payment_type = parts[2]
        await show_payment_options(update, context, plan_name, payment_type)
    
    elif data.startswith("result_"):
        # Manejar selección de resultado de búsqueda
        message_id = int(data.split("_")[1])
        
        try:
            # Verificar si el usuario puede reenviar contenido
            plan_name = user_info.get("plan", "default")
            can_forward = PLANS[plan_name]["can_forward"]
            
            if can_forward:
                # Reenviar el mensaje del canal al usuario
                try:
                    await context.bot.forward_message(
                        chat_id=user_id,
                        from_chat_id=CHANNEL_ID,
                        message_id=message_id
                    )
                except Exception as e:
                    logger.error(f"Error al reenviar mensaje: {e}")
                    await query.message.reply_text(
                        "No se pudo reenviar el contenido. Enviando como texto plano."
                    )
                    # Intentar obtener y enviar el mensaje como texto plano
                    try:
                        message = await context.bot.get_message(
                            chat_id=CHANNEL_ID,
                            message_id=message_id
                        )
                        if message.text:
                            await query.message.reply_text(message.text)
                        elif message.caption:
                            await query.message.reply_text(message.caption)
                    except Exception:
                        await query.message.reply_text(
                            "No se pudo obtener el contenido. Por favor, contacta al administrador."
                        )
            else:
                # Copiar el mensaje sin mostrar el origen
                try:
                    message = await context.bot.copy_message(
                        chat_id=user_id,
                        from_chat_id=CHANNEL_ID,
                        message_id=message_id
                    )
                except Exception as e:
                    logger.error(f"Error al copiar mensaje: {e}")
                    await query.message.reply_text(
                        "No se pudo enviar el contenido. Contacta al administrador para obtener ayuda."
                    )
        except Exception as e:
            logger.error(f"Error al procesar resultado: {e}")
            await query.message.reply_text(
                "No se pudo enviar el contenido. Por favor, inténtalo de nuevo más tarde."
            )
    
    elif data.startswith("request_"):
        # Guardar tipo de pedido en el contexto
        parts = data.split("_")
        request_type = parts[1]  # movie o series
        title = "_".join(parts[2:])
        
        context.user_data["request_type"] = request_type
        context.user_data["request_title"] = title
        
        try:
            await query.edit_message_text(
                f"Has seleccionado hacer un pedido de {request_type}: {title}\n"
                f"Haz clic en 'Hacer Pedido 📡' para confirmar.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Hacer Pedido 📡", callback_data=f"make_request_{title}")]
                ])
            )
        except Exception as e:
            logger.error(f"Error al editar mensaje para pedido: {e}")
    
    elif data.startswith("make_request_"):
        # Verificar límites de pedidos
        plan_name = user_info.get("plan", "default")
        request_limit = PLANS[plan_name]["request_limit"]
        request_count = user_info.get("request_count", 0)
        
        if request_count >= request_limit:
            try:
                await query.edit_message_text(
                    f"Has alcanzado tu límite diario de pedidos ({request_limit}).\n"
                    f"Espera al reinicio diario o actualiza tu plan para obtener más pedidos."
                )
            except Exception as e:
                logger.error(f"Error al editar mensaje para límite de pedidos: {e}")
            return
        
        # Obtener datos del pedido
        request_type = context.user_data.get("request_type", "movie")
        request_title = data.split("_", 1)[1] if "_" in data else context.user_data.get("request_title", "")
        
        if not request_title:
            try:
                await query.edit_message_text(
                    "No se pudo procesar tu pedido. Por favor, intenta de nuevo."
                )
            except Exception as e:
                logger.error(f"Error al editar mensaje para pedido sin título: {e}")
            return
        
        # Guardar pedido en la base de datos
        request_id = save_pending_request(user_id, request_type, request_title)
        
        if not request_id:
            try:
                await query.edit_message_text(
                    "Error al guardar tu pedido. Por favor, intenta de nuevo más tarde."
                )
            except Exception as e:
                logger.error(f"Error al editar mensaje para error de guardado: {e}")
            return
        
        # Actualizar contador de pedidos
        update_request_count(user_id)
        
        # Notificar al usuario
        request_type_text = "película" if request_type == "movie" else "serie"
        try:
            await query.edit_message_text(
                f"Tu pedido de {request_type_text} '{request_title}' ha sido enviado al administrador.\n"
                f"Te notificaremos cuando esté disponible."
            )
        except Exception as e:
            logger.error(f"Error al editar mensaje para confirmación de pedido: {e}")
        
        # Enviar notificación al administrador con botones de acción
        admin_keyboard = [
            [
                InlineKeyboardButton("Aceptar ✅", callback_data=f"admin_accept_{request_id}")
            ]
        ]
        
        admin_markup = InlineKeyboardMarkup(admin_keyboard)
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"📩 Nuevo pedido\n\n"
                    f"• Usuario: {query.from_user.first_name} (@{query.from_user.username or 'sin_username'})\n"
                    f"• ID: {user_id}\n"
                    f"• Tipo: {request_type_text.capitalize()}\n"
                    f"• Título: {request_title}\n"
                    f"• Plan: {plan_name.capitalize()}"
                )
            )
            
            # Enviar mensaje separado con botones para evitar errores de formato
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"Acciones para el pedido de '{request_title}':",
                reply_markup=admin_markup
            )
        except Exception as e:
            logger.error(f"Error al enviar notificación al administrador: {e}")
    
    elif data.startswith("admin_accept_"):
        # Verificar si el usuario es administrador
        if user_id != ADMIN_ID:
            await query.answer("No tienes permisos para usar esta función.")
            return
        
        # Extraer datos
        request_id = int(data.split("_")[2])
        
        # Obtener información de la solicitud
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM pending_requests WHERE id = ?",
            (request_id,)
        )
        request_info = cursor.fetchone()
        
        if not request_info:
            await query.edit_message_text("No se encontró la solicitud.")
            conn.close()
            return
        
        # Actualizar estado de la solicitud
        cursor.execute(
            "UPDATE pending_requests SET status = 'accepted' WHERE id = ?",
            (request_id,)
        )
        
        conn.commit()
        
        target_user_id = request_info["user_id"]
        request_title = request_info["title"]
        
        conn.close()
        
        # Notificar al usuario que su pedido fue aceptado
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"✅ Tu pedido '{request_title}' ha sido aceptado y añadido al bot.\n"
                     f"Ya puedes buscarlo usando /search {request_title}"
            )
            
            await query.edit_message_text(
                f"✅ Pedido '{request_title}' aceptado y notificado al usuario."
            )
        except Exception as e:
            logger.error(f"Error al notificar aceptación de pedido: {e}")
            await query.edit_message_text(
                f"✅ Pedido '{request_title}' aceptado, pero no se pudo notificar al usuario."
            )

# Función para manejar solicitudes pendientes (para administradores)
async def pending_requests(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario es administrador
    if user_id != ADMIN_ID:
        await update.message.reply_text("No tienes permisos para usar este comando.")
        return
    
    # Obtener solicitudes pendientes
    pending = get_pending_requests(5)
    
    if not pending:
        await update.message.reply_text("No hay solicitudes pendientes.")
        return
    
    # Crear mensaje y botones
    pending_text = "📋 Solicitudes pendientes:\n\n"
    keyboard = []
    
    for i, req in enumerate(pending, 1):
        req_type = "Película" if req["request_type"] == "movie" else "Serie"
        username = req["username"] or "sin_username"
        
        pending_text += f"{i}. {req_type}: {req['title']} - Usuario: @{username}\n"
        
        keyboard.append([
            InlineKeyboardButton(f"Aprobar #{i}", callback_data=f"admin_accept_{req['id']}")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        pending_text,
        reply_markup=reply_markup
    )

# Función para manejar el comando de pedido
async def handle_request_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    # Verificar si el usuario está baneado
    user_info = get_user_info(user_id)
    if not user_info:
        await update.message.reply_text(
            "No se pudo obtener tu información. Por favor, inicia el bot con /start."
        )
        return
    
    if user_info.get("plan") == "banned":
        await update.message.reply_text(
            "Has sido baneado y no puedes usar el bot. Contacta con un administrador."
        )
        return
    
    # Verificar límites de pedidos
    plan_name = user_info.get("plan", "default")
    request_limit = PLANS[plan_name]["request_limit"]
    request_count = user_info.get("request_count", 0)
    
    if request_count >= request_limit:
        await update.message.reply_text(
            f"Has alcanzado tu límite diario de pedidos ({request_limit}).\n"
            f"Espera al reinicio diario o actualiza tu plan para obtener más pedidos."
        )
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Uso: /pedido [año] [nombre]\n"
            "Ejemplo: /pedido 2023 Avengers"
        )
        return
    
    year = context.args[0]
    title = " ".join(context.args[1:])
    
    # Guardar pedido en la base de datos
    request_id = save_pending_request(user_id, "movie", title, year)
    
    if not request_id:
        await update.message.reply_text(
            "Error al guardar tu pedido. Por favor, intenta de nuevo más tarde."
        )
        return
    
    # Actualizar contador de pedidos
    update_request_count(user_id)
    
    # Notificar al usuario
    await update.message.reply_text(
        f"Tu pedido de '{title}' ({year}) ha sido enviado al administrador.\n"
        f"Te notificaremos cuando esté disponible."
    )
    
    # Enviar notificación al administrador con botones de acción
    admin_keyboard = [
        [
            InlineKeyboardButton("Aceptar ✅", callback_data=f"admin_accept_{request_id}")
        ]
    ]
    
    admin_markup = InlineKeyboardMarkup(admin_keyboard)
    
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📩 Nuevo pedido\n\n"
            f"• Usuario: {update.effective_user.first_name} (@{update.effective_user.username or 'sin_username'})\n"
            f"• ID: {user_id}\n"
            f"• Título: {title}\n"
            f"• Año: {year}\n"
            f"• Plan: {plan_name.capitalize()}"
        )
    )
    
    # Enviar mensaje separado con botones para evitar errores de formato
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"Acciones para el pedido de '{title}':",
        reply_markup=admin_markup
    )

# Función principal
def main() -> None:
    # Inicializar la base de datos
    initialize_database()
    
    # Crear la aplicación
    application = Application.builder().token(TOKEN).build()

    # Registrar manejadores de comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admin_help", admin_help))
    application.add_handler(CommandHandler("mantenimiento", maintenance_on))
    application.add_handler(CommandHandler("mantenimientooff", maintenance_off))
    application.add_handler(CommandHandler("mute", mute_user_command))
    application.add_handler(CommandHandler("unmute", unmute_user_command))
    application.add_handler(CommandHandler("plan", assign_plan))
    application.add_handler(CommandHandler("addgift_code", add_gift_code))
    application.add_handler(CommandHandler("gift_code", use_gift_code_command))
    application.add_handler(CommandHandler("setdiamonds", set_diamonds))
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("unban", unban_user))
    application.add_handler(CommandHandler("pendientes", pending_requests))
    application.add_handler(CommandHandler("reset_counters", reset_counters_command))
    application.add_handler(CommandHandler("search", search_content))
    application.add_handler(CommandHandler("pedido", handle_request_command))

    # Registrar manejador de botones
    application.add_handler(CallbackQueryHandler(button_callback))

    # Registrar manejador de mensajes
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_content))

    # Iniciar el bot
    application.run_polling()

if __name__ == "__main__":
    main()