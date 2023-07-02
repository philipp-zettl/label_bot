#!/usr/bin/env python

import os
import logging
from copy import deepcopy
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler
from telegram.ext.filters import Regex
from dotenv import load_dotenv


load_dotenv()
NULL_STATE = 0
CONV_STATE = 1
LABEL_STATE = 2

STATE = dict()


async def send_and_switch_state(update, msg, state, inline_keyboard=None):
    global STATE

    await update.message.reply_text(msg, reply_markup=inline_keyboard)
    STATE[update.effective_chat.id]['state'] = state


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global STATE
    if update.effective_chat.id not in STATE:
        STATE[update.effective_chat.id] = dict(data=[])
    if not context.args:
        await update.message.reply_text("Please provide a set of labels with the start command.")
        return

    await send_and_switch_state(
        update, 
        'Welcome, ', 
        CONV_STATE
    )
    STATE[update.effective_chat.id]['labels'] = deepcopy(context.args)
    STATE[update.effective_chat.id]['keyboard'] = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(state, callback_data=state)
            for state in context.args
        ]
    ])


async def end(update, context):
    await send_and_switch_state(
        update,
        'Conversation closed. Use /export to get the collected data set.',
        NULL_STATE
    )


async def export(update, context):
    global STATE
    if not STATE[update.effective_chat.id]['data']:
        return await send_and_switch_state(
            update,
            'Nothing to export. Start conversation first using /start!',
            NULL_STATE
        )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text='Here\'s your export, have fun!'
    )

    with open('export.csv', 'w') as f:
        f.write('\n'.join(list(map(lambda x: '\t'.join(x), STATE[update.effective_chat.id]['data']))))

    STATE[update.effective_chat.id]['data'] = []

    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document='export.csv'
    )

    os.remove('export.csv')


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and updates the message text."""
    global STATE
    query = update.callback_query

    # CallbackQueries need to be answered, even if no notification to the user is needed
    # Some clients may have trouble otherwise. See https://core.telegram.org/bots/api#callbackquery
    await query.answer()

    await query.edit_message_text(text=f"Selected option: {query.data}")

    STATE[update.effective_chat.id]['data'].append([STATE[update.effective_chat.id]['current_sample'], query.data])
    STATE[update.effective_chat.id]['current_sample'] = ''
    STATE[update.effective_chat.id]['state'] = CONV_STATE


async def process(update, context):
    global STATE
    if update.effective_chat.id not in STATE or STATE[update.effective_chat.id].get('state') == NULL_STATE:
        return

    if STATE[update.effective_chat.id].get('state') == CONV_STATE:
        STATE[update.effective_chat.id]['current_sample'] = update.message.text
        await send_and_switch_state(update, 'Chose a label:', LABEL_STATE, STATE[update.effective_chat.id]['keyboard'])

    else:
        label = update.message.text

        if label not in STATE[update.effective_chat.id]['labels']:
            await update.message.reply_text(f'False label, select one of {STATE[update.effective_chat.id]["labels"]}')
            return

        STATE[update.effective_chat.id]['data'].append([STATE[update.effective_chat.id]['current_sample'], update.message.text])
        STATE[update.effective_chat.id]['current_sample'] = ''
        await send_and_switch_state(update, 'Done. Next sample...', CONV_STATE)


def clear_state(user_id):
    global STATE

    STATE[user_id] = {
        'data': [],
        'state': NULL_STATE,
        'current_sample': ''
    }
 

async def clear(update, context):
    if update.effective_chat.id not in STATE:
        await update.message.reply_text("Nothing to clear.")
        return
    clear_state(update.effective_chat.id)



async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to use the bot."""

    help_text = """Help:
        /start [List of categories]: Start labeling process, requires space separated list of categories
        /end: stops labeling process
        /export: exports labelled data as TSV
        /clear: removes any state for a user
        /help: displays this help
    """
    await update.message.reply_text(help_text)


if __name__ == '__main__':
    application = ApplicationBuilder().token(os.environ.get('TG_TOKEN')).build()

    handlers = [
        CommandHandler('start', start),
        CallbackQueryHandler(button),
        CommandHandler('end', end),
        CommandHandler('clear', clear),
        CommandHandler('export', export),
        CommandHandler('help', help_command),
        MessageHandler(Regex(r'.*'), process),
    ]
    application.add_handlers(handlers)
    application.run_polling()

