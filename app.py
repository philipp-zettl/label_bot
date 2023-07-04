#!/usr/bin/env python

import os
import logging
import csv
from copy import deepcopy
from tempfile import NamedTemporaryFile
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler
from telegram.ext.filters import Regex, Document
from dotenv import load_dotenv


load_dotenv()
NULL_STATE = 0
CONV_STATE = 1
LABEL_STATE = 2
SAMPLE_UPLOAD_STATE = 3
LABEL_SET_CATEGORIES_STATE = 4
LABEL_SAMPLE_STATE = 5

STATE = dict()


def with_state(method):
    def inner_method(update, **kwargs):
        return method(update, **kwargs, state=STATE[update.effective_chat.id])

    return inner_method


async def send_and_switch_state(update, msg, state, inline_keyboard=None):
    global STATE

    if update.message is not None:
        await update.message.reply_text(msg, reply_markup=inline_keyboard)
    else:
        await update.effective_chat.send_message(msg, reply_markup=inline_keyboard)
    STATE[update.effective_chat.id]['state'] = state


async def conv_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please provide a set of labels with the start command.")
        return

    global STATE
    if update.effective_chat.id not in STATE:
        STATE[update.effective_chat.id] = dict(data=[])

    STATE[update.effective_chat.id]['labels'] = deepcopy(context.args)
    STATE[update.effective_chat.id]['keyboard'] = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(state, callback_data=state)
            for state in context.args
        ]
    ])
    await send_and_switch_state(
        update, 
        'Welcome, ', 
        CONV_STATE
    )


async def label_list(update, context):
    await send_and_switch_state(
        update,
        'Now, please upload your samples as a single column TSV',
        SAMPLE_UPLOAD_STATE
    )


async def upload_list(update, context):
    global STATE
    with NamedTemporaryFile() as tmpfile:
        with open(tmpfile.name, 'wb') as f:
            file = await context.bot.get_file(update.message.document)
            content = await file.download_to_memory(out=f)
        with open(tmpfile.name, 'r') as f:
            spamreader = csv.reader(f, delimiter='\t')
            samples = []
            # simple for now
            for row in spamreader:
                samples.append(row[0])

    STATE[update.effective_chat.id]['samples'] = samples
    await send_and_switch_state(
        update,
        'File uploaded. Provide your categories...',
        LABEL_SET_CATEGORIES_STATE
    )

async def set_categories(update, context):
    global STATE
    labels = update.message.text.split(' ')
    STATE[update.effective_chat.id]['labels'] = labels
    STATE[update.effective_chat.id]['keyboard'] = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(state, callback_data=state)
            for state in labels
        ]
    ])
    await send_and_switch_state(
        update,
        'Categories set. We begin shortly...',
        LABEL_SAMPLE_STATE
    )

    await prompt_sample(update, context)


async def prompt_sample(update, context):
    if 'current_sample_index' not in STATE[update.effective_chat.id]:
        STATE[update.effective_chat.id]['current_sample_index'] = 0

    current_sample = STATE[update.effective_chat.id]['samples'][STATE[update.effective_chat.id]["current_sample_index"]] 
    STATE[update.effective_chat.id]['current_sample'] = current_sample
    await send_and_switch_state(update, f'"{current_sample}": ', LABEL_SAMPLE_STATE, STATE[update.effective_chat.id]['keyboard'])


async def start(update, context):
    global STATE
    if update.effective_chat.id not in STATE:
        STATE[update.effective_chat.id] = dict(data=[])

    await help_command(update, context)


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


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and updates the message text."""
    global STATE
    query = update.callback_query

    # CallbackQueries need to be answered, even if no notification to the user is needed
    # Some clients may have trouble otherwise. See https://core.telegram.org/bots/api#callbackquery
    await query.answer()
    sample = STATE[update.effective_chat.id]['current_sample']
    STATE[update.effective_chat.id]['current_sample'] = ''

    if STATE[update.effective_chat.id]['state'] == LABEL_SAMPLE_STATE:
        await query.edit_message_text(text=f'{query.message.text} {query.data}')
        STATE[update.effective_chat.id]['current_sample_index'] += 1
        if STATE[update.effective_chat.id]['current_sample_index'] >= len(STATE[update.effective_chat.id]['samples']):
            await send_and_switch_state(update, 'All samples labeled.', NULL_STATE)
        else:
            await prompt_sample(update, context)
    elif STATE[update.effective_chat.id]['state'] == CONV_STATE:
        await query.edit_message_text(text=f"Selected option: {query.data}")

        STATE[update.effective_chat.id]['state'] = CONV_STATE
    STATE[update.effective_chat.id]['data'].append([sample, query.data])


async def process(update, context):
    global STATE
    if update.effective_chat.id not in STATE or STATE[update.effective_chat.id].get('state') == NULL_STATE:
        return

    if STATE[update.effective_chat.id].get('state') == LABEL_SET_CATEGORIES_STATE:
        await set_categories(update, context)
    elif STATE[update.effective_chat.id].get('state') == LABEL_SAMPLE_STATE:
        await prompt_sample(update, context)
    elif STATE[update.effective_chat.id].get('state') == CONV_STATE:
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
    await update.message.reply_text("All clear.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to use the bot."""

    help_text = """Help:
        /start: Register yourself
        /label_conv [List of categories]: Start labeling process, requires space separated list of categories
        /label_list: Label a list of TSV
        /end: stops labeling process
        /export: exports labelled data as TSV
        /clear: removes any state for the current user
        /help: displays this help
    """
    await update.message.reply_text(help_text)


if __name__ == '__main__':
    application = ApplicationBuilder().token(os.environ.get('TG_TOKEN')).build()

    handlers = [
        CommandHandler('start', start),
        CallbackQueryHandler(category_callback),
        CommandHandler('label_conv', conv_label),
        CommandHandler('label_list', label_list),
        MessageHandler(Document.TEXT, upload_list),
        CommandHandler('end', end),
        CommandHandler('clear', clear),
        CommandHandler('export', export),
        CommandHandler('help', help_command),
        MessageHandler(Regex(r'.*'), process),
    ]
    application.add_handlers(handlers)
    application.run_polling()

