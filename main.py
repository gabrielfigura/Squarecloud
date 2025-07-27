import requests
import json
import logging
import os
from telegram.ext import Application, CommandHandler
from datetime import datetime, timezone

# Configuração de logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurações
API_URL = "https://api.casinoscores.com/svc-evolution-game-events/api/bacbo/latest"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7703975421:AAG-CG5Who2xs4NlevJqB5TNvjjzeUEDz8o")
CHAT_ID = "-1002859771274"
CHECK_INTERVAL = 5
PATTERNS_FILE = "patterns.json"

# Carregar padrões
def load_patterns():
    try:
        with open(PATTERNS_FILE, 'r') as f:
            patterns = json.load(f)
        for pattern in patterns:
            if not all(emoji in ["🔴", "🔵", "🟡"] for emoji in pattern['sequencia']):
                logger.error(f"Padrão inválido detectado: {pattern['id']}")
                raise ValueError(f"Padrão inválido: {pattern['id']}")
        return patterns
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Erro ao carregar padrões: {e}")
        raise

PATTERNS = load_patterns()

# Estado
last_game_id = None
current_streak = 0
last_message_id = None
gale_active = False
last_bet = None
last_pattern_id = None

async def fetch_latest_game():
    """Busca os dados mais recentes da API do CasinoScores."""
    try:
        response = requests.get(API_URL, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data['id'] == last_game_id:
            return None
        return data
    except requests.RequestException as e:
        logger.error(f"Erro ao buscar dados da API: {e}")
        return None

def load_game_history():
    """Carrega o histórico de jogos."""
    try:
        with open('game_history.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_game_history(history):
    """Salva o histórico de jogos."""
    history = history[-100:]  # Limita a 100 rodadas
    try:
        with open('game_history.json', 'w') as f:
            json.dump(history, f)
    except IOError as e:
        logger.error(f"Erro ao salvar histórico: {e}")

def map_outcome_to_emoji(outcome):
    """Mapeia o resultado do jogo para emoji."""
    if outcome == "BankerWon":
        return "🔴"
    elif outcome == "PlayerWon":
        return "🔵"
    elif outcome == "Tie":
        return "🟡"
    return None

def check_pattern(history):
    """Verifica se algum padrão foi detectado no histórico."""
    history_emojis = [map_outcome_to_emoji(game['data']['result']['outcome']) for game in history][-10:]
    matched_patterns = []
    for pattern in PATTERNS:
        pattern_seq = pattern['sequencia']
        if len(history_emojis) >= len(pattern_seq) and history_emojis[-len(pattern_seq):] == pattern_seq:
            matched_patterns.append((pattern, len(pattern_seq)))
    if matched_patterns:
        return max(matched_patterns, key=lambda x: x[1])[0]  # Escolhe o padrão mais longo
    return None

def determine_bet(pattern):
    """Determina a aposta com base na ação do padrão."""
    action = pattern['acao']
    last_result = pattern['sequencia'][-1]
    
    if action == "Entrar a favor":
        return "Banker" if last_result == "🔴" else "Player"
    elif action == "Entrar no oposto do último":
        return "Player" if last_result == "🔴" else "Banker"
    elif action == "Entrar contra":
        return "Player" if last_result == "🔴" else "Banker"
    elif action == "Entrar no lado que inicia":
        return "Banker" if pattern['sequencia'][0] == "🔴" else "Player"
    elif action == "Seguir rompimento":
        return "Player" if last_result == "🔵" else "Banker"
    elif action == "Seguir alternância":
        return "Player" if last_result == "🔴" else "Banker"
    elif action == "Seguir nova cor":
        return "Player" if last_result == "🔵" else "Banker"
    elif action == "Seguir 🔴":
        return "Banker"
    elif action == "Seguir 🔵":
        return "Player"
    elif action == "Ignorar Tie e seguir 🔴":
        return "Banker"
    elif action == "Voltar para 🔵":
        return "Player"
    elif action == "Seguir pares":
        return "Banker" if pattern['sequencia'][-2] == "🔴" else "Player"
    elif action == "Seguir ciclo":
        return "Banker" if pattern['sequencia'][0] == "🔴" else "Player"
    elif action == "Novo início":
        return "Player" if pattern['sequencia'][0] == "🔵" else "Banker"
    elif action == "Seguir padrão 2x":
        return "Banker" if pattern['sequencia'][-2] == "🔴" else "Player"
    return None

async def send_signal(context, pattern, bet):
    """Envia o sinal de aposta no Telegram."""
    global last_message_id, last_bet, last_pattern_id
    bet_emoji = "🔴" if bet == "Banker" else "🔵"
    message = (
        f"ATENÇÃO PADRÃO {pattern['id']} DETECTADO\n"
        f"Entrar no {bet}: {bet_emoji}\n"
        "Proteger o empate: 🟡\n"
        "Fazer até 1 gale 🔥\n"
        "Mais dinheiro e menos amigos 🤏"
    )
    if last_message_id:
        try:
            await context.bot.delete_message(chat_id=CHAT_ID, message_id=last_message_id)
        except telegram.error.TelegramError as e:
            logger.error(f"Erro ao deletar mensagem: {e}")
    sent_message = await context.bot.send_message(chat_id=CHAT_ID, text=message)
    last_bet = bet
    last_pattern_id = pattern['id']
    last_message_id = sent_message.message_id

async def validate_bet(context, game_data):
    """Valida o resultado da aposta."""
    global current_streak, gale_active, last_bet, last_pattern_id
    outcome = game_data['data']['result']['outcome']
    bet_won = (
        (last_bet == "Banker" and outcome == "BankerWon") or
        (last_bet == "Player" and outcome == "PlayerWon") or
        outcome == "Tie"
    )
    
    if bet_won:
        current_streak += 1
        message = "Mais Dinheiro no bolso🤌\n"
        message += f"Placar de acertos: {current_streak} ✅"
        gale_active = False
    else:
        if not gale_active:
            gale_active = True
            message = "Vamos entrar no 1 Gale🔥"
        else:
            message = "Perdemos no 1 Gale😔, vamos pegar a outra rodada🤌"
            current_streak = 0
            gale_active = False
    
    await context.bot.send_message(chat_id=CHAT_ID, text=message)
    last_bet = None
    last_pattern_id = None

async def monitor_table(context):
    """Monitora a mesa e envia sinais quando necessário."""
    global last_game_id, last_message_id
    game_data = await fetch_latest_game()
    if not game_data:
        return
    
    history = load_game_history()
    history.append(game_data)
    save_game_history(history)
    
    pattern = check_pattern(history)
    if pattern:
        bet = determine_bet(pattern)
        if bet:
            started_at = datetime.strptime(game_data['data']['startedAt'], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            time_diff = (now - started_at).total_seconds()
            if time_diff < 20:
                await send_signal(context, pattern, bet)
            else:
                logger.warning(f"Sinal não enviado: tempo restante insuficiente ({time_diff}s)")
    
    if last_bet and game_data['data']['status'] == "Resolved":
        await validate_bet(context, game_data)
    
    last_game_id = game_data['id']
    
    if not last_bet and not last_message_id:
        message = "MONITORANDO A MESA🤌"
        sent_message = await context.bot.send_message(chat_id=CHAT_ID, text=message)
        last_message_id = sent_message.message_id

async def start(update, context):
    """Comando /start para iniciar o bot."""
    await update.message.reply_text("Bot de monitoramento de Bac Bo iniciado! 🤌")
    context.job_queue.run_repeating(monitor_table, interval=CHECK_INTERVAL, first=0)

async def check_permissions(update, context):
    """Verifica permissões do bot no chat."""
    try:
        bot_member = await context.bot.get_chat_member(CHAT_ID, context.bot.id)
        if bot_member.can_delete_messages and bot_member.can_post_messages:
            await update.message.reply_text("Bot tem permissões necessárias (enviar e deletar mensagens). ✅")
        else:
            await update.message.reply_text("Bot não tem permissões suficientes. Verifique se é administrador com permissões para enviar e deletar mensagens. ⚠️")
    except telegram.error.TelegramError as e:
        await update.message.reply_text(f"Erro ao verificar permissões: {e}")

async def main():
    """Função principal do bot."""
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check_permissions", check_permissions))
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
