#!/usr/bin/env python
# pylint: disable=unused-argument
# This program is derrived from https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/echobot.py

"""
One-shot transaction adding Telegram bot for Firefly3

The purpose of the bot is to allow adding new (cash) transactions from
anywhere to your own instance of Firefly3 finance planning application.

Once configuring and running the authentificated user can add a new cash
withdrawal by just sending one message to the bot. The main data points
are amount and description. In addition the destination account and/or
category can be specified with partial name by wildcard match.

Example usage:

23
34.21
12€
23.12 coffee
9€ cheese, acc=Wochenmarkt
12 coffe, cat=food outside

The script is expected to be run inside a Docker container with parameters
being passed to the script via environment variables:

* FIREFLY_URL
* FIREFLY_TOKEN
* FIREFLY_SOURCE_ACCOUNT
* TELEGRAM_BOT_TOKEN
* TELEGRAM_ALLOW_USERID

"""

import datetime
import json
import logging
import os
import requests

from thefuzz import process
from telegram import ForceReply, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.DEBUG
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.INFO)
logging.getLogger("telegram").setLevel(logging.INFO)

logger = logging.getLogger(__name__)

args = {
    "firefly_url": os.environ["FIREFLY_URL"].rstrip("/"),
    "firefly_app_token": os.environ["FIREFLY_TOKEN"],
    "account_id": os.environ["FIREFLY_SOURCE_ACCOUNT"],
    "account_name": os.environ["FIREFLY_SOURCE_ACCOUNT"],
}

# Helper functions


def _get_data_from_request(url, first=False, method="GET", post_data=None):
    logger.info(f"Reading from '{url}'.")
    if "api/v1" not in url:
        url = "/".join([args["firefly_url"], "api/v1", url])
    r = requests.request(
        method=method,
        url=url,
        headers={
            "Authorization": "Bearer " + args["firefly_app_token"],
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/json",
        },
        json=post_data,
    )
    if method != "GET":
        logger.info("Data: %s", r.text)
        r.raise_for_status()
        return
    data = r.json()
    if "data" not in data:
        logger.error("Bad data: %s", data)
    real_data = data["data"]
    if first:
        return real_data
    if "links" in data and data["links"]["self"] != data["links"]["last"]:
        logger.info("Next page is there!")
        real_data.extend(
            _get_data_from_request(data["links"]["next"]))
    return real_data


def _find_account_id(account_name):

    t = _get_data_from_request("accounts/?type=asset")
    for account in t:
        account_id = account["id"]
        if account["attributes"]["name"] == account_name:
            return account_id
    raise RuntimeError(f"Base account with name {account_name} not found")


def _find_dest_account(part):
    if part:
        t = _get_data_from_request("accounts/?type=expense")
        accounts = {
            a["attributes"]["name"]: a["id"]
            for a in t
        }
        name, ratio = process.extractOne(part, accounts.keys())
        if ratio < 60:
            logger.warning("Match too bad, should make a new account")
        return accounts[name], name
        # TODO create an account if asked (prefix "+")
    return 9, "Unknown"


def _find_category(part):
    if part:
        t = _get_data_from_request("categories/")
        categories = {
            a["attributes"]["name"]: a["id"]
            for a in t
        }
        name, ratio = process.extractOne(part, categories.keys())
        if ratio < 60:
            logger.warning("Match too bad, should make a new category")
        return categories[name], name
        # TODO create a category if asked (prefix "+")
    return 3, "Entertainment - food outside"


async def restrict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Access denied")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        rf"Hi {user.mention_html()}!",
        reply_markup=ForceReply(selective=True),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text("""
Add a new transaction by giving amount and description.
Use /undo to delete /last transaction.
Check categories with /cat and destination accounts with /dest
    """)


def _get_last_transaction():
    end = datetime.date.today()
    start = end - datetime.timedelta(days=365)
    end = end.isoformat()
    start = start.isoformat()
    t = _get_data_from_request(
        f"accounts/{args['account_id']}/transactions/?type=withdrawal&limit=1&start={start}&end={end}", first=True)
    t = t[0]
    id = t["id"]
    t = t["attributes"]["transactions"][0]
    msg = "{:.2f} {} {}, dest={}, cat={}, id={}".format(
        float(t["amount"]),
        t["currency_symbol"],
        t["description"],
        t["destination_name"],
        t["category_name"],
        id,
    )
    return msg, id


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mark the last added transaction as deleted"""
    msg, id = _get_last_transaction()
    _get_data_from_request(f"transactions/{id}/", method="DELETE")
    await update.message.reply_text("Deleted: " + msg)


async def last_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user what the last added transaction looks like"""
    msg, _ = _get_last_transaction()
    await update.message.reply_text(msg)


async def cat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user what the last added transaction looks like"""
    id, name = _find_category(" ".join(context.args))
    await update.message.reply_text(name)


async def dest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user what the last added transaction looks like"""
    id, name = _find_dest_account(" ".join(context.args))
    await update.message.reply_text(name)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add the transaction"""
    msg = update.message.text
    msg = msg.strip()
    dest = None
    dest_id = None
    cat = None
    cat_id = None
    desc = ""
    for part in msg.split(","):
        part = part.strip()
        if part.startswith("dest="):
            part = part[len("dest="):]
            dest_id, dest = _find_dest_account(part)
        elif part.startswith("cat="):
            part = part[len("cat="):]
            cat_id, cat = _find_category(part)
        else:
            if desc:
                desc += ", "
            desc += part
    amount, desc = desc.split(maxsplit=1)
    amount = float(amount)
    if not dest_id:
        dest_id, dest = _find_dest_account("unknown")
    post_data = {
        "apply_rules": True,
        "transactions": [{
            "type": "withdrawal",
            "date": datetime.datetime.now().isoformat("T"),
            "amount": f"{amount:.2f}",
            "description": desc,
            "source_id": args["account_id"],
            "notes": "Added via Telegram",
            "destination_id": dest_id,
        }],
    }
    if cat_id:
        post_data["transactions"][0]["category_id"] = cat_id
    _get_data_from_request("transactions", method="POST", post_data=post_data)
    await update.message.reply_text(json.dumps(post_data))


def main() -> None:
    """Start the bot."""
    args["account_id"] = _find_account_id(args["account_name"])
    logger.info("Dest account: %s", _find_dest_account("Wochen"))
    logger.info("Category: %s", _find_category("medical"))

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(
        os.environ["TELEGRAM_BOT_TOKEN"]).build()

    # Restrict bot to the specified user_id
    restrict_handler = MessageHandler(~ filters.User(
        int(os.environ["TELEGRAM_ALLOW_USERID"])), restrict)
    application.add_handler(restrict_handler)

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(CommandHandler("undo", undo_command))
    application.add_handler(CommandHandler("last", last_command))

    application.add_handler(CommandHandler(
        "dest", dest_command, has_args=True))
    application.add_handler(CommandHandler("cat", cat_command, has_args=True))

    # on non command i.e message - parse a new transaction out
    # of the message
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, add))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
