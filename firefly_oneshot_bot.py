#!/usr/bin/env python
# pylint: disable=unused-argument
# This program is derived from
# https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/echobot.py

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
9€ cheese, dest=Wochenmarkt
12 coffe, cat=food outside

The text from "cat=" or "dest=" to the next comma is used to find the category
or destination account. You can create a new category or destination account
by prefixing the name with "+" (e.g. "cat=+Food").

The script is expected to be run inside a Docker container with parameters
being passed to the script via environment variables:

* FIREFLY_URL
* FIREFLY_TOKEN
* FIREFLY_SOURCE_ACCOUNT
* TELEGRAM_BOT_TOKEN
* TELEGRAM_ALLOW_USERID

"""

import datetime
import functools
import json
import logging
import os
from urllib.parse import urljoin
import requests

from thefuzz import process
from telegram import ForceReply, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
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
    logger.info("Reading from '%s'.", url)
    if "api/v1" not in url:
        url = urljoin(args["firefly_url"] + "/", "api/v1/" + url)
    r = requests.request(
        method=method,
        url=url,
        headers={
            "Authorization": "Bearer " + args["firefly_app_token"],
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/json",
        },
        json=post_data,
        timeout=30,
    )
    if method == "DELETE":
        logger.debug("Data: %s", r.text)
        r.raise_for_status()
        return None
    if method != "GET":
        logger.debug("Data: %s", r.text)
        r.raise_for_status()

    data = r.json()
    if "data" not in data:
        logger.error("Bad data: %s", data)
    real_data = data["data"]
    if first or method != "GET":
        return real_data
    if "links" in data and data["links"]["self"] != data["links"]["last"]:
        logger.info("Next page is there!")
        real_data.extend(
            _get_data_from_request(data["links"]["next"]))
    return real_data


def _find_account_id(account_name):
    """Find the account ID for a given account name."""
    data = _get_data_from_request("accounts/?type=asset")
    for account in data:
        account_id = account["id"]
        if account["attributes"]["name"] == account_name:
            return account_id
    raise RuntimeError(f"Base account with name {account_name} not found")


@functools.lru_cache(maxsize=1)
def _get_expense_accounts_data():
    """Get the list of expense accounts."""
    return _get_data_from_request("accounts/?type=expense")


def _find_dest_account(part):
    """Find a destination account by partial name."""
    if part:
        part = part.strip()
        if part.startswith("+"):
            name = part[1:].strip()
            if not name:
                logger.warning("Cannot create account with empty name.")
                return None, None
            try:
                data = _get_data_from_request(
                    "accounts", method="POST", post_data={"name": name, "type": "expense"}
                )
                _get_expense_accounts_data.cache_clear()
                return data["id"], data["attributes"]["name"]
            except requests.exceptions.RequestException as e:
                logger.error("Failed to create account '%s': %s", name, e)
                return None, None

        data = _get_expense_accounts_data()
        accounts = {
            a["attributes"]["name"]: a["id"]
            for a in data
        }
        name, ratio = process.extractOne(part, accounts.keys())
        if ratio < 60:
            logger.warning("Match too bad, should make a new account")
        return accounts[name], name
    return None, None


@functools.lru_cache(maxsize=1)
def _get_categories_data():
    """Get the list of categories."""
    return _get_data_from_request("categories/")


def _find_category(part):
    """Find a category by partial name."""
    if part:
        part = part.strip()
        if part.startswith("+"):
            name = part[1:].strip()
            if not name:
                logger.warning("Cannot create category with empty name.")
                return None, None
            try:
                data = _get_data_from_request("categories", method="POST", post_data={"name": name})
                _get_categories_data.cache_clear()
                return data["id"], data["attributes"]["name"]
            except requests.exceptions.RequestException as e:
                logger.error("Failed to create category '%s': %s", name, e)
                return None, None

        data = _get_categories_data()
        categories = {
            a["attributes"]["name"]: a["id"]
            for a in data
        }
        name, ratio = process.extractOne(part, categories.keys())
        if ratio < 60:
            logger.warning("Match too bad, should make a new category")
        return categories[name], name
    return None, None


async def restrict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restrict access to the bot."""
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
    """Get the last transaction details."""
    end = datetime.date.today()
    start_date = end - datetime.timedelta(days=365)
    end = end.isoformat()
    start_date = start_date.isoformat()
    data = _get_data_from_request(
        f"accounts/{args['account_id']}/transactions/"
        f"?type=withdrawal&limit=1&start={start_date}&end={end}",
        first=True
    )
    transaction = data[0]
    trans_id = transaction["id"]
    split = transaction["attributes"]["transactions"][0]
    msg = (
        f"{float(split['amount']):.2f} {split['currency_symbol']} {split['description']}, "
        f"dest={split['destination_name']}, cat={split['category_name']}, id={trans_id}"
    )
    return msg, trans_id


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mark the last added transaction as deleted"""
    msg, trans_id = _get_last_transaction()
    _get_data_from_request(f"transactions/{trans_id}/", method="DELETE")
    await update.message.reply_text("Deleted: " + msg)


async def last_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user what the last added transaction looks like"""
    msg, _ = _get_last_transaction()
    await update.message.reply_text(msg)


async def cat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Find a category by partial name."""
    _, name = _find_category(" ".join(context.args))
    await update.message.reply_text(name)


async def dest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Find a destination account by partial name."""
    _, name = _find_dest_account(" ".join(context.args))
    await update.message.reply_text(name)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add the transaction"""
    msg = update.message.text
    msg = msg.strip()
    dest_id = None
    cat_id = None
    desc = ""
    for part in msg.split(","):
        part = part.strip()
        if part.startswith("dest="):
            part = part[len("dest="):]
            dest_id, _ = _find_dest_account(part)
        elif part.startswith("cat="):
            part = part[len("cat="):]
            cat_id, _ = _find_category(part)
        else:
            if desc:
                desc += ", "
            desc += part

    try:
        parts = desc.split(maxsplit=1)
        if not parts:
            raise ValueError("Empty message")
        if len(parts) == 1:
            amount = float(parts[0])
            desc = "Unknown"
        else:
            amount = float(parts[0])
            desc = parts[1]
    except ValueError:
        await update.message.reply_text("Could not parse message. Expected: <amount> [description]")
        return

    if not dest_id:
        dest_id, dest = _find_dest_account("Unknown")
        if not dest_id:
            await update.message.reply_text("Could not identify destination account.")
            return

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
